"""Incrementally aggregate delivered sales by calendar date and hour (all stores)."""
from __future__ import annotations

import argparse
import logging
import os
import re
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
LOG = logging.getLogger("sales_per_hour")
PROCESS = "sales_per_hour_v1"
LEDGER = "etl_processed_order_items_hourly"
REPORT_UTC_OFFSET = os.getenv("ETL_REPORT_UTC_OFFSET", "+05:30").strip()


def ensure_destination(cn):
    cur = cn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS sales_per_hour (
      sold_date DATE NOT NULL,
      sold_hour TINYINT UNSIGNED NOT NULL,
      total_quantity DECIMAL(20,4) NOT NULL DEFAULT 0,
      total_sales DECIMAL(20,2) NOT NULL DEFAULT 0,
      total_orders BIGINT UNSIGNED NOT NULL DEFAULT 0,
      total_order_items BIGINT UNSIGNED NOT NULL DEFAULT 0,
      last_updated TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      PRIMARY KEY (sold_date,sold_hour),
      KEY idx_sales_hour (sold_hour,sold_date),
      KEY idx_hourly_sales (sold_date,total_sales)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
    cur.execute("""CREATE TABLE IF NOT EXISTS store_hourly_sales (
      store_id INT UNSIGNED NOT NULL,
      store_name VARCHAR(255) NULL,
      sales_date DATE NOT NULL,
      hour_of_day TINYINT UNSIGNED NOT NULL,
      total_sales_value DECIMAL(20,2) NOT NULL DEFAULT 0.00,
      total_orders BIGINT UNSIGNED NOT NULL DEFAULT 0,
      total_quantity DECIMAL(20,4) NOT NULL DEFAULT 0.0000,
      last_updated TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      PRIMARY KEY (store_id,sales_date,hour_of_day),
      KEY idx_store_hourly_date (sales_date,store_id),
      KEY idx_store_hourly_time (hour_of_day,sales_date),
      CONSTRAINT chk_store_hourly_hour CHECK (hour_of_day BETWEEN 0 AND 23)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
    cur.execute("""CREATE TABLE IF NOT EXISTS etl_metadata (
      process_name VARCHAR(100) NOT NULL PRIMARY KEY,
      last_order_id BIGINT UNSIGNED NOT NULL DEFAULT 0,
      last_fetch_timestamp TIMESTAMP NULL DEFAULT NULL
    ) ENGINE=InnoDB""")
    cur.execute(f"""CREATE TABLE IF NOT EXISTS {LEDGER} (
      source_order_item_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
      source_order_id BIGINT UNSIGNED NOT NULL,
      sold_date DATE NOT NULL,
      sold_hour TINYINT UNSIGNED NOT NULL,
      quantity_processed DECIMAL(20,4) NOT NULL,
      sales_amount_processed DECIMAL(20,2) NOT NULL,
      processed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      KEY idx_hourly_ledger_order (source_order_id)
    ) ENGINE=InnoDB""")
    cur.execute("INSERT IGNORE INTO etl_metadata(process_name,last_order_id) VALUES (%s,0)", (PROCESS,))
    cn.commit()
    cur.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME='store_hourly_sales'", (cn.database,))
    columns = {row[0] for row in cur.fetchall()}
    if "store_name" not in columns:
        cur.execute("ALTER TABLE store_hourly_sales ADD COLUMN store_name VARCHAR(255) NULL AFTER store_id")
        cn.commit()
    cur.close()


def refresh_store_hourly_sales(src, dst, clause, status_params):
    """Rebuild the store/hour summary from current eligible D2 rows atomically.

    Full replacement keeps source edits, cancellations, and late arrivals reflected
    without adding old totals a second time. DISTINCT on the stable item ID prevents
    duplicate materialized order rows from multiplying sales.
    """
    if not re.fullmatch(r"(?:[+-](?:0\d|1[0-3]):[0-5]\d|[+-]14:00)", REPORT_UTC_OFFSET):
        raise ValueError("ETL_REPORT_UTC_OFFSET must be a UTC offset such as +05:30")
    src_cur = src.cursor(dictionary=True)
    dst_cur = dst.cursor()
    src_cur.execute(f"""SELECT DISTINCT i.id AS source_order_item_id,i.order_id,i.store_id,
        COALESCE(NULLIF(s.name,''),CONCAT('Store ',i.store_id)) AS store_name,
        DATE(CONVERT_TZ(o.order_created_at,@@session.time_zone,%s)) AS sales_date,
        HOUR(CONVERT_TZ(o.order_created_at,@@session.time_zone,%s)) AS hour_of_day,
        i.quantity,i.amount
        FROM materialized_order_items i
        JOIN materialized_orders o ON o.order_id=i.order_id
        LEFT JOIN stores s ON s.id=i.store_id
        WHERE {clause}
        ORDER BY i.id""", [REPORT_UTC_OFFSET, REPORT_UTC_OFFSET, *status_params])

    aggregates = defaultdict(lambda: [None, Decimal(0), Decimal(0), set()])
    while True:
        rows = src_cur.fetchmany(2000)
        if not rows:
            break
        for row in rows:
            if any(row[k] is None for k in ("source_order_item_id", "order_id", "store_id", "sales_date", "hour_of_day", "quantity", "amount")):
                raise ValueError(f"Invalid or unconvertible timestamp/store on source item {row.get('source_order_item_id')}")
            hour = int(row["hour_of_day"])
            quantity, amount = Decimal(str(row["quantity"])), Decimal(str(row["amount"]))
            if not 0 <= hour <= 23 or not quantity.is_finite() or not amount.is_finite() or quantity < 0 or amount < 0:
                raise ValueError(f"Invalid hour, quantity, or amount on source item {row['source_order_item_id']}")
            key = (int(row["store_id"]), row["sales_date"], hour)
            aggregates[key][0] = row["store_name"]
            aggregates[key][1] += amount
            aggregates[key][2] += quantity
            aggregates[key][3].add(int(row["order_id"]))

    values = [(store_id, store_name, sales_date, hour, amount, len(orders), quantity)
              for (store_id, sales_date, hour), (store_name, amount, quantity, orders) in aggregates.items()]
    try:
        # The existing incremental order ETL has committed before this refresh.
        dst.commit()
        dst.start_transaction()
        dst_cur.execute("DELETE FROM store_hourly_sales")
        for start in range(0, len(values), 2000):
            dst_cur.executemany("""INSERT INTO store_hourly_sales
                (store_id,store_name,sales_date,hour_of_day,total_sales_value,total_orders,total_quantity)
                VALUES (%s,%s,%s,%s,%s,%s,%s)""", values[start:start+2000])
        dst.commit()
    except Exception:
        dst.rollback()
        raise
    finally:
        src_cur.close()
        dst_cur.close()
    LOG.info("store_hourly_sales refreshed rows=%s timezone=UTC%s", len(values), REPORT_UTC_OFFSET)
    return len(values)


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
    if not re.fullmatch(r"(?:[+-](?:0\d|1[0-3]):[0-5]\d|[+-]14:00)", REPORT_UTC_OFFSET):
        raise ValueError("ETL_REPORT_UTC_OFFSET must be a UTC offset such as +05:30")
    src = connect_with_retry(source, retries)
    dst = connect_with_retry(dest, retries)
    try:
        validate_source(src)
        if dry_run:
            cur = src.cursor()
            cur.execute(f"SELECT COUNT(DISTINCT i.id) FROM materialized_order_items i JOIN materialized_orders o ON o.order_id=i.order_id WHERE {clause}", statuses)
            print("eligible delivered source items:", cur.fetchone()[0])
            cur.close()
            return
        ensure_destination(dst)
        cur = dst.cursor()
        lock_name = "etl:" + PROCESS
        cur.execute("SELECT GET_LOCK(%s,0)", (lock_name,))
        if cur.fetchone()[0] != 1:
            raise RuntimeError("Another sales-per-hour ETL is already running")
        try:
            if rebuild:
                dst.start_transaction()
                cur.execute(f"DELETE FROM {LEDGER}")
                cur.execute("DELETE FROM sales_per_hour")
                cur.execute("UPDATE etl_metadata SET last_order_id=0,last_fetch_timestamp=NULL WHERE process_name=%s", (PROCESS,))
                dst.commit()
                LOG.info("sales per hour rebuild reset committed")
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
                source_cur.execute(f"""SELECT i.id,i.order_id,DATE(o.order_created_at) AS sold_date,
                    HOUR(o.order_created_at) AS sold_hour,i.quantity,i.amount
                    FROM materialized_order_items i
                    JOIN materialized_orders o ON o.order_id=i.order_id
                    WHERE i.order_id>%s AND i.order_id<=%s AND {clause}
                    ORDER BY i.order_id,i.id""", [checkpoint, upper, *statuses])
                rows = source_cur.fetchall()
                totals = defaultdict(lambda: [Decimal(0), Decimal(0), 0, set()])
                newly_processed = 0
                try:
                    dst.start_transaction()
                    for row in rows:
                        if any(row[k] is None for k in ("id", "order_id", "sold_date", "sold_hour", "quantity", "amount")):
                            raise ValueError(f"Missing required source value on order item {row.get('id')}")
                        quantity, sales = Decimal(str(row["quantity"])), Decimal(str(row["amount"]))
                        hour = int(row["sold_hour"])
                        if not quantity.is_finite() or not sales.is_finite() or quantity < 0 or sales < 0 or not 0 <= hour <= 23:
                            raise ValueError(f"Invalid quantity, sales amount, or order hour on item {row['id']}")
                        cur.execute(f"""INSERT IGNORE INTO {LEDGER}
                            (source_order_item_id,source_order_id,sold_date,sold_hour,quantity_processed,sales_amount_processed)
                            VALUES (%s,%s,%s,%s,%s,%s)""",
                            (row["id"], row["order_id"], row["sold_date"], hour, quantity, sales))
                        if cur.rowcount == 1:
                            newly_processed += 1
                            key = (row["sold_date"], hour)
                            totals[key][0] += quantity
                            totals[key][1] += sales
                            totals[key][2] += 1
                            totals[key][3].add(int(row["order_id"]))
                    for (sold_date, hour), (quantity, sales, item_count, orders) in totals.items():
                        cur.execute("""INSERT INTO sales_per_hour
                            (sold_date,sold_hour,total_quantity,total_sales,total_orders,total_order_items)
                            VALUES (%s,%s,%s,%s,%s,%s)
                            ON DUPLICATE KEY UPDATE total_quantity=total_quantity+VALUES(total_quantity),
                            total_sales=total_sales+VALUES(total_sales),total_orders=total_orders+VALUES(total_orders),
                            total_order_items=total_order_items+VALUES(total_order_items),last_updated=CURRENT_TIMESTAMP""",
                            (sold_date, hour, quantity, sales, len(orders), item_count))
                    cur.execute("UPDATE etl_metadata SET last_order_id=%s,last_fetch_timestamp=CURRENT_TIMESTAMP WHERE process_name=%s", (upper, PROCESS))
                    dst.commit()
                except Exception:
                    dst.rollback()
                    raise
                LOG.info("batch fetched_items=%s newly_processed=%s hourly_buckets=%s checkpoint=%s elapsed=%.2fs",
                         len(rows), newly_processed, len(totals), upper, time.time()-started)
            source_cur.close()
            refreshed = refresh_store_hourly_sales(src, dst, clause, statuses)
            print(f"Refreshed store_hourly_sales: {refreshed} store/date/hour rows (UTC{REPORT_UTC_OFFSET}).")
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
    parser.add_argument("--rebuild", action="store_true", help="Rebuild sales_per_hour and its ledger, then refresh store_hourly_sales")
    args = parser.parse_args()
    try:
        source, dest = config()
        run(source, dest, dry_run=args.dry_run, rebuild=args.rebuild)
    except Exception as exc:
        LOG.error("Sales per hour ETL stopped: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
