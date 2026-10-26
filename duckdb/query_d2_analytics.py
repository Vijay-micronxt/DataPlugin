"""Run date-filtered queries against storenxt_d2_analytics.duckdb."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import duckdb


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("table", help="DuckDB report/fact table")
    parser.add_argument("--from-date", required=True, type=date.fromisoformat, help="Inclusive YYYY-MM-DD")
    parser.add_argument("--to-date", required=True, type=date.fromisoformat, help="Inclusive YYYY-MM-DD")
    parser.add_argument("--database", default="storenxt_d2_analytics.duckdb")
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()
    if args.from_date > args.to_date:
        parser.error("--from-date must be on or before --to-date")
    if args.limit < 1 or args.limit > 100_000:
        parser.error("--limit must be between 1 and 100000")

    db_path = Path(args.database)
    if not db_path.is_file():
        parser.error(f"DuckDB file not found: {db_path}")
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
        if args.table not in tables:
            parser.error(f"Unknown table {args.table!r}. Available tables: {', '.join(sorted(tables))}")
        columns = {row[0] for row in con.execute(f"DESCRIBE {args.table}").fetchall()}
        date_column = "report_date" if "report_date" in columns else "cohort_date" if "cohort_date" in columns else None
        if date_column is None:
            parser.error(f"{args.table} has no report_date or cohort_date to filter.")
        rows = con.execute(
            f"SELECT * FROM {args.table} WHERE {date_column} BETWEEN ? AND ? LIMIT ?",
            [args.from_date, args.to_date, args.limit],
        ).fetchdf()
        print(rows.to_json(orient="records", date_format="iso", date_unit="s", force_ascii=False))
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
