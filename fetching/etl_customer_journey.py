"""Refresh customer counts for the Cart-to-Payment journey funnel by store."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from etl_top_selling_skus import connect_with_retry, config

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("customer_journey")
STAGES = ("Cart", "Order", "Payment")
ALL_STORES_ID = 0


def ensure_destination(cn):
    cur = cn.cursor()
    cur.execute("SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME='customer_journey' AND COLUMN_NAME IN ('journey_stage','customer_count')", (cn.database,))
    if cur.fetchone()[0] != 2:
        cur.execute("DROP TABLE IF EXISTS customer_journey")
    cur.execute("""CREATE TABLE IF NOT EXISTS customer_journey (
      store_id INT UNSIGNED NOT NULL,
      store_name VARCHAR(255) NOT NULL,
      journey_stage ENUM('Cart','Order','Payment') NOT NULL,
      customer_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
      refreshed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      PRIMARY KEY (store_id,journey_stage),
      KEY idx_journey_stage_count (journey_stage,customer_count)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
    cn.commit()
    cur.close()


def run(source, dest, dry_run=False):
    retries = int(os.getenv("ETL_RETRY_COUNT", "3"))
    src = connect_with_retry(source, retries)
    dst = connect_with_retry(dest, retries)
    try:
        cur = src.cursor()
        cur.execute("SELECT id,COALESCE(NULLIF(TRIM(name),''),CONCAT('Store ',id)) FROM stores")
        store_names = {int(store_id): name for store_id, name in cur.fetchall()}
        customers_by_stage_store = {stage: {} for stage in STAGES}

        # Saved-for-later carts still represent the Cart stage; removed cart rows do not.
        cur.execute("""SELECT DISTINCT customer_id,store_id FROM cart_items
            WHERE customer_id IS NOT NULL AND customer_id>0
              AND store_id IS NOT NULL AND deleted_at IS NULL""")
        customers_by_stage_store["Cart"] = {}
        for customer_id, store_id in cur.fetchall():
            customers_by_stage_store["Cart"].setdefault(int(store_id), set()).add(int(customer_id))

        cur.execute("""SELECT DISTINCT customer_id,store_id FROM materialized_orders
            WHERE customer_id IS NOT NULL AND customer_id>0
              AND cancelled_at IS NULL AND deleted_at IS NULL""")
        for customer_id, store_id in cur.fetchall():
            customers_by_stage_store["Order"].setdefault(int(store_id), set()).add(int(customer_id))

        cur.execute("""SELECT DISTINCT o.customer_id,COALESCE(p.mat_opay_store_id,o.store_id) AS store_id
            FROM materialized_order_payments p
            JOIN materialized_orders o ON o.order_id=p.mat_opay_order_id
            WHERE o.customer_id IS NOT NULL AND o.customer_id>0
              AND o.cancelled_at IS NULL AND o.deleted_at IS NULL
              AND p.mat_opay_capture_amount>0""")
        for customer_id, store_id in cur.fetchall():
            if store_id is not None:
                customers_by_stage_store["Payment"].setdefault(int(store_id), set()).add(int(customer_id))
        cur.close()

        store_ids = set(store_names)
        for stage in STAGES:
            store_ids.update(customers_by_stage_store[stage])
        prepared = []
        for store_id in sorted(store_ids):
            store_name = store_names.get(store_id, f"Store {store_id}")
            for stage in STAGES:
                prepared.append((store_id, store_name, stage,
                                 len(customers_by_stage_store[stage].get(store_id, set()))))
        for stage in STAGES:
            all_customers = set().union(*customers_by_stage_store[stage].values()) if customers_by_stage_store[stage] else set()
            prepared.append((ALL_STORES_ID, "All Stores", stage, len(all_customers)))

        if dry_run:
            for store_id, store_name, stage, count in prepared:
                if store_id == ALL_STORES_ID:
                    print(f"{store_name} | {stage}: {count} customers")
            return len(prepared)

        ensure_destination(dst)
        dst.start_transaction()
        try:
            out = dst.cursor()
            out.execute("DELETE FROM customer_journey")
            if prepared:
                out.executemany("""INSERT INTO customer_journey
                    (store_id,store_name,journey_stage,customer_count)
                    VALUES (%s,%s,%s,%s)""", prepared)
            dst.commit()
            out.close()
        except Exception:
            dst.rollback()
            raise
        LOG.info("customer_journey funnel refreshed stores=%s stage_rows=%s",
                 len(store_ids), len(prepared))
        print(f"Refreshed customer_journey: {len(prepared)} store/stage counts.")
        return len(prepared)
    finally:
        src.close()
        dst.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Show funnel counts without changing destination data")
    args = parser.parse_args()
    try:
        source, dest = config()
        run(source, dest, dry_run=args.dry_run)
    except Exception:
        LOG.exception("Customer journey ETL failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
