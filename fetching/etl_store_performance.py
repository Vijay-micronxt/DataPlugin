"""Build daily store performance aggregates independently of the SKU ETL."""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

from etl_top_selling_skus import connect_with_retry, config, policy_clause, validate_source

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("store_performance")
PROCESS = "store_performance_v1"
LEDGER = "etl_processed_order_items_store"


def ensure_destination(cn):
    cur = cn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS etl_metadata (
      process_name VARCHAR(100) NOT NULL PRIMARY KEY,
      last_order_id BIGINT UNSIGNED NOT NULL DEFAULT 0,
      last_fetch_timestamp TIMESTAMP NULL DEFAULT NULL
    ) ENGINE=InnoDB""")
    cur.execute("""CREATE TABLE IF NOT EXISTS store_performance (
      store_id INT UNSIGNED NOT NULL,
      sold_date DATE NOT NULL,
      store_name VARCHAR(255) CHARACTER SET utf8mb4 NULL,
      total_quantity DECIMAL(20,4) NOT NULL DEFAULT 0,
      total_sales DECIMAL(20,2) NOT NULL DEFAULT 0,
      total_orders BIGINT UNSIGNED NOT NULL DEFAULT 0,
      last_updated TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      PRIMARY KEY (store_id,sold_date),
      KEY idx_store_performance_date_sales (sold_date,total_sales)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
    cur.execute(f"""CREATE TABLE IF NOT EXISTS {LEDGER} (
      source_order_item_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
      source_order_id BIGINT UNSIGNED NOT NULL,
      store_id INT UNSIGNED NOT NULL,
      sold_date DATE NOT NULL,
      quantity_processed DECIMAL(20,4) NOT NULL,
      sales_amount_processed DECIMAL(20,2) NOT NULL,
      processed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      KEY idx_store_ledger_order (source_order_id)
    ) ENGINE=InnoDB""")
    cur.execute("INSERT IGNORE INTO etl_metadata(process_name,last_order_id) VALUES (%s,0)", (PROCESS,))
    cn.commit()
    cur.close()


def run(source, dest, dry_run=False, rebuild=False):
    policy = os.getenv("ETL_SALES_POLICY", "").strip().lower()
    clause = policy_clause(policy)
    status_params = [s.strip().lower() for s in os.getenv("ETL_DELIVERED_STATUSES", "delivered").split(",") if s.strip()] if policy == "delivered_gross" else []
    if policy == "delivered_gross" and not status_params:
        raise ValueError("ETL_DELIVERED_STATUSES cannot be empty")
    batch_size = int(os.getenv("ETL_BATCH_SIZE", "500"))
    retries = int(os.getenv("ETL_RETRY_COUNT", "3"))
    if not 1 <= batch_size <= 10000 or not 0 <= retries <= 10:
        raise ValueError("ETL_BATCH_SIZE must be 1..10000; ETL_RETRY_COUNT must be 0..10")

    src = connect_with_retry(source, retries)
    dst = connect_with_retry(dest, retries)
    try:
        validate_source(src)
        if dry_run:
            cur = src.cursor()
            cur.execute(f"SELECT COUNT(DISTINCT i.id) FROM materialized_order_items i JOIN materialized_orders o ON o.order_id=i.order_id WHERE {clause}", status_params)
            print("eligible source items:", cur.fetchone()[0])
            cur.close()
            return

        ensure_destination(dst)
        cur = dst.cursor()
        lock_name = "etl:" + PROCESS
        cur.execute("SELECT GET_LOCK(%s,0)", (lock_name,))
        if cur.fetchone()[0] != 1:
            raise RuntimeError("Another store performance ETL is already running")
        try:
            if rebuild:
                dst.start_transaction()
                cur.execute(f"DELETE FROM {LEDGER}")
                cur.execute("DELETE FROM store_performance")
                cur.execute("UPDATE etl_metadata SET last_order_id=0,last_fetch_timestamp=NULL WHERE process_name=%s", (PROCESS,))
                dst.commit()
                LOG.info("store performance rebuild reset committed")

            source_cur = src.cursor(dictionary=True)
            while True:
                cur.execute("SELECT last_order_id FROM etl_metadata WHERE process_name=%s FOR UPDATE", (PROCESS,))
                checkpoint = int(cur.fetchone()[0])
                dst.commit()
                source_cur.execute(f"SELECT DISTINCT i.order_id FROM materialized_order_items i JOIN materialized_orders o ON o.order_id=i.order_id WHERE i.order_id>%s AND {clause} ORDER BY i.order_id LIMIT %s", [checkpoint, *status_params, batch_size])
                order_ids = [int(r["order_id"]) for r in source_cur.fetchall()]
                if not order_ids:
                    break
                upper = max(order_ids)
                started = time.time()
                source_cur.execute(f"SELECT DISTINCT i.id,i.order_id,i.store_id,DATE(o.order_created_at) AS sold_date,s.name AS store_name,i.quantity,i.amount FROM materialized_order_items i JOIN materialized_orders o ON o.order_id=i.order_id LEFT JOIN stores s ON s.id=i.store_id WHERE i.order_id>%s AND i.order_id<=%s AND {clause} ORDER BY i.order_id,i.id", [checkpoint, upper, *status_params])
                rows = source_cur.fetchall()
                totals = defaultdict(lambda: [None, Decimal(0), Decimal(0), set()])
                inserted = 0
                try:
                    dst.start_transaction()
                    for row in rows:
                        if any(row[k] is None for k in ("id", "order_id", "store_id", "sold_date", "quantity", "amount")):
                            raise ValueError(f"Missing required value in source order item {row.get('id')}")
                        quantity = Decimal(str(row["quantity"]))
                        sales = Decimal(str(row["amount"]))
                        if not quantity.is_finite() or not sales.is_finite() or quantity < 0 or sales < 0:
                            raise ValueError(f"Invalid quantity or sales amount in source order item {row['id']}")
                        cur.execute(f"INSERT IGNORE INTO {LEDGER}(source_order_item_id,source_order_id,store_id,sold_date,quantity_processed,sales_amount_processed) VALUES (%s,%s,%s,%s,%s,%s)", (row["id"], row["order_id"], row["store_id"], row["sold_date"], quantity, sales))
                        if cur.rowcount == 1:
                            inserted += 1
                            key = (int(row["store_id"]), row["sold_date"])
                            totals[key][0] = row["store_name"]
                            totals[key][1] += quantity
                            totals[key][2] += sales
                            totals[key][3].add(int(row["order_id"]))
                    for (store_id, sold_date), (store_name, quantity, sales, orders) in totals.items():
                        cur.execute("INSERT INTO store_performance(store_id,sold_date,store_name,total_quantity,total_sales,total_orders) VALUES (%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE store_name=VALUES(store_name),total_quantity=total_quantity+VALUES(total_quantity),total_sales=total_sales+VALUES(total_sales),total_orders=total_orders+VALUES(total_orders),last_updated=CURRENT_TIMESTAMP", (store_id, sold_date, store_name, quantity, sales, len(orders)))
                    cur.execute("UPDATE etl_metadata SET last_order_id=%s,last_fetch_timestamp=CURRENT_TIMESTAMP WHERE process_name=%s", (upper, PROCESS))
                    dst.commit()
                except Exception:
                    dst.rollback()
                    raise
                LOG.info("batch fetched=%s newly_processed=%s stores_updated=%s checkpoint=%s elapsed=%.2fs", len(rows), inserted, len(totals), upper, time.time()-started)
            source_cur.close()
        finally:
            cur.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
            cur.fetchone()
            cur.close()
    finally:
        src.close()
        dst.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Count eligible source rows without writing")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild only store_performance and its ledger")
    args = parser.parse_args()
    try:
        source, dest = config()
        run(source, dest, dry_run=args.dry_run, rebuild=args.rebuild)
    except Exception as exc:
        LOG.error("Store performance ETL stopped: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
