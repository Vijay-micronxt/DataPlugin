"""Build a date-aware DuckDB analytics database from the D2 MySQL source.

Credentials are read from MYSQL_HOST, MYSQL_PORT, MYSQL_DATABASE,
MYSQL_USER, and MYSQL_PASSWORD. The source connection is read-only in practice:
this script only issues SELECT statements.
"""

from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path
import sys

import duckdb
import mysql.connector
import pandas as pd


CHUNK_SIZE = 100_000
REPORT_TABLES = (
    "sales_item_facts", "sales_by_sku_daily", "top_selling_skus_value",
    "top_selling_skus_count", "store_performance", "food_type_sales",
    "inventory_demand_daily", "low_inventory_high_demand", "high_inventory_low_sales",
    "hourly_sales_summary", "hourly_store_sales", "top_customers", "customer_adoption",
    "customer_7day_repeat_orders", "customer_7day_repeat", "customer_7day_non_returning",
    "customer_7day_food_type_segmentation", "item_level_feedback", "store_level_feedback",
    "store_feedback_summary",
)


def require_env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if not value:
        raise RuntimeError(f"Set {name} in the environment before running this script.")
    return value


def copy_query(mysql_conn, duck_conn, name: str, query: str, schema: str) -> int:
    """Stream one selected source query into a DuckDB table in batches."""
    duck_conn.execute(f"CREATE TABLE {name} ({schema})")
    cur = mysql_conn.cursor(buffered=False)
    cur.execute(query)
    columns = [column[0] for column in cur.description]
    quoted_columns = ", ".join('"' + c.replace('"', '""') + '"' for c in columns)
    insert_sql = f"INSERT INTO {name} ({quoted_columns}) SELECT {quoted_columns} FROM _load_batch"
    count = 0
    while True:
        rows = cur.fetchmany(CHUNK_SIZE)
        if not rows:
            break
        frame = pd.DataFrame.from_records(rows, columns=columns)
        duck_conn.register("_load_batch", frame)
        duck_conn.execute(insert_sql)
        duck_conn.unregister("_load_batch")
        count += len(rows)
        if count and count % 500_000 == 0:
            print(f"  {name}: {count:,} rows", flush=True)
    cur.close()
    print(f"  {name}: {count:,} rows", flush=True)
    return count


