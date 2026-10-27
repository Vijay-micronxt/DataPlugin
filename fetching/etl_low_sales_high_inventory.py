"""Build a daily list of SKUs with low recent sales and high inventory.

Reads the latest sku_daily_snapshots date from the source and the delivered
sales aggregates in the destination's top_selling_skus table.
"""
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
LOG = logging.getLogger("low_sales_high_inventory")
PROCESS = "low_sales_high_inventory_v1"
SALES_WINDOW_DAYS = int(os.getenv("LOW_SALES_WINDOW_DAYS", "30"))
MAX_UNITS_SOLD = Decimal(os.getenv("LOW_SALES_MAX_UNITS", "5"))
MIN_INVENTORY = Decimal(os.getenv("HIGH_INVENTORY_MIN_UNITS", "20"))
BATCH_SIZE = int(os.getenv("LOW_INVENTORY_BATCH_SIZE", "2000"))


def ensure_destination(cn):
    cur = cn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS low_sales_high_inventory (
      sku_id BIGINT UNSIGNED NOT NULL,
      store_id INT UNSIGNED NOT NULL,
      snapshot_date DATE NOT NULL,
      sku_name VARCHAR(255) CHARACTER SET utf8mb4 NULL,
      store_name VARCHAR(255) CHARACTER SET utf8mb4 NULL,
      inventory_quantity DECIMAL(20,4) NOT NULL,
      inventory_value DECIMAL(20,4) NULL,
      sales_window_start DATE NOT NULL,
      sales_window_end DATE NOT NULL,
      units_sold_30d DECIMAL(20,4) NOT NULL DEFAULT 0,
      sales_amount_30d DECIMAL(20,2) NOT NULL DEFAULT 0,
      max_units_sold DECIMAL(20,4) NOT NULL,
      min_inventory_quantity DECIMAL(20,4) NOT NULL,
      calculated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (sku_id,store_id,snapshot_date),
      KEY idx_low_sales_snapshot (snapshot_date,units_sold_30d),
      KEY idx_low_sales_store (store_id,snapshot_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
    cur.execute("""CREATE TABLE IF NOT EXISTS etl_metadata (
      process_name VARCHAR(100) NOT NULL PRIMARY KEY,
      last_order_id BIGINT UNSIGNED NOT NULL DEFAULT 0,
      last_fetch_timestamp TIMESTAMP NULL DEFAULT NULL
    ) ENGINE=InnoDB""")
    cur.execute("INSERT IGNORE INTO etl_metadata(process_name,last_order_id) VALUES (%s,0)", (PROCESS,))
    cur.execute("""CREATE TABLE IF NOT EXISTS low_sales_high_inventory_metadata (
      process_name VARCHAR(100) NOT NULL PRIMARY KEY,
      last_snapshot_date DATE NULL,
      last_fetch_timestamp TIMESTAMP NULL DEFAULT NULL,
      sales_window_days INT UNSIGNED NOT NULL,
      max_units_sold DECIMAL(20,4) NOT NULL,
      min_inventory_quantity DECIMAL(20,4) NOT NULL
    ) ENGINE=InnoDB""")
    cur.execute("""INSERT IGNORE INTO low_sales_high_inventory_metadata
      (process_name,sales_window_days,max_units_sold,min_inventory_quantity) VALUES (%s,%s,%s,%s)""",
      (PROCESS, SALES_WINDOW_DAYS, MAX_UNITS_SOLD, MIN_INVENTORY))
    cn.commit()
    cur.close()


def run(source, dest, dry_run=False):
    if SALES_WINDOW_DAYS < 1 or MAX_UNITS_SOLD < 0 or MIN_INVENTORY < 0 or not 1 <= BATCH_SIZE <= 20000:
        raise ValueError("Invalid low-sales/high-inventory thresholds or batch size")
    retries = int(os.getenv("ETL_RETRY_COUNT", "3"))
    src = connect_with_retry(source, retries)
    dst = connect_with_retry(dest, retries)
    try:
        scur = src.cursor(dictionary=True)
        dcur = dst.cursor()
        # The source table's primary-key tail provides the latest loaded snapshot
        # date without scanning millions of rows for MAX(snapshot_date).
        scur.execute("SELECT snapshot_date FROM sku_daily_snapshots ORDER BY id DESC LIMIT 1")
        latest = scur.fetchone()
        if not latest or not latest["snapshot_date"]:
            raise RuntimeError("sku_daily_snapshots has no dated rows")
        snapshot_date = latest["snapshot_date"]
        window_start = snapshot_date - timedelta(days=SALES_WINDOW_DAYS - 1)

        if dry_run:
            scur.execute("SELECT COUNT(*) FROM sku_daily_snapshots WHERE snapshot_date=%s AND quantity >= %s",
                         (snapshot_date, MIN_INVENTORY))
            candidate_count = next(iter(scur.fetchone().values()))
            print(f"Latest snapshot: {snapshot_date}; inventory candidates: {candidate_count}; "
                  f"classification: <= {MAX_UNITS_SOLD} units sold in {SALES_WINDOW_DAYS} days, "
                  f">= {MIN_INVENTORY} units inventory")
            return

        ensure_destination(dst)
        dcur.execute("SELECT GET_LOCK(%s,0)", ("etl:" + PROCESS,))
        if dcur.fetchone()[0] != 1:
            raise RuntimeError("Another low-sales/high-inventory ETL is already running")
        try:
            # Keep the sales lookup bounded to this snapshot's trailing window.
            dcur.execute("""SELECT sku_id,store_id,SUM(total_quantity),SUM(total_sales)
                FROM top_selling_skus WHERE sold_date BETWEEN %s AND %s
                GROUP BY sku_id,store_id""", (window_start, snapshot_date))
            sales = {(int(sku), int(store)): (Decimal(str(qty or 0)), Decimal(str(amount or 0)))
                     for sku, store, qty, amount in dcur.fetchall()}
            # mysql-connector starts a transaction for the sales SELECT; close it
            # before explicitly beginning the atomic snapshot replacement below.
            dst.commit()

            # Read just the chosen date in bounded batches, resolving names in source.
            scur.execute("""SELECT ds.sku_id,ds.store_id,ds.snapshot_date,ds.quantity,ds.inventory_value,
                       sk.name AS sku_name,st.name AS store_name
                FROM sku_daily_snapshots ds
                LEFT JOIN skus sk ON sk.id=ds.sku_id
                LEFT JOIN stores st ON st.id=ds.store_id
                WHERE ds.snapshot_date=%s ORDER BY ds.sku_id,ds.store_id""", (snapshot_date,))
            dst.start_transaction()
            dcur.execute("DELETE FROM low_sales_high_inventory WHERE snapshot_date=%s", (snapshot_date,))
            inserted = 0
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
                    if inventory < MIN_INVENTORY:
                        continue
                    sold, amount = sales.get((int(row["sku_id"]), int(row["store_id"])), (Decimal(0), Decimal(0)))
                    if sold > MAX_UNITS_SOLD:
                        continue
                    values.append((row["sku_id"], row["store_id"], snapshot_date, row["sku_name"],
                                   row["store_name"], inventory, row["inventory_value"], window_start,
                                   snapshot_date, sold, amount, MAX_UNITS_SOLD, MIN_INVENTORY))
                if values:
                    dcur.executemany("""INSERT INTO low_sales_high_inventory
                      (sku_id,store_id,snapshot_date,sku_name,store_name,inventory_quantity,inventory_value,
                       sales_window_start,sales_window_end,units_sold_30d,sales_amount_30d,
                       max_units_sold,min_inventory_quantity)
                      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", values)
                    inserted += len(values)
            dcur.execute("""UPDATE low_sales_high_inventory_metadata
                SET last_snapshot_date=%s,last_fetch_timestamp=CURRENT_TIMESTAMP,
                    sales_window_days=%s,max_units_sold=%s,min_inventory_quantity=%s
                WHERE process_name=%s""",
                (snapshot_date, SALES_WINDOW_DAYS, MAX_UNITS_SOLD, MIN_INVENTORY, PROCESS))
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
    parser.add_argument("--init-destination", action="store_true", help="Create the result table without reading source data")
    args = parser.parse_args()
    try:
        source, dest = config()
        if args.init_destination:
            cn = connect_with_retry(dest, int(os.getenv("ETL_RETRY_COUNT", "3")))
            try:
                ensure_destination(cn)
            finally:
                cn.close()
            print("low_sales_high_inventory table is ready.")
        else:
            run(source, dest, dry_run=args.dry_run)
    except Exception as exc:
        LOG.error("Low-sales/high-inventory ETL stopped: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
