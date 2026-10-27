"""Build a daily list of SKUs with high recent sales and low inventory."""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

from etl_top_selling_skus import connect_with_retry, config

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("high_sales_low_inventory")
PROCESS = "high_sales_low_inventory_v1"
SALES_WINDOW_DAYS = int(os.getenv("HIGH_SALES_WINDOW_DAYS", "30"))
MIN_UNITS_SOLD = Decimal(os.getenv("HIGH_SALES_MIN_UNITS", "20"))
MAX_INVENTORY = Decimal(os.getenv("LOW_INVENTORY_MAX_UNITS", "5"))
BATCH_SIZE = int(os.getenv("LOW_INVENTORY_BATCH_SIZE", "2000"))


def ensure_destination(cn):
    cur = cn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS high_sales_low_inventory (
      sku_id BIGINT UNSIGNED NOT NULL,
      store_id INT UNSIGNED NOT NULL,
      snapshot_date DATE NOT NULL,
      sku_name VARCHAR(255) CHARACTER SET utf8mb4 NULL,
      store_name VARCHAR(255) CHARACTER SET utf8mb4 NULL,
      inventory_quantity DECIMAL(20,4) NULL,
      inventory_value DECIMAL(20,4) NULL,
      inventory_snapshot_found TINYINT(1) NOT NULL DEFAULT 1,
      sales_window_start DATE NOT NULL,
      sales_window_end DATE NOT NULL,
      units_sold_30d DECIMAL(20,4) NOT NULL DEFAULT 0,
      sales_amount_30d DECIMAL(20,2) NOT NULL DEFAULT 0,
      min_units_sold DECIMAL(20,4) NOT NULL,
      max_inventory_quantity DECIMAL(20,4) NOT NULL,
      calculated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (sku_id,store_id,snapshot_date),
      KEY idx_high_sales_snapshot (snapshot_date,units_sold_30d),
      KEY idx_high_sales_store (store_id,snapshot_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
    cur.execute("""CREATE TABLE IF NOT EXISTS high_sales_low_inventory_metadata (
      process_name VARCHAR(100) NOT NULL PRIMARY KEY,
      last_snapshot_date DATE NULL,
      last_fetch_timestamp TIMESTAMP NULL DEFAULT NULL,
      sales_window_days INT UNSIGNED NOT NULL,
      min_units_sold DECIMAL(20,4) NOT NULL,
      max_inventory_quantity DECIMAL(20,4) NOT NULL
    ) ENGINE=InnoDB""")
    cur.execute("""INSERT IGNORE INTO high_sales_low_inventory_metadata
      (process_name,sales_window_days,min_units_sold,max_inventory_quantity) VALUES (%s,%s,%s,%s)""",
      (PROCESS, SALES_WINDOW_DAYS, MIN_UNITS_SOLD, MAX_INVENTORY))
    cn.commit()
    # Upgrade tables created before missing inventory records were reported.
    cur.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME='high_sales_low_inventory'", (cn.database,))
    columns = {row[0] for row in cur.fetchall()}
    if "inventory_snapshot_found" not in columns:
        cur.execute("ALTER TABLE high_sales_low_inventory MODIFY inventory_quantity DECIMAL(20,4) NULL, ADD COLUMN inventory_snapshot_found TINYINT(1) NOT NULL DEFAULT 1 AFTER inventory_value")
        cn.commit()
    cur.close()


def run(source, dest, dry_run=False):
    if SALES_WINDOW_DAYS < 1 or MIN_UNITS_SOLD < 0 or MAX_INVENTORY < 0 or not 1 <= BATCH_SIZE <= 20000:
        raise ValueError("Invalid high-sales/low-inventory thresholds or batch size")
    retries = int(os.getenv("ETL_RETRY_COUNT", "3"))
    src = connect_with_retry(source, retries)
    dst = connect_with_retry(dest, retries)
    try:
        scur = src.cursor(dictionary=True)
        dcur = dst.cursor()
        scur.execute("SELECT snapshot_date FROM sku_daily_snapshots ORDER BY id DESC LIMIT 1")
        latest = scur.fetchone()
        if not latest or not latest["snapshot_date"]:
            raise RuntimeError("sku_daily_snapshots has no dated rows")
        snapshot_date = latest["snapshot_date"]
        window_start = snapshot_date - timedelta(days=SALES_WINDOW_DAYS - 1)

        if dry_run:
            scur.execute("SELECT COUNT(*) FROM sku_daily_snapshots WHERE snapshot_date=%s AND quantity <= %s",
                         (snapshot_date, MAX_INVENTORY))
            candidate_count = next(iter(scur.fetchone().values()))
            print(f"Latest snapshot: {snapshot_date}; low-inventory candidates: {candidate_count}; "
                  f"classification: >= {MIN_UNITS_SOLD} units sold in {SALES_WINDOW_DAYS} days, "
                  f"<= {MAX_INVENTORY} units inventory")
            return

        ensure_destination(dst)
        dcur.execute("SELECT GET_LOCK(%s,0)", ("etl:" + PROCESS,))
        if dcur.fetchone()[0] != 1:
            raise RuntimeError("Another high-sales/low-inventory ETL is already running")
        try:
            dcur.execute("""SELECT sku_id,store_id,SUM(total_quantity),SUM(total_sales),
                       MAX(sku_name),MAX(store_name)
                FROM top_selling_skus WHERE sold_date BETWEEN %s AND %s
                GROUP BY sku_id,store_id""", (window_start, snapshot_date))
            sales = {(int(sku), int(store)): (Decimal(str(qty or 0)), Decimal(str(amount or 0)), sku_name, store_name)
                     for sku, store, qty, amount, sku_name, store_name in dcur.fetchall()}
            dst.commit()

            scur.execute("""SELECT ds.sku_id,ds.store_id,ds.snapshot_date,ds.quantity,ds.inventory_value,
                       sk.name AS sku_name,st.name AS store_name
                FROM sku_daily_snapshots ds
                LEFT JOIN skus sk ON sk.id=ds.sku_id
                LEFT JOIN stores st ON st.id=ds.store_id
                WHERE ds.snapshot_date=%s ORDER BY ds.sku_id,ds.store_id""", (snapshot_date,))
            dst.start_transaction()
            dcur.execute("DELETE FROM high_sales_low_inventory WHERE snapshot_date=%s", (snapshot_date,))
            inserted = 0
            seen_inventory_pairs = set()
            while True:
                rows = scur.fetchmany(BATCH_SIZE)
                if not rows:
                    break
                values = []
                for row in rows:
                    if any(row[k] is None for k in ("sku_id", "store_id", "quantity", "snapshot_date")):
                        raise ValueError("Source snapshot has a null SKU, store, quantity, or date")
                    inventory = Decimal(str(row["quantity"]))
                    if not inventory.is_finite():
                        raise ValueError(f"Invalid inventory quantity for SKU {row['sku_id']}")
                    key = (int(row["sku_id"]), int(row["store_id"]))
                    seen_inventory_pairs.add(key)
                    if inventory > MAX_INVENTORY:
                        continue
                    sold, amount, _, _ = sales.get(key, (Decimal(0), Decimal(0), None, None))
                    if sold < MIN_UNITS_SOLD:
                        continue
                    values.append((row["sku_id"], row["store_id"], snapshot_date, row["sku_name"],
                                   row["store_name"], inventory, row["inventory_value"], 1, window_start,
                                   snapshot_date, sold, amount, MIN_UNITS_SOLD, MAX_INVENTORY))
                if values:
                    dcur.executemany("""INSERT INTO high_sales_low_inventory
                      (sku_id,store_id,snapshot_date,sku_name,store_name,inventory_quantity,inventory_value,
                       inventory_snapshot_found,sales_window_start,sales_window_end,units_sold_30d,sales_amount_30d,
                       min_units_sold,max_inventory_quantity)
                      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", values)
                    inserted += len(values)
            # High-selling SKU/store pairs without a row in the snapshot are
            # surfaced for review with NULL inventory and an explicit false flag.
            missing_inventory = []
            for (sku_id, store_id), (sold, amount, sku_name, store_name) in sales.items():
                if (sku_id, store_id) in seen_inventory_pairs or sold < MIN_UNITS_SOLD:
                    continue
                missing_inventory.append((sku_id, store_id, snapshot_date, sku_name, store_name,
                                          None, None, 0, window_start, snapshot_date, sold, amount,
                                          MIN_UNITS_SOLD, MAX_INVENTORY))
            if missing_inventory:
                dcur.executemany("""INSERT INTO high_sales_low_inventory
                  (sku_id,store_id,snapshot_date,sku_name,store_name,inventory_quantity,inventory_value,
                   inventory_snapshot_found,sales_window_start,sales_window_end,units_sold_30d,sales_amount_30d,
                   min_units_sold,max_inventory_quantity)
                  VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", missing_inventory)
                inserted += len(missing_inventory)
            dcur.execute("""UPDATE high_sales_low_inventory_metadata
                SET last_snapshot_date=%s,last_fetch_timestamp=CURRENT_TIMESTAMP,
                    sales_window_days=%s,min_units_sold=%s,max_inventory_quantity=%s
                WHERE process_name=%s""",
                (snapshot_date, SALES_WINDOW_DAYS, MIN_UNITS_SOLD, MAX_INVENTORY, PROCESS))
            dst.commit()
            LOG.info("snapshot=%s window=%s..%s candidates=%s", snapshot_date, window_start, snapshot_date, inserted)
            print(f"Updated {inserted} rows for snapshot {snapshot_date} (sales window {window_start} to {snapshot_date}).")
        except Exception:
            dst.rollback()
            raise
        finally:
            dcur.execute("SELECT RELEASE_LOCK(%s)", ("etl:" + PROCESS,))
            dcur.fetchone()
        dcur.close()
        scur.close()
    finally:
        src.close()
        dst.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Show latest inventory snapshot and threshold; do not write results")
    parser.add_argument("--init-destination", action="store_true", help="Create result and metadata tables without reading source data")
    args = parser.parse_args()
    try:
        source, dest = config()
        if args.init_destination:
            cn = connect_with_retry(dest, int(os.getenv("ETL_RETRY_COUNT", "3")))
            try:
                ensure_destination(cn)
            finally:
                cn.close()
            print("high_sales_low_inventory tables are ready.")
        else:
            run(source, dest, dry_run=args.dry_run)
    except Exception as exc:
        LOG.error("High-sales/low-inventory ETL stopped: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