def create_source_facts(mysql_conn, duck_conn) -> None:
    print("Loading source facts and safe dimension fields…", flush=True)
    copy_query(mysql_conn, duck_conn, "fact_orders", """
        SELECT o.id AS order_id, DATE(o.order_date) AS report_date, o.order_date AS order_ts,
               o.order_status_id, o.order_status_name,
               o.customer_id, o.store_id, o.total_amount, o.total_payable,
               o.sub_total, o.deleted_at
        FROM orders o
        WHERE o.order_date IS NOT NULL
    """, """
        order_id BIGINT, report_date DATE, order_ts TIMESTAMP,
        order_status_id INTEGER, order_status_name VARCHAR,
        customer_id BIGINT, store_id BIGINT, total_amount DOUBLE, total_payable DOUBLE,
        sub_total DOUBLE, deleted_at TIMESTAMP
    """)

    copy_query(mysql_conn, duck_conn, "fact_order_items", """
        SELECT oi.id AS order_item_id, oi.order_id, o.order_date AS report_ts,
               DATE(o.order_date) AS report_date, o.store_id, o.customer_id,
               oi.sku_id, s.name AS sku_name, s.food_type_id, ft.name AS food_type_name,
               oi.quantity, oi.amount AS sales_value, oi.price, oi.discount_amount,
               oi.cancelled_at, oi.deleted_at AS item_deleted_at,
               o.order_status_id, o.order_status_name, o.deleted_at AS order_deleted_at
        FROM order_items oi
        JOIN orders o ON o.id = oi.order_id
        LEFT JOIN skus s ON s.id = oi.sku_id
        LEFT JOIN food_types ft ON ft.id = s.food_type_id
        WHERE o.order_date IS NOT NULL
    """, """
        order_item_id BIGINT, order_id BIGINT, report_ts TIMESTAMP, report_date DATE,
        store_id BIGINT, customer_id BIGINT, sku_id BIGINT, sku_name VARCHAR,
        food_type_id BIGINT, food_type_name VARCHAR, quantity DOUBLE, sales_value DOUBLE,
        price DOUBLE, discount_amount DOUBLE, cancelled_at TIMESTAMP,
        item_deleted_at TIMESTAMP, order_status_id INTEGER, order_status_name VARCHAR,
        order_deleted_at TIMESTAMP
    """)

    copy_query(mysql_conn, duck_conn, "dim_skus", """
        SELECT id AS sku_id, name AS sku_name, store_id, food_type_id,
               quantity AS current_quantity, low_quantity_alert,
               deleted_at
        FROM skus
    """, """
        sku_id BIGINT, sku_name VARCHAR, store_id BIGINT, food_type_id BIGINT,
        current_quantity DOUBLE, low_quantity_alert DOUBLE, deleted_at TIMESTAMP
    """)
    copy_query(mysql_conn, duck_conn, "dim_stores", """
        SELECT id AS store_id, name AS store_name, owner_id, deleted_at FROM stores
    """, "store_id BIGINT, store_name VARCHAR, owner_id BIGINT, deleted_at TIMESTAMP")
    copy_query(mysql_conn, duck_conn, "dim_food_types", """
        SELECT id AS food_type_id, name AS food_type_name, deleted_at FROM food_types
    """, "food_type_id BIGINT, food_type_name VARCHAR, deleted_at TIMESTAMP")
    # Customer names are sufficient for the requested customer reports. No
    # email, mobile, password, token, or address fields are copied.
    copy_query(mysql_conn, duck_conn, "dim_customers", """
        SELECT id AS customer_id,
               TRIM(CONCAT(COALESCE(first_name, ''), ' ', COALESCE(last_name, ''))) AS customer_name,
               deleted_at
        FROM customers
    """, "customer_id BIGINT, customer_name VARCHAR, deleted_at TIMESTAMP")

    copy_query(mysql_conn, duck_conn, "fact_inventory_snapshots", """
        SELECT ss.snapshot_date AS report_date, ss.sku_id, ss.store_id,
               CAST(ss.quantity AS DOUBLE) AS stock_quantity,
               CAST(ss.inventory_value AS DOUBLE) AS inventory_value
        FROM sku_daily_snapshots ss
        WHERE ss.snapshot_date IS NOT NULL
    """, """
        report_date DATE, sku_id BIGINT, store_id BIGINT, stock_quantity DOUBLE,
        inventory_value DOUBLE
    """)

    copy_query(mysql_conn, duck_conn, "fact_cart_items", """
        SELECT DATE(created_at) AS report_date, created_at AS event_ts,
               id AS cart_item_id, customer_id, store_id, sku_id, quantity,
               is_save_later
        FROM cart_items
        WHERE created_at IS NOT NULL
    """, """
        report_date DATE, event_ts TIMESTAMP, cart_item_id VARCHAR, customer_id BIGINT,
        store_id BIGINT, sku_id BIGINT, quantity DOUBLE, is_save_later BOOLEAN
    """)

    copy_query(mysql_conn, duck_conn, "fact_payments", """
        SELECT DATE(p.created_at) AS report_date, p.created_at AS event_ts,
               p.id AS payment_id, p.order_id, o.customer_id, p.store_id,
               p.amount, p.capture_amount, p.payment_type_name
        FROM order_payments p
        LEFT JOIN orders o ON o.id = p.order_id
        WHERE p.created_at IS NOT NULL
    """, """
        report_date DATE, event_ts TIMESTAMP, payment_id BIGINT, order_id BIGINT,
        customer_id BIGINT, store_id BIGINT, amount DOUBLE, capture_amount DOUBLE,
        payment_type_name VARCHAR
    """)

    copy_query(mysql_conn, duck_conn, "fact_order_status_history", """
        SELECT DATE(h.changed_at) AS report_date, h.changed_at AS event_ts,
               h.order_id, o.customer_id, o.store_id, h.from_status_name,
               h.to_status_name, h.source
        FROM order_status_histories h
        LEFT JOIN orders o ON o.id = h.order_id
        WHERE h.changed_at IS NOT NULL
    """, """
        report_date DATE, event_ts TIMESTAMP, order_id BIGINT, customer_id BIGINT,
        store_id BIGINT, from_status_name VARCHAR, to_status_name VARCHAR, source VARCHAR
    """)

    copy_query(mysql_conn, duck_conn, "fact_store_feedback", """
        SELECT DATE(created_at) AS report_date, created_at AS feedback_ts,
               order_id, customer_id, store_id, owner_id, rating, description
        FROM order_ratings
        WHERE created_at IS NOT NULL
    """, """
        report_date DATE, feedback_ts TIMESTAMP, order_id BIGINT, customer_id BIGINT,
        store_id BIGINT, owner_id BIGINT, rating INTEGER, description VARCHAR
    """)
    copy_query(mysql_conn, duck_conn, "fact_item_feedback", """
        SELECT DATE(created_at) AS report_date, created_at AS feedback_ts,
               order_id, order_item_id, sku_id, customer_id, store_id, owner_id,
               rating, description
        FROM order_item_ratings
        WHERE created_at IS NOT NULL
    """, """
        report_date DATE, feedback_ts TIMESTAMP, order_id BIGINT, order_item_id BIGINT,
        sku_id BIGINT, customer_id BIGINT, store_id BIGINT, owner_id BIGINT,
        rating INTEGER, description VARCHAR
    """)
    duck_conn.execute("CREATE TABLE source_facts_loaded (loaded BOOLEAN)")
    duck_conn.execute("INSERT INTO source_facts_loaded VALUES (TRUE)")


