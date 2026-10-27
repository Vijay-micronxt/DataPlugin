"""Incrementally aggregate delivered sales by food type, store, and date."""
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
LOG = logging.getLogger("sales_by_food_type")
PROCESS = "sales_by_food_type_v1"
LEDGER = "etl_processed_order_items_food_type"


def ensure_destination(cn):
    cur = cn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS sales_by_food_type (
      food_type_id SMALLINT UNSIGNED NOT NULL,
      food_type_name VARCHAR(150) NOT NULL,
      store_id INT UNSIGNED NOT NULL,
      store_name VARCHAR(255) NULL,
      sold_date DATE NOT NULL,
      total_quantity DECIMAL(20,4) NOT NULL DEFAULT 0,
      total_sales DECIMAL(20,2) NOT NULL DEFAULT 0,
      total_orders BIGINT UNSIGNED NOT NULL DEFAULT 0,
      last_updated TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      PRIMARY KEY (food_type_id,store_id,sold_date),
      KEY idx_food_type_sales_date (sold_date,total_sales),
      KEY idx_food_type_store_date (store_id,sold_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
    cur.execute("""CREATE TABLE IF NOT EXISTS etl_metadata (
      process_name VARCHAR(100) NOT NULL PRIMARY KEY,
      last_order_id BIGINT UNSIGNED NOT NULL DEFAULT 0,
      last_fetch_timestamp TIMESTAMP NULL DEFAULT NULL
    ) ENGINE=InnoDB""")
    cur.execute(f"""CREATE TABLE IF NOT EXISTS {LEDGER} (
      source_order_item_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
      source_order_id BIGINT UNSIGNED NOT NULL,
      food_type_id SMALLINT UNSIGNED NOT NULL,
      store_id INT UNSIGNED NOT NULL,
      sold_date DATE NOT NULL,
      quantity_processed DECIMAL(20,4) NOT NULL,
      sales_amount_processed DECIMAL(20,2) NOT NULL,
      processed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      KEY idx_food_type_ledger_order (source_order_id)
    ) ENGINE=InnoDB""")
    cur.execute("INSERT IGNORE INTO etl_metadata(process_name,last_order_id) VALUES (%s,0)", (PROCESS,))
    cn.commit()
    cur.close()


def run(source, dest, dry_run=False, rebuild=False):
    policy = os.getenv("ETL_SALES_POLICY", "").strip().lower()
    clause = policy_clause(policy)
    statuses = [s.strip().lower() for s in os.getenv("ETL_DELIVERED_STATUSES", "delivered").split(",") if s.strip()] if policy == "delivered_gross" else []
    if policy == "delivered_gross" and not statuses:
        raise ValueError("ETL_DELIVERED_STATUSES cannot be empty")
    batch_size = int(os.getenv("ETL_BATCH_SIZE", "500"))
    retries = int(os.getenv("ETL_RETRY_COUNT", "3"))
    if not 1 <= batch_size <= 10000 or not 0 <= retries <= 10:
        raise ValueError("ETL_BATCH_SIZE must be 1..10000; ETL_RETRY_COUNT must be 0..10")

    src = connect_with_retry(source, retries)
    dst = connect_with_retry(dest, retries)
    try:
        validate_source(src)
        cur = src.cursor()
        cur.execute("SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME='skus' AND COLUMN_NAME='food_type_id'", (src.database,))
        if cur.fetchone()[0] != 1:
            raise RuntimeError("Source skus table is missing food_type_id")
        cur.execute("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME='food_types'", (src.database,))
        if cur.fetchone()[0] != 1:
            raise RuntimeError("Source food_types table is missing")
        cur.close()

        if dry_run:
            cur = src.cursor()
            cur.execute(f"SELECT COUNT(DISTINCT i.id) FROM materialized_order_items i JOIN materialized_orders o ON o.order_id=i.order_id WHERE {clause}", statuses)
            print("eligible delivered source items:", cur.fetchone()[0])
            cur.execute("SELECT COUNT(*) FROM food_types WHERE deleted_at IS NULL")
            print("active food types:", cur.fetchone()[0])
            cur.close()
            return

        ensure_destination(dst)
        cur = dst.cursor()
        lock_name = "etl:" + PROCESS
        cur.execute("SELECT GET_LOCK(%s,0)", (lock_name,))
        if cur.fetchone()[0] != 1:
            raise RuntimeError("Another sales-by-food-type ETL is already running")
        try:
            if rebuild:
                dst.start_transaction()
                cur.execute(f"DELETE FROM {LEDGER}")
                cur.execute("DELETE FROM sales_by_food_type")
                cur.execute("UPDATE etl_metadata SET last_order_id=0,last_fetch_timestamp=NULL WHERE process_name=%s", (PROCESS,))
                dst.commit()
                LOG.info("sales by food type rebuild reset committed")

            source_cur = src.cursor(dictionary=True)
            while True:
                cur.execute("SELECT last_order_id FROM etl_metadata WHERE process_name=%s FOR UPDATE", (PROCESS,))
                checkpoint = int(cur.fetchone()[0])
                dst.commit()
                source_cur.execute(f"""SELECT DISTINCT i.order_id FROM materialized_order_items i
                    JOIN materialized_orders o ON o.order_id=i.order_id
                    WHERE i.order_id>%s AND {clause} ORDER BY i.order_id LIMIT %s""",
                    [checkpoint, *statuses, batch_size])
                order_ids = [int(row["order_id"]) for row in source_cur.fetchall()]
                if not order_ids:
                    break
                upper = max(order_ids)
                started = time.time()
                source_cur.execute(f"""SELECT i.id,i.order_id,i.sku_id,i.store_id,
                    DATE(o.order_created_at) AS sold_date,
                    COALESCE(sk.food_type_id,0) AS food_type_id,
                    COALESCE(NULLIF(ft.name,''),'Unclassified') AS food_type_name,
                    s.name AS store_name,i.quantity,i.amount
                    FROM materialized_order_items i
                    JOIN materialized_orders o ON o.order_id=i.order_id
                    LEFT JOIN skus sk ON sk.id=i.sku_id
                    LEFT JOIN food_types ft ON ft.id=sk.food_type_id
                    LEFT JOIN stores s ON s.id=i.store_id
                    WHERE i.order_id>%s AND i.order_id<=%s AND {clause}
                    ORDER BY i.order_id,i.id""", [checkpoint, upper, *statuses])
                rows = source_cur.fetchall()
                totals = defaultdict(lambda: [None, Decimal(0), Decimal(0), set()])
                newly_processed = 0
                try:
                    dst.start_transaction()
                    for row in rows:
                        if any(row[k] is None for k in ("id", "order_id", "store_id", "sold_date", "quantity", "amount")):
                            raise ValueError(f"Missing required source value on order item {row.get('id')}")
                        quantity, sales = Decimal(str(row["quantity"])), Decimal(str(row["amount"]))
                        if not quantity.is_finite() or not sales.is_finite() or quantity < 0 or sales < 0:
                            raise ValueError(f"Invalid quantity or sales amount on order item {row['id']}")
                        food_type_id = int(row["food_type_id"] or 0)
                        key = (food_type_id, int(row["store_id"]), row["sold_date"])
                        cur.execute(f"""INSERT IGNORE INTO {LEDGER}
                            (source_order_item_id,source_order_id,food_type_id,store_id,sold_date,quantity_processed,sales_amount_processed)
                            VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                            (row["id"], row["order_id"], food_type_id, row["store_id"], row["sold_date"], quantity, sales))
                        if cur.rowcount == 1:
                            newly_processed += 1
                            totals[key][0] = (row["food_type_name"], row["store_name"])
                            totals[key][1] += quantity
                            totals[key][2] += sales
                            totals[key][3].add(int(row["order_id"]))
                    for (food_type_id, store_id, sold_date), ((food_name, store_name), quantity, sales, orders) in totals.items():
                        cur.execute("""INSERT INTO sales_by_food_type
                            (food_type_id,food_type_name,store_id,store_name,sold_date,total_quantity,total_sales,total_orders)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                            ON DUPLICATE KEY UPDATE food_type_name=VALUES(food_type_name),store_name=VALUES(store_name),
                            total_quantity=total_quantity+VALUES(total_quantity),total_sales=total_sales+VALUES(total_sales),
                            total_orders=total_orders+VALUES(total_orders),last_updated=CURRENT_TIMESTAMP""",
                            (food_type_id, food_name, store_id, store_name, sold_date, quantity, sales, len(orders)))
                    cur.execute("UPDATE etl_metadata SET last_order_id=%s,last_fetch_timestamp=CURRENT_TIMESTAMP WHERE process_name=%s", (upper, PROCESS))
                    dst.commit()
                except Exception:
                    dst.rollback()
                    raise
                LOG.info("batch fetched_items=%s newly_processed=%s aggregates_updated=%s checkpoint=%s elapsed=%.2fs",
                         len(rows), newly_processed, len(totals), upper, time.time()-started)
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
    parser.add_argument("--dry-run", action="store_true", help="Count eligible delivered items without writing")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild only the sales_by_food_type table and ledger")
    args = parser.parse_args()
    try:
        source, dest = config()
        run(source, dest, dry_run=args.dry_run, rebuild=args.rebuild)
    except Exception as exc:
        LOG.error("Sales by food type ETL stopped: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
