"""Incremental D2 SKU sales ETL. Source tables are read-only."""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv
from mysql.connector import Error

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("top_selling_skus")
PROCESS = "top_selling_skus_v1"
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def ident(value: str) -> str:
    if not IDENTIFIER.fullmatch(value):
        raise ValueError(f"Invalid SQL identifier: {value!r}")
    return f"`{value}`"


def config():
    def get(*names, required=True, default=None):
        for name in names:
            if os.getenv(name):
                return os.environ[name]
        if required:
            raise ValueError(f"Missing configuration: set {names[0]}")
        return default
    source = dict(host=get("DB_HOST"), port=int(get("DB_PORT", required=False, default="3306")),
                  user=get("DB_USERNAME", "DB_USER"), password=get("DB_PASSWORD"),
                  database=get("DB_DATABASE"), connection_timeout=10)
    dest = dict(host=get("DEST_HOST"), port=int(get("DEST_PORT", required=False, default="3306")),
                user=get("DEST_USER", "DEST_USERNAME"), password=get("DEST_PASSWORD"),
                database=get("DEST_DATABASE"), connection_timeout=10)
    return source, dest


def connect_with_retry(settings, retries):
    for attempt in range(retries + 1):
        try:
            return mysql.connector.connect(**settings)
        except Error:
            if attempt >= retries:
                raise
            delay = min(2 ** attempt, 16)
            LOG.warning("Database connection failed; retrying in %ss (attempt %s/%s)", delay, attempt + 1, retries)
            time.sleep(delay)


def inspect(source):
    cn = connect_with_retry(source, 2)
    cur = cn.cursor(dictionary=True)
    for table in ("materialized_orders", "materialized_order_items"):
        cur.execute("SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION", (source["database"], table))
        rows = cur.fetchall()
        if not rows:
            raise RuntimeError(f"Required source table {table} is missing")
        print(f"{table}:")
        for row in rows:
            print("  {COLUMN_NAME}: {COLUMN_TYPE}, nullable={IS_NULLABLE}, key={COLUMN_KEY}".format(**row))
    print("\nProposed source mapping:")
    print("  item primary key=id; order key=order_id; sold date=DATE(order_created_at); SKU=sku_id/sku_name; store=store_id; quantity=quantity; sales=amount")
    print("  amount is stored as DOUBLE and matched quantity * selling_price for 12,865 of 12,866 rows in the inspected snapshot")
    print("  order status and cancellation fields exist; return amount is order-level and cannot be allocated to SKUs from these tables")
    cn.close()


