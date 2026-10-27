"""Copy dashboard report tables from destination MySQL into DuckDB."""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import duckdb

from etl_top_selling_skus import connect_with_retry, config

ROOT = Path(__file__).resolve().parent
CHUNK_SIZE = 10000
LOG = logging.getLogger("export_dashboard_to_duckdb")


def quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def duckdb_type(mysql_type: str) -> str:
    """Map report-table MySQL column types to DuckDB types."""
    kind = mysql_type.lower().split("(", 1)[0].split()[0]
    unsigned = "unsigned" in mysql_type.lower()
    if kind == "tinyint":
        return "UTINYINT" if unsigned else "TINYINT"
    if kind == "smallint":
        return "USMALLINT" if unsigned else "SMALLINT"
    if kind in ("mediumint", "int", "integer"):
        return "UINTEGER" if unsigned else "INTEGER"
    if kind == "bigint":
        return "UBIGINT" if unsigned else "BIGINT"
    if kind in ("decimal", "numeric"):
        return mysql_type.upper().replace(" UNSIGNED", "")
    if kind in ("double", "float", "real"):
        return "DOUBLE"
    if kind == "date":
        return "DATE"
    if kind in ("timestamp", "datetime"):
        return "TIMESTAMP"
    if kind == "time":
        return "TIME"
    return "VARCHAR"


def destination_config():
    _, dest = config()
    return dest


def copy_tables(dry_run=False, output_path=None):
    dest_config = destination_config()
    mysql = connect_with_retry(dest_config, int(os.getenv("ETL_RETRY_COUNT", "3")))
    db_path = Path(output_path or os.getenv("DUCKDB_PATH", str(ROOT / "dashboard.duckdb")))
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    try:
        cur = mysql.cursor()
        cur.execute("""SELECT TABLE_NAME FROM information_schema.TABLES
            WHERE TABLE_SCHEMA=%s AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME""",
            (dest_config["database"],))
        selected = [row[0] for row in cur.fetchall()]
        if dry_run:
            for table in selected:
                cur.execute(f"SELECT COUNT(*) FROM `{table}`")
                print(f"{table}: {cur.fetchone()[0]} rows")
            print(f"DuckDB output: {db_path}")
            return

        db_path.parent.mkdir(parents=True, exist_ok=True)
        duck = duckdb.connect(str(db_path))
        try:
            duck.execute("BEGIN TRANSACTION")
            for table in selected:
                cur.execute("""SELECT COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s
                    ORDER BY ORDINAL_POSITION""", (dest_config["database"], table))
                columns = cur.fetchall()
                if not columns:
                    LOG.warning("Skipping %s because its MySQL schema could not be read", table)
                    continue
                column_names = [col[0] for col in columns]
                declarations = [
                    f"{quote_identifier(name)} {duckdb_type(mysql_type)}"
                    + (" NOT NULL" if nullable == "NO" else "")
                    for name, mysql_type, nullable in columns
                ]
                quoted_table = quote_identifier(table)
                quoted_columns = ",".join(quote_identifier(name) for name in column_names)
                placeholders = ",".join("?" for _ in column_names)
                duck.execute(f"DROP TABLE IF EXISTS {quoted_table}")
                duck.execute(f"CREATE TABLE {quoted_table} ({','.join(declarations)})")

                cur.execute(f"SELECT {','.join(f'`{name}`' for name in column_names)} FROM `{table}`")
                copied = 0
                while True:
                    batch = cur.fetchmany(CHUNK_SIZE)
                    if not batch:
                        break
                    duck.executemany(
                        f"INSERT INTO {quoted_table} ({quoted_columns}) VALUES ({placeholders})",
                        batch,
                    )
                    copied += len(batch)
                print(f"{table}: copied {copied} rows")
            duck.execute("COMMIT")
        except Exception:
            duck.execute("ROLLBACK")
            raise
        finally:
            duck.close()
        print(f"DuckDB export complete: {db_path}")
    finally:
        cur.close()
        mysql.close()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="List all destination tables and row counts")
    parser.add_argument("--output", help="DuckDB file path (default: dashboard.duckdb in this folder)")
    args = parser.parse_args()
    try:
        copy_tables(dry_run=args.dry_run, output_path=args.output)
    except Exception:
        LOG.exception("Dashboard DuckDB export failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