def build_reports(con) -> None:
    print("Building report tables…", flush=True)
    for table in REPORT_TABLES:
        con.execute(f"DROP TABLE IF EXISTS {table}")
    # Use non-deleted, non-canceled order items as sales. Dates remain on every
    # materialized report table so callers can filter by report_date.
    con.execute("""
        CREATE TABLE sales_item_facts AS
        SELECT i.*
        FROM fact_order_items i
        WHERE i.item_deleted_at IS NULL AND i.order_deleted_at IS NULL
          AND i.cancelled_at IS NULL
          AND LOWER(COALESCE(i.order_status_name, '')) NOT LIKE '%cancel%'
          AND LOWER(COALESCE(i.order_status_name, '')) NOT LIKE '%discard%'
    """)

    con.execute("""
        CREATE TABLE sales_by_sku_daily AS
        SELECT report_date, store_id, sku_id, sku_name, food_type_id, food_type_name,
               SUM(COALESCE(quantity, 0)) AS total_quantity,
               SUM(COALESCE(sales_value, 0)) AS total_sales_value,
               COUNT(DISTINCT order_id) AS total_orders,
               COUNT(DISTINCT customer_id) AS total_customers
        FROM sales_item_facts
        GROUP BY ALL
    """)
    con.execute("""
        CREATE TABLE top_selling_skus_value AS
        WITH g AS (
            SELECT report_date, sku_id, sku_name, SUM(total_quantity) AS total_quantity,
                   SUM(total_sales_value) AS total_sales_value,
                   SUM(total_orders) AS total_orders,
                   ROW_NUMBER() OVER (PARTITION BY report_date
                       ORDER BY SUM(total_sales_value) DESC, sku_id) AS rank
            FROM sales_by_sku_daily GROUP BY report_date, sku_id, sku_name
        ) SELECT * FROM g
    """)
    con.execute("""
        CREATE TABLE top_selling_skus_count AS
        WITH g AS (
            SELECT report_date, sku_id, sku_name, SUM(total_quantity) AS total_quantity,
                   SUM(total_sales_value) AS total_sales_value,
                   SUM(total_orders) AS total_orders,
                   ROW_NUMBER() OVER (PARTITION BY report_date
                       ORDER BY SUM(total_quantity) DESC, sku_id) AS rank
            FROM sales_by_sku_daily GROUP BY report_date, sku_id, sku_name
        ) SELECT * FROM g
    """)

    con.execute("""
        CREATE TABLE store_performance AS
        SELECT o.report_date, o.store_id, st.store_name,
               COUNT(DISTINCT o.order_id) AS total_orders,
               COUNT(DISTINCT o.customer_id) AS total_customers,
               COALESCE(SUM(o.total_amount), 0) AS total_order_value,
               COALESCE(SUM(si.total_quantity), 0) AS total_quantity,
               COALESCE(SUM(si.total_sales_value), 0) AS item_sales_value,
               CASE WHEN COUNT(DISTINCT o.order_id) = 0 THEN 0
                    ELSE SUM(o.total_amount) / COUNT(DISTINCT o.order_id) END AS average_order_value
        FROM fact_orders o
        LEFT JOIN dim_stores st ON st.store_id = o.store_id
        LEFT JOIN (
            SELECT report_date, store_id, order_id,
                   SUM(quantity) AS total_quantity, SUM(sales_value) AS total_sales_value
            FROM sales_item_facts GROUP BY report_date, store_id, order_id
        ) si ON si.report_date = o.report_date AND si.store_id = o.store_id
            AND si.order_id = o.order_id
        WHERE o.deleted_at IS NULL
          AND LOWER(COALESCE(o.order_status_name, '')) NOT LIKE '%cancel%'
          AND LOWER(COALESCE(o.order_status_name, '')) NOT LIKE '%discard%'
        GROUP BY o.report_date, o.store_id, st.store_name
    """)

    con.execute("""
        CREATE TABLE food_type_sales AS
        SELECT report_date, food_type_id, COALESCE(food_type_name, 'Uncategorized') AS food_type_name,
               SUM(quantity) AS total_quantity_sold,
               SUM(sales_value) AS total_sales_value,
               COUNT(DISTINCT order_id) AS total_orders,
               COUNT(DISTINCT customer_id) AS total_customers
        FROM sales_item_facts
        GROUP BY report_date, food_type_id, food_type_name
    """)

    # The snapshot table spans about one month in the source. Demand is measured
    # from actual sales in the seven calendar days ending on each snapshot date.
    con.execute("""
        CREATE TABLE inventory_demand_daily AS
        WITH d AS (
            SELECT ss.report_date, ss.sku_id, COALESCE(ss.store_id, s.store_id) AS store_id,
                   s.sku_name, ss.stock_quantity, s.low_quantity_alert, ss.inventory_value,
                   COALESCE(SUM(si.quantity), 0) AS sold_quantity_7day,
                   COALESCE(SUM(si.sales_value), 0) AS sales_value_7day
            FROM fact_inventory_snapshots ss
            LEFT JOIN dim_skus s ON s.sku_id = ss.sku_id
            LEFT JOIN sales_item_facts si
              ON si.sku_id = ss.sku_id AND si.store_id = COALESCE(ss.store_id, s.store_id)
             AND si.report_date BETWEEN ss.report_date - INTERVAL 6 DAY AND ss.report_date
            GROUP BY ss.report_date, ss.sku_id, COALESCE(ss.store_id, s.store_id),
                     s.sku_name, ss.stock_quantity, s.low_quantity_alert, ss.inventory_value
        ), q AS (
            SELECT *,
              QUANTILE_CONT(sold_quantity_7day, 0.75) OVER (PARTITION BY report_date, store_id) AS demand_p75,
              QUANTILE_CONT(sold_quantity_7day, 0.25) OVER (PARTITION BY report_date, store_id) AS demand_p25
            FROM d
        ) SELECT * FROM q
    """)
    con.execute("""
        CREATE TABLE low_inventory_high_demand AS
        SELECT *, 'stock at or below configured low quantity alert; 7-day sales at or above store-day 75th percentile' AS rule
        FROM inventory_demand_daily
        WHERE low_quantity_alert IS NOT NULL AND low_quantity_alert > 0
          AND stock_quantity <= low_quantity_alert AND sold_quantity_7day > 0
          AND sold_quantity_7day >= demand_p75
    """)
    con.execute("""
        CREATE TABLE high_inventory_low_sales AS
        SELECT *, 'stock above configured low quantity alert; 7-day sales at or below store-day 25th percentile' AS rule
        FROM inventory_demand_daily
        WHERE low_quantity_alert IS NOT NULL AND low_quantity_alert > 0
          AND stock_quantity > low_quantity_alert
          AND sold_quantity_7day <= demand_p25
    """)

    con.execute("""
        CREATE TABLE hourly_sales_summary AS
        SELECT report_date, EXTRACT(HOUR FROM order_ts)::INTEGER AS hour,
               COUNT(DISTINCT order_id) AS total_orders,
               COUNT(DISTINCT customer_id) AS total_customers,
               COALESCE(SUM(total_quantity), 0) AS total_quantity_sold,
               COALESCE(SUM(total_sales_value), 0) AS total_sales_value
        FROM (
            SELECT report_date, report_ts AS order_ts, order_id, customer_id,
                   SUM(quantity) AS total_quantity, SUM(sales_value) AS total_sales_value
            FROM sales_item_facts GROUP BY report_date, report_ts, order_id, customer_id
        ) x GROUP BY report_date, hour
    """)
    con.execute("""
        CREATE TABLE hourly_store_sales AS
        SELECT report_date, EXTRACT(HOUR FROM order_ts)::INTEGER AS hour,
               store_id, store_name, COUNT(DISTINCT order_id) AS total_orders,
               COUNT(DISTINCT customer_id) AS total_customers,
               SUM(total_quantity) AS total_quantity_sold,
               SUM(total_sales_value) AS total_sales_value
        FROM (
            SELECT i.report_date, i.report_ts AS order_ts, i.store_id, st.store_name,
                   i.order_id, i.customer_id, SUM(i.quantity) AS total_quantity,
                   SUM(i.sales_value) AS total_sales_value
            FROM sales_item_facts i LEFT JOIN dim_stores st USING (store_id)
            GROUP BY i.report_date, i.report_ts, i.store_id, st.store_name,
                     i.order_id, i.customer_id
        ) x GROUP BY report_date, hour, store_id, store_name
    """)
    con.execute("""
        CREATE TABLE top_customers AS
        WITH g AS (
            SELECT i.report_date, i.customer_id, c.customer_name,
                   COUNT(DISTINCT i.order_id) AS total_orders,
                   SUM(i.quantity) AS total_quantity,
                   SUM(i.sales_value) AS total_sales_value,
                   ROW_NUMBER() OVER (PARTITION BY i.report_date
                       ORDER BY SUM(i.sales_value) DESC, i.customer_id) AS rank
            FROM sales_item_facts i LEFT JOIN dim_customers c USING (customer_id)
            GROUP BY i.report_date, i.customer_id, c.customer_name
        ) SELECT * FROM g WHERE rank <= 10
    """)

    # These are event counts by event date, not a user-level conversion cohort.
    # cart_items only stores current cart rows, so the Cart stage is a limited proxy.
    con.execute("""
        CREATE TABLE customer_adoption AS
        SELECT report_date, 'Cart' AS stage, COUNT(*) AS event_count,
               COUNT(DISTINCT customer_id) AS customer_count,
               'Current cart_items rows by created_at; historical cart events are unavailable' AS definition
        FROM fact_cart_items WHERE COALESCE(is_save_later, FALSE) = FALSE
        GROUP BY report_date
        UNION ALL
        SELECT report_date, 'Order', COUNT(DISTINCT order_id), COUNT(DISTINCT customer_id),
               'Orders created by order_date'
        FROM fact_orders WHERE deleted_at IS NULL GROUP BY report_date
        UNION ALL
        SELECT report_date, 'Payment', COUNT(DISTINCT order_id), COUNT(DISTINCT customer_id),
               'Orders with a positive payment event by payment created_at'
        FROM fact_payments WHERE COALESCE(amount, 0) > 0 GROUP BY report_date
        UNION ALL
        SELECT h.report_date, 'Post-payment status: ' || COALESCE(h.to_status_name, 'Unknown'),
               COUNT(*), COUNT(DISTINCT h.customer_id),
               'Recorded status history events after the first recorded payment for that order'
        FROM fact_order_status_history h
        WHERE EXISTS (
            SELECT 1 FROM fact_payments p
            WHERE p.order_id = h.order_id AND p.event_ts <= h.event_ts AND COALESCE(p.amount, 0) > 0
        )
        GROUP BY h.report_date, h.to_status_name
    """)

    # A seven-day cohort starts on each customer's first valid order date.
    con.execute("""
        CREATE TABLE customer_7day_repeat_orders AS
        WITH firsts AS (
            SELECT customer_id, MIN(report_date) AS cohort_date
            FROM fact_orders
            WHERE customer_id IS NOT NULL AND deleted_at IS NULL
              AND LOWER(COALESCE(order_status_name, '')) NOT LIKE '%cancel%'
              AND LOWER(COALESCE(order_status_name, '')) NOT LIKE '%discard%'
              AND report_date <= (SELECT MAX(report_date) - INTERVAL 6 DAY FROM fact_orders)
            GROUP BY customer_id
        ), cohorts AS (
            SELECT f.cohort_date, f.customer_id, c.customer_name,
                   COUNT(DISTINCT o.order_id) AS total_orders_7day,
                   GREATEST(COUNT(DISTINCT o.order_id) - 1, 0) AS repeat_orders_7day,
                   SUM(COALESCE(o.total_amount, 0)) AS total_sales_7day
            FROM firsts f
            LEFT JOIN fact_orders o ON o.customer_id = f.customer_id
              AND o.report_date BETWEEN f.cohort_date AND f.cohort_date + INTERVAL 6 DAY
              AND o.deleted_at IS NULL
              AND LOWER(COALESCE(o.order_status_name, '')) NOT LIKE '%cancel%'
              AND LOWER(COALESCE(o.order_status_name, '')) NOT LIKE '%discard%'
            LEFT JOIN dim_customers c ON c.customer_id = f.customer_id
            GROUP BY f.cohort_date, f.customer_id, c.customer_name
        )
        SELECT cohort_date AS report_date, customer_id, customer_name,
               total_orders_7day, repeat_orders_7day, total_sales_7day,
               CASE WHEN repeat_orders_7day > 0 THEN 'repeat' ELSE 'non_returning' END AS customer_status,
               CASE WHEN total_orders_7day = 0 THEN 0
                    ELSE repeat_orders_7day::DOUBLE / total_orders_7day END AS repeat_order_rate,
               'Orders from first order date through the following six calendar days' AS period_definition
        FROM cohorts
    """)
    con.execute("""
        CREATE TABLE customer_7day_repeat AS
        SELECT * FROM customer_7day_repeat_orders WHERE repeat_orders_7day > 0
    """)
    con.execute("""
        CREATE TABLE customer_7day_non_returning AS
        SELECT * FROM customer_7day_repeat_orders WHERE repeat_orders_7day = 0
    """)
    con.execute("""
        CREATE TABLE customer_7day_food_type_segmentation AS
        WITH firsts AS (
            SELECT customer_id, MIN(report_date) AS cohort_date
            FROM fact_orders
            WHERE customer_id IS NOT NULL AND deleted_at IS NULL
              AND LOWER(COALESCE(order_status_name, '')) NOT LIKE '%cancel%'
              AND LOWER(COALESCE(order_status_name, '')) NOT LIKE '%discard%'
              AND report_date <= (SELECT MAX(report_date) - INTERVAL 6 DAY FROM fact_orders)
            GROUP BY customer_id
        )
        SELECT f.cohort_date AS report_date, i.customer_id,
               COALESCE(i.food_type_name, 'Uncategorized') AS food_type_name,
               SUM(i.quantity) AS total_items_7day, SUM(i.sales_value) AS total_sales_7day,
               COUNT(DISTINCT i.order_id) AS total_orders_7day
        FROM firsts f JOIN sales_item_facts i ON i.customer_id = f.customer_id
          AND i.report_date BETWEEN f.cohort_date AND f.cohort_date + INTERVAL 6 DAY
        GROUP BY f.cohort_date, i.customer_id, food_type_name
    """)

    con.execute("""
        CREATE TABLE item_level_feedback AS
        SELECT f.report_date, f.feedback_ts, f.order_id, f.order_item_id, f.sku_id,
               s.sku_name, f.customer_id, f.store_id, st.store_name, f.rating, f.description
        FROM fact_item_feedback f
        LEFT JOIN dim_skus s ON s.sku_id = f.sku_id
        LEFT JOIN dim_stores st ON st.store_id = f.store_id
    """)
    con.execute("""
        CREATE TABLE store_level_feedback AS
        SELECT f.report_date, f.feedback_ts, f.order_id, f.customer_id,
               c.customer_name, f.store_id, st.store_name, f.rating, f.description
        FROM fact_store_feedback f
        LEFT JOIN dim_customers c USING (customer_id)
        LEFT JOIN dim_stores st USING (store_id)
    """)
    con.execute("""
        CREATE TABLE store_feedback_summary AS
        SELECT report_date, store_id, store_name, COUNT(*) AS total_feedback,
               COUNT(DISTINCT customer_id) AS total_customers,
               AVG(rating) AS average_rating,
               COUNT(*) FILTER (WHERE rating = 5) AS five_star_count,
               COUNT(*) FILTER (WHERE rating = 4) AS four_star_count,
               COUNT(*) FILTER (WHERE rating = 3) AS three_star_count,
               COUNT(*) FILTER (WHERE rating = 2) AS two_star_count,
               COUNT(*) FILTER (WHERE rating = 1) AS one_star_count
        FROM store_level_feedback GROUP BY report_date, store_id, store_name
    """)

    # Helpful indexes for common date-filtered drilldowns.
    for table in (
        "sales_item_facts", "sales_by_sku_daily", "store_performance",
        "food_type_sales", "inventory_demand_daily", "customer_adoption",
        "customer_7day_repeat_orders", "item_level_feedback", "store_level_feedback",
    ):
        con.execute(f"CREATE INDEX idx_{table}_date ON {table}(report_date)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="storenxt_d2_analytics.duckdb", help="Output DuckDB path")
    args = parser.parse_args()
    output_path = Path(args.output).resolve()
    temp_path = output_path.with_name(output_path.stem + ".building.duckdb")
    resume = False
    if temp_path.exists():
        probe = duckdb.connect(str(temp_path))
        resume = "source_facts_loaded" in {row[0] for row in probe.execute("SHOW TABLES").fetchall()}
        probe.close()
        if not resume:
            temp_path.unlink()
    duck_conn = duckdb.connect(str(temp_path))
    mysql_conn = None
    try:
        duck_conn.execute("SET threads TO 4")
        if not resume:
            mysql_password = os.environ.get("MYSQL_PASSWORD") or getpass.getpass("D2 MySQL password: ")
            mysql_conn = mysql.connector.connect(
                host=require_env("MYSQL_HOST"),
                port=int(require_env("MYSQL_PORT", "3306")),
                database=require_env("MYSQL_DATABASE"),
                user=require_env("MYSQL_USER"),
                password=mysql_password,
                connection_timeout=15,
                autocommit=True,
            )
            create_source_facts(mysql_conn, duck_conn)
            mysql_conn.close()
            mysql_conn = None
        else:
            print("Resuming from the completed source import…", flush=True)
        build_reports(duck_conn)
        duck_conn.execute("CHECKPOINT")
        tables = duck_conn.execute("SHOW TABLES").fetchall()
        print(f"Created {len(tables)} tables in {temp_path}", flush=True)
        print("Date ranges:", flush=True)
        for table in ("sales_item_facts", "inventory_demand_daily", "customer_adoption", "store_level_feedback"):
            print(table, duck_conn.execute(f"SELECT MIN(report_date), MAX(report_date), COUNT(*) FROM {table}").fetchone(), flush=True)
        print("Item feedback source rows:", duck_conn.execute("SELECT COUNT(*) FROM item_level_feedback").fetchone()[0], flush=True)
    except Exception:
        duck_conn.close()
        if mysql_conn is not None:
            mysql_conn.close()
        raise
    else:
        duck_conn.close()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()
    temp_path.replace(output_path)
    print(f"Ready: {output_path}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        sys.exit(1)