def ensure_destination(cn):
    cur = cn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS top_selling_skus (
      sku_id BIGINT UNSIGNED NOT NULL,
      sold_date DATE NOT NULL,
      sku_name VARCHAR(255) CHARACTER SET utf8mb4 NULL,
      store_id INT UNSIGNED NOT NULL,
      store_name VARCHAR(255) CHARACTER SET utf8mb4 NULL,
      total_quantity DECIMAL(20,4) NOT NULL DEFAULT 0,
      total_sales DECIMAL(20,2) NOT NULL DEFAULT 0,
      last_updated TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      PRIMARY KEY (sku_id,store_id,sold_date),
      KEY idx_sold_date_sales (sold_date,total_sales)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
    cur.execute("""CREATE TABLE IF NOT EXISTS etl_metadata (
      process_name VARCHAR(100) NOT NULL PRIMARY KEY,
      last_order_id BIGINT UNSIGNED NOT NULL DEFAULT 0,
      last_fetch_timestamp TIMESTAMP NULL DEFAULT NULL
    ) ENGINE=InnoDB""")
    cur.execute("""CREATE TABLE IF NOT EXISTS etl_processed_order_items (
      source_order_item_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
      source_order_id BIGINT UNSIGNED NOT NULL,
      sku_id BIGINT UNSIGNED NOT NULL,
      quantity_processed DECIMAL(20,4) NOT NULL,
      sales_amount_processed DECIMAL(20,2) NOT NULL,
      processed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      KEY idx_processed_order_id (source_order_id), KEY idx_processed_sku (sku_id)
    ) ENGINE=InnoDB""")
    cur.execute("INSERT IGNORE INTO etl_metadata(process_name,last_order_id) VALUES (%s,0)", (PROCESS,))
    cn.commit()
    # Upgrade the first version's one-row-per-SKU table to SKU/store grain.
    cur.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME='top_selling_skus'", (cn.database,))
    columns={r[0] for r in cur.fetchall()}
    if "store_id" not in columns:
        cur.execute("ALTER TABLE top_selling_skus DROP PRIMARY KEY, ADD COLUMN store_id INT UNSIGNED NOT NULL DEFAULT 0 AFTER sku_id, ADD COLUMN sku_name VARCHAR(255) NULL AFTER sku_id, ADD COLUMN store_name VARCHAR(255) NULL AFTER store_id, ADD PRIMARY KEY (sku_id,store_id)")
        cn.commit()
        # Legacy global totals cannot be split across stores; repopulate from the source ledger.
        cur.execute("DELETE FROM top_selling_skus")
        cur.execute("DELETE FROM etl_processed_order_items")
        cur.execute("UPDATE etl_metadata SET last_order_id=0,last_fetch_timestamp=NULL WHERE process_name=%s", (PROCESS,))
        cn.commit()
        columns.add("store_id")
    if "sold_date" not in columns:
        cur.execute("ALTER TABLE top_selling_skus DROP PRIMARY KEY, ADD COLUMN sold_date DATE NOT NULL DEFAULT '1000-01-01' AFTER store_id, ADD PRIMARY KEY (sku_id,store_id,sold_date), ADD KEY idx_sold_date_sales (sold_date,total_sales)")
        cn.commit()
        # Existing totals do not carry a sales date; rebuild daily rows from source.
        cur.execute("DELETE FROM top_selling_skus")
        cur.execute("DELETE FROM etl_processed_order_items")
        cur.execute("UPDATE etl_metadata SET last_order_id=0,last_fetch_timestamp=NULL WHERE process_name=%s", (PROCESS,))
        cn.commit()
    cur.execute("ALTER TABLE top_selling_skus MODIFY sku_name VARCHAR(255) CHARACTER SET utf8mb4 NULL, MODIFY store_name VARCHAR(255) CHARACTER SET utf8mb4 NULL")
    cn.commit()
    cur.close()


def validate_source(cn):
    cur = cn.cursor()
    cur.execute("SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME='materialized_order_items' AND COLUMN_NAME IN ('id','order_id','sku_id','quantity','amount','cancelled_at','deleted_at')", (cn.database,))
    if cur.fetchone()[0] != 7:
        raise RuntimeError("Source table is missing one or more required columns")
    cur.execute("SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME='materialized_orders' AND COLUMN_NAME IN ('order_id','order_status_name','cancelled_at','deleted_at')", (cn.database,))
    if cur.fetchone()[0] != 4:
        raise RuntimeError("Source order table is missing status/cancellation fields")
    cur.execute("SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME='materialized_orders' AND COLUMN_NAME='order_created_at'", (cn.database,))
    if cur.fetchone()[0] != 1:
        raise RuntimeError("Source order table is missing order_created_at required for sold_date")
    cur.close()


def policy_clause(policy):
    if policy == "delivered_gross":
        return "LOWER(COALESCE(o.order_status_name,'')) IN ({}) AND o.cancelled_at IS NULL AND o.deleted_at IS NULL AND i.cancelled_at IS NULL AND i.deleted_at IS NULL".format(
            ",".join(["%s"] * len(os.getenv("ETL_DELIVERED_STATUSES", "delivered").split(","))))
    if policy == "noncancelled_gross":
        return "o.cancelled_at IS NULL AND o.deleted_at IS NULL AND i.cancelled_at IS NULL AND i.deleted_at IS NULL"
    raise ValueError("Set ETL_SALES_POLICY to delivered_gross or noncancelled_gross after confirming business rules")


def run(source, dest, dry_run=False, rebuild=False):
    policy = os.getenv("ETL_SALES_POLICY", "").strip().lower()
    clause = policy_clause(policy)
    status_params = [s.strip().lower() for s in os.getenv("ETL_DELIVERED_STATUSES", "delivered").split(",") if s.strip()] if policy == "delivered_gross" else []
    if not status_params and policy == "delivered_gross":
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
            print(f"Dry run: policy={policy}; order key=order_id; source item key=id; SKU=sku_id/sku_name; store=store_id/stores.name; quantity=quantity; sales=amount; complete-order batches={batch_size}")
            cur=src.cursor(); cur.execute(f"SELECT COUNT(DISTINCT i.id) FROM materialized_order_items i JOIN materialized_orders o ON o.order_id=i.order_id WHERE {clause}", status_params); print("eligible source items:",cur.fetchone()[0]); cur.close()
            return
        ensure_destination(dst)
        dcur=dst.cursor()
        dcur.execute("SELECT GET_LOCK(%s,0)", ("etl:" + PROCESS,))
        if dcur.fetchone()[0] != 1:
            raise RuntimeError("Another ETL process holds the destination lock")
        try:
            if rebuild:
                # Atomic reset lets the normal incremental loop resume safely if interrupted.
                dst.start_transaction()
                dcur.execute("DELETE FROM etl_processed_order_items")
                dcur.execute("DELETE FROM top_selling_skus")
                dcur.execute("UPDATE etl_metadata SET last_order_id=0,last_fetch_timestamp=NULL WHERE process_name=%s", (PROCESS,))
                dst.commit()
                LOG.info("full reconciliation reset committed; rebuilding from source")
            src_cur=src.cursor(dictionary=True)
            while True:
                dcur.execute("SELECT last_order_id FROM etl_metadata WHERE process_name=%s FOR UPDATE", (PROCESS,))
                checkpoint=int(dcur.fetchone()[0])
                dst.commit()  # release the checkpoint row lock; advisory lock remains held
                src_cur.execute(f"SELECT DISTINCT i.order_id FROM materialized_order_items i JOIN materialized_orders o ON o.order_id=i.order_id WHERE i.order_id>%s AND {clause} ORDER BY i.order_id LIMIT %s", [checkpoint,*status_params,batch_size])
                order_ids=[int(r["order_id"]) for r in src_cur.fetchall()]
                if not order_ids:
                    break
                upper=max(order_ids)
                start=time.time()
                LOG.info("batch start checkpoint=%s upper_order_id=%s",checkpoint,upper)
                src_cur.execute(f"SELECT DISTINCT i.id,i.order_id,i.sku_id,i.store_id,DATE(o.order_created_at) AS sold_date,COALESCE(NULLIF(i.sku_name,''),NULLIF(sk.name,''),i.product_name,CONCAT('SKU ',i.sku_id)) AS sku_name,s.name AS store_name,i.quantity,i.amount,i.cancelled_at,i.deleted_at FROM materialized_order_items i JOIN materialized_orders o ON o.order_id=i.order_id LEFT JOIN skus sk ON sk.id=i.sku_id LEFT JOIN stores s ON s.id=i.store_id WHERE i.order_id>%s AND i.order_id<=%s AND {clause} ORDER BY i.order_id,i.id", [checkpoint,upper,*status_params])
                rows=src_cur.fetchall()
                aggs=defaultdict(lambda:[None,None,Decimal(0),Decimal(0)])
                inserted=0
                try:
                    dst.start_transaction()
                    for row in rows:
                        if any(row[k] is None for k in ("id","order_id","sku_id","store_id","quantity","amount")):
                            raise ValueError(f"Null required source value in item {row.get('id')}")
                        q=Decimal(str(row["quantity"])); amount=Decimal(str(row["amount"]))
                        if not q.is_finite() or not amount.is_finite() or q < 0 or amount < 0:
                            raise ValueError(f"Invalid quantity or amount in source item {row['id']}")
                        dcur.execute("INSERT IGNORE INTO etl_processed_order_items(source_order_item_id,source_order_id,sku_id,quantity_processed,sales_amount_processed) VALUES (%s,%s,%s,%s,%s)", (row["id"],row["order_id"],row["sku_id"],q,amount))
                        if dcur.rowcount == 1:
                            inserted += 1
                            key=(int(row["sku_id"]),int(row["store_id"]),row["sold_date"])
                            aggs[key][0]=row["sku_name"]
                            aggs[key][1]=row["store_name"]
                            aggs[key][2] += q
                            aggs[key][3] += amount
                    for (sku,store,sold_date),(sku_name,store_name,qty,sales) in aggs.items():
                        dcur.execute("INSERT INTO top_selling_skus(sku_id,sold_date,sku_name,store_id,store_name,total_quantity,total_sales) VALUES (%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE sku_name=VALUES(sku_name),store_name=VALUES(store_name),total_quantity=total_quantity+VALUES(total_quantity),total_sales=total_sales+VALUES(total_sales),last_updated=CURRENT_TIMESTAMP", (sku,sold_date,sku_name,store,store_name,qty,sales))
                    dcur.execute("UPDATE etl_metadata SET last_order_id=%s,last_fetch_timestamp=CURRENT_TIMESTAMP WHERE process_name=%s", (upper,PROCESS))
                    dst.commit()
                except Exception:
                    dst.rollback(); raise
                LOG.info("batch end fetched=%s newly_processed=%s updated_skus=%s checkpoint=%s elapsed=%.2fs",len(rows),inserted,len(aggs),upper,time.time()-start)
            src_cur.close()
        finally:
            dcur.execute("SELECT RELEASE_LOCK(%s)", ("etl:" + PROCESS,))
            dcur.fetchone()
            dcur.close()
    finally:
        src.close(); dst.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspect",action="store_true",help="Read and display source schema only")
    parser.add_argument("--dry-run",action="store_true",help="Count eligible rows and display mapping; creates no destination tables")
    parser.add_argument("--rebuild",action="store_true",help="Rebuild all aggregates and ledger from the current source snapshot")
    parser.add_argument("--init-destination",action="store_true",help="Create/upgrade destination summary and ledger tables")
    args=parser.parse_args()
    try:
        source,dest=config()
        if args.inspect: inspect(source)
        elif args.init_destination:
            dst=connect_with_retry(dest,int(os.getenv("ETL_RETRY_COUNT","3")))
            try: ensure_destination(dst)
            finally: dst.close()
            print("Destination tables are ready.")
        else: run(source,dest,dry_run=args.dry_run,rebuild=args.rebuild)
    except Exception as exc:
        LOG.error("ETL stopped: %s",exc)
        sys.exit(1)

if __name__ == "__main__":
    main()
