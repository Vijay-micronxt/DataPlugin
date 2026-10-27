"""Refresh the identified-customer table for repeat and one-time buyers."""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from etl_top_selling_skus import connect_with_retry, config

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("repeat_nonrepeated_customers")
PROCESS = "repeat_nonrepeated_customers_v1"


def ensure_destination(cn):
    cur = cn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS repeat_nonrepeated_customers (
      customer_id BIGINT UNSIGNED NOT NULL,
      store_id INT UNSIGNED NOT NULL,
      store_name VARCHAR(255) NOT NULL,
      customer_name VARCHAR(255) NOT NULL,
      customer_segment ENUM('Repeat','One-time') NOT NULL,
      total_orders BIGINT UNSIGNED NOT NULL,
      total_quantity DECIMAL(20,4) NOT NULL DEFAULT 0,
      total_sales DECIMAL(20,2) NOT NULL DEFAULT 0,
      first_order_date DATE NOT NULL,
      last_order_date DATE NOT NULL,
      refreshed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (customer_id,store_id),
      KEY idx_customer_segment_orders (customer_segment,total_orders),
      KEY idx_customer_segment_sales (customer_segment,total_sales)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
    cur.execute("""CREATE TABLE IF NOT EXISTS repeat_nonrepeated_customers_metadata (
      process_name VARCHAR(100) NOT NULL PRIMARY KEY,
      last_refresh_timestamp TIMESTAMP NULL DEFAULT NULL,
      customer_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
      repeat_customer_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
      one_time_customer_count BIGINT UNSIGNED NOT NULL DEFAULT 0
    ) ENGINE=InnoDB""")
    cur.execute("INSERT IGNORE INTO repeat_nonrepeated_customers_metadata(process_name) VALUES (%s)", (PROCESS,))
    cur.execute("SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME='repeat_nonrepeated_customers' AND COLUMN_NAME IN ('store_id','store_name')", (cn.database,))
    if cur.fetchone()[0] != 2:
        # This is a derived table and will be repopulated atomically below.
        cur.execute("DROP TABLE repeat_nonrepeated_customers")
        cur.execute("""CREATE TABLE repeat_nonrepeated_customers (
          customer_id BIGINT UNSIGNED NOT NULL,
          store_id INT UNSIGNED NOT NULL,
          store_name VARCHAR(255) NOT NULL,
          customer_name VARCHAR(255) NOT NULL,
          customer_segment ENUM('Repeat','One-time') NOT NULL,
          total_orders BIGINT UNSIGNED NOT NULL,
          total_quantity DECIMAL(20,4) NOT NULL DEFAULT 0,
          total_sales DECIMAL(20,2) NOT NULL DEFAULT 0,
          first_order_date DATE NOT NULL,
          last_order_date DATE NOT NULL,
          refreshed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (customer_id,store_id),
          KEY idx_customer_segment_orders (customer_segment,total_orders),
          KEY idx_customer_segment_sales (customer_segment,total_sales)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
    cn.commit()
    cur.close()


def run(dest, dry_run=False):
    retries = int(os.getenv("ETL_RETRY_COUNT", "3"))
    cn = connect_with_retry(dest, retries)
    try:
        cur = cn.cursor()
        if dry_run:
            cur.execute("""SELECT COUNT(*),SUM(total_orders>=2),SUM(total_orders=1)
                FROM top_customers_by_order WHERE is_identified_customer=1 AND customer_id<>0""")
            print("identified customers, repeat customers, one-time customers:", cur.fetchone())
            return
        ensure_destination(cn)
        cur.execute("SELECT GET_LOCK(%s,0)", ("etl:" + PROCESS,))
        if cur.fetchone()[0] != 1:
            raise RuntimeError("Another repeat/non-repeated customer refresh is already running")
        cn.commit()  # close the read transaction; GET_LOCK is connection-scoped
        try:
            cn.start_transaction()
            cur.execute("DELETE FROM repeat_nonrepeated_customers")
            cur.execute("""INSERT INTO repeat_nonrepeated_customers
                (customer_id,store_id,store_name,customer_name,customer_segment,total_orders,total_quantity,total_sales,first_order_date,last_order_date)
                SELECT customer_id,store_id,store_name,COALESCE(NULLIF(customer_name,''),CONCAT('Customer ',customer_id)),
                       CASE WHEN total_orders>=2 THEN 'Repeat' ELSE 'One-time' END,
                       total_orders,total_quantity,total_sales,first_order_date,last_order_date
                FROM top_customers_by_order
                WHERE is_identified_customer=1 AND customer_id<>0 AND total_orders>0""")
            customer_count = cur.rowcount
            cur.execute("""SELECT SUM(customer_segment='Repeat'),SUM(customer_segment='One-time')
                FROM repeat_nonrepeated_customers""")
            repeat_count, one_time_count = cur.fetchone()
            cur.execute("""UPDATE repeat_nonrepeated_customers_metadata
                SET last_refresh_timestamp=CURRENT_TIMESTAMP,customer_count=%s,
                    repeat_customer_count=%s,one_time_customer_count=%s
                WHERE process_name=%s""", (customer_count, repeat_count or 0, one_time_count or 0, PROCESS))
            cn.commit()
            LOG.info("refreshed identified customers=%s repeat=%s one_time=%s", customer_count, repeat_count or 0, one_time_count or 0)
            print(f"Refreshed {customer_count} identified customers: {repeat_count or 0} repeat, {one_time_count or 0} one-time.")
        except Exception:
            cn.rollback()
            raise
        finally:
            cur.execute("SELECT RELEASE_LOCK(%s)", ("etl:" + PROCESS,))
            cur.fetchone()
        cur.close()
    finally:
        cn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Count customer segments without refreshing the report")
    args = parser.parse_args()
    try:
        _, dest = config()
        run(dest, dry_run=args.dry_run)
    except Exception as exc:
        LOG.error("Repeat/non-repeated customer ETL stopped: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
