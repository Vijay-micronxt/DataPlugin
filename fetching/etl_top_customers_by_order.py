"""Incrementally aggregate delivered order activity by customer."""
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
LOG = logging.getLogger("top_customers_by_order")
PROCESS = "top_customers_by_order_v1"
LEDGER = "etl_processed_order_items_customer"
GUEST_ID = 0
GUEST_NAME = "Guest / unidentified"


def ensure_destination(cn, rebuild=False):
    cur = cn.cursor()
    cur.execute("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME='top_customers_by_order'", (cn.database,))
    existing = cur.fetchone()[0] > 0
    if existing:
        cur.execute("SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME='top_customers_by_order' AND COLUMN_NAME IN ('store_id','store_name')", (cn.database,))
        if cur.fetchone()[0] != 2:
            if not rebuild:
                cur.close()
                raise RuntimeError("Customer summary schema needs a store-level rebuild; rerun with --rebuild")
            cur.execute("DROP TABLE top_customers_by_order")
            cur.execute(f"DROP TABLE IF EXISTS {LEDGER}")
    cur.execute("""CREATE TABLE IF NOT EXISTS top_customers_by_order (
      customer_id BIGINT UNSIGNED NOT NULL,
      store_id INT UNSIGNED NOT NULL,
      store_name VARCHAR(255) NOT NULL,
      customer_name VARCHAR(255) NOT NULL,
      is_identified_customer TINYINT(1) NOT NULL,
      total_orders BIGINT UNSIGNED NOT NULL DEFAULT 0,
      total_quantity DECIMAL(20,4) NOT NULL DEFAULT 0,
      total_sales DECIMAL(20,2) NOT NULL DEFAULT 0,
      total_order_items BIGINT UNSIGNED NOT NULL DEFAULT 0,
      first_order_date DATE NOT NULL,
      last_order_date DATE NOT NULL,
      last_updated TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      PRIMARY KEY (customer_id,store_id),
      KEY idx_customers_orders (is_identified_customer,total_orders),
      KEY idx_customers_sales (total_sales)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
    cur.execute("""CREATE TABLE IF NOT EXISTS etl_metadata (
      process_name VARCHAR(100) NOT NULL PRIMARY KEY,
      last_order_id BIGINT UNSIGNED NOT NULL DEFAULT 0,
      last_fetch_timestamp TIMESTAMP NULL DEFAULT NULL
    ) ENGINE=InnoDB""")
    cur.execute(f"""CREATE TABLE IF NOT EXISTS {LEDGER} (
      source_order_item_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
      source_order_id BIGINT UNSIGNED NOT NULL,
      customer_id BIGINT UNSIGNED NOT NULL,
      store_id INT UNSIGNED NOT NULL,
      sold_date DATE NOT NULL,
      quantity_processed DECIMAL(20,4) NOT NULL,
      sales_amount_processed DECIMAL(20,2) NOT NULL,
      processed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      KEY idx_customer_ledger_order (source_order_id),
      KEY idx_customer_ledger_customer (customer_id)
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
        c = src.cursor()
        c.execute("SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME='materialized_orders' AND COLUMN_NAME IN ('customer_id','customer_name','order_created_at')", (src.database,))
        if c.fetchone()[0] != 3:
            raise RuntimeError("Source order table is missing customer identity or order timestamp fields")
        c.execute("SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME='customers' AND COLUMN_NAME IN ('id','first_name','last_name')", (src.database,))
        if c.fetchone()[0] != 3:
            raise RuntimeError("Source customers table is missing required name fields")
        c.close()
        if dry_run:
            c = src.cursor()
            c.execute(f"SELECT COUNT(DISTINCT o.order_id),COUNT(DISTINCT CASE WHEN o.customer_id IS NULL OR o.customer_id=0 THEN o.order_id END) FROM materialized_orders o JOIN materialized_order_items i ON i.order_id=o.order_id WHERE {clause}", statuses)
            total, guests = c.fetchone()
            print(f"eligible delivered orders: {total}; guest/unidentified orders: {guests}; identified orders: {total-guests}")
            c.close()
            return

        ensure_destination(dst, rebuild=rebuild)
        cur = dst.cursor()
        lock_name = "etl:" + PROCESS
        cur.execute("SELECT GET_LOCK(%s,0)", (lock_name,))
        if cur.fetchone()[0] != 1:
            raise RuntimeError("Another top-customers ETL is already running")
        try:
            if rebuild:
                dst.start_transaction()
                cur.execute(f"DELETE FROM {LEDGER}")
                cur.execute("DELETE FROM top_customers_by_order")
                cur.execute("UPDATE etl_metadata SET last_order_id=0,last_fetch_timestamp=NULL WHERE process_name=%s", (PROCESS,))
                dst.commit()
                LOG.info("top customers rebuild reset committed")
            source_cur = src.cursor(dictionary=True)
            while True:
                cur.execute("SELECT last_order_id FROM etl_metadata WHERE process_name=%s FOR UPDATE", (PROCESS,))
                checkpoint = int(cur.fetchone()[0])
                dst.commit()
                source_cur.execute(f"""SELECT DISTINCT i.order_id FROM materialized_order_items i
                    JOIN materialized_orders o ON o.order_id=i.order_id
                    WHERE i.order_id>%s AND {clause} ORDER BY i.order_id LIMIT %s""",
                    [checkpoint, *statuses, batch_size])
                order_ids = [int(r["order_id"]) for r in source_cur.fetchall()]
                if not order_ids:
                    break
                upper = max(order_ids)
                started = time.time()
                source_cur.execute(f"""SELECT i.id,i.order_id,
                    COALESCE(o.customer_id,0) AS customer_id,
                    CASE WHEN o.customer_id IS NULL OR o.customer_id=0 THEN %s
                         ELSE COALESCE(NULLIF(o.customer_name,''),NULLIF(TRIM(CONCAT_WS(' ',c.first_name,c.last_name)),''),'Customer') END AS customer_name,
                    COALESCE(i.store_id,0) AS store_id,
                    COALESCE(NULLIF(TRIM(s.name),''),CONCAT('Store ',COALESCE(i.store_id,0))) AS store_name,
                    DATE(o.order_created_at) AS sold_date,i.quantity,i.amount
                    FROM materialized_order_items i
                    JOIN materialized_orders o ON o.order_id=i.order_id
                    LEFT JOIN customers c ON c.id=o.customer_id
                    LEFT JOIN stores s ON s.id=i.store_id
                    WHERE i.order_id>%s AND i.order_id<=%s AND {clause}
                    ORDER BY i.order_id,i.id""", [GUEST_NAME, checkpoint, upper, *statuses])
                rows = source_cur.fetchall()
                totals = defaultdict(lambda: [None, None, Decimal(0), Decimal(0), 0, set(), None, None])
                newly_processed = 0
                try:
                    dst.start_transaction()
                    for row in rows:
                        if any(row[k] is None for k in ("id", "order_id", "store_id", "sold_date", "quantity", "amount")):
                            raise ValueError(f"Missing required source value on order item {row.get('id')}")
                        qty, amount = Decimal(str(row["quantity"])), Decimal(str(row["amount"]))
                        if not qty.is_finite() or not amount.is_finite() or qty < 0 or amount < 0:
                            raise ValueError(f"Invalid quantity or sales amount on order item {row['id']}")
                        customer_id = int(row["customer_id"] or GUEST_ID)
                        store_id = int(row["store_id"] or 0)
                        cur.execute(f"""INSERT IGNORE INTO {LEDGER}
                            (source_order_item_id,source_order_id,customer_id,store_id,sold_date,quantity_processed,sales_amount_processed)
                            VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                            (row["id"], row["order_id"], customer_id, store_id, row["sold_date"], qty, amount))
                        if cur.rowcount == 1:
                            newly_processed += 1
                            agg = totals[(customer_id, store_id)]
                            agg[0] = row["customer_name"] or (GUEST_NAME if customer_id == GUEST_ID else "Customer")
                            agg[1] = row["store_name"]
                            agg[2] += qty
                            agg[3] += amount
                            agg[4] += 1
                            agg[5].add(int(row["order_id"]))
                            agg[6] = row["sold_date"] if agg[6] is None else min(agg[6], row["sold_date"])
                            agg[7] = row["sold_date"] if agg[7] is None else max(agg[7], row["sold_date"])
                    for (customer_id, store_id), (name, store_name, qty, sales, item_count, orders, first_date, last_date) in totals.items():
                        is_identified = int(customer_id != GUEST_ID)
                        cur.execute("""INSERT INTO top_customers_by_order
                            (customer_id,store_id,store_name,customer_name,is_identified_customer,total_orders,total_quantity,total_sales,
                             total_order_items,first_order_date,last_order_date)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                            ON DUPLICATE KEY UPDATE customer_name=VALUES(customer_name),
                            store_name=VALUES(store_name),
                            is_identified_customer=VALUES(is_identified_customer),
                            total_orders=total_orders+VALUES(total_orders),total_quantity=total_quantity+VALUES(total_quantity),
                            total_sales=total_sales+VALUES(total_sales),total_order_items=total_order_items+VALUES(total_order_items),
                            first_order_date=LEAST(first_order_date,VALUES(first_order_date)),
                            last_order_date=GREATEST(last_order_date,VALUES(last_order_date)),last_updated=CURRENT_TIMESTAMP""",
                            (customer_id, store_id, store_name, name, is_identified, len(orders), qty, sales, item_count, first_date, last_date))
                    cur.execute("UPDATE etl_metadata SET last_order_id=%s,last_fetch_timestamp=CURRENT_TIMESTAMP WHERE process_name=%s", (upper, PROCESS))
                    dst.commit()
                except Exception:
                    dst.rollback()
                    raise
                LOG.info("batch fetched_items=%s newly_processed=%s customers_updated=%s checkpoint=%s elapsed=%.2fs",
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
    parser.add_argument("--dry-run", action="store_true", help="Count eligible orders without writing")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild the customer/store summary and ledger")
    args = parser.parse_args()
    try:
        source, dest = config()
        run(source, dest, dry_run=args.dry_run, rebuild=args.rebuild)
    except Exception as exc:
        LOG.error("Top customers ETL stopped: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
