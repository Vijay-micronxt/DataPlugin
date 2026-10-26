from __future__ import annotations

import os
import logging
import re
import subprocess
import sys
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any

import mysql.connector
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware


load_dotenv(Path(__file__).resolve().parents[1] / ".env")


app = FastAPI(
    title="StoreNxt Analytics API",
    version="2.0.0",
    description="Date-filtered SKU sales analytics backed by MySQL.",
)
logger = logging.getLogger(__name__)

SYNC_SCRIPT = Path(r"E:\fetching\sync_incremental.py")
SYNC_LOG = Path(r"E:\fetching\logs\incremental_sync.log")
_sync_lock = threading.Lock()
_sync_process: subprocess.Popen | None = None
_sync_log_offset = 0
_sync_state: dict[str, Any] = {
    "status": "idle", "pid": None, "started_at": None, "finished_at": None,
    "return_code": None, "updated_tables": [],
}

origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def connect_database():
    try:
        return mysql.connector.connect(
            host=os.environ["MYSQL_HOST"],
            port=int(os.getenv("MYSQL_PORT", "3306")),
            database=os.environ["MYSQL_DATABASE"],
            user=os.environ["MYSQL_USER"],
            password=os.environ["MYSQL_PASSWORD"],
            connection_timeout=10,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="MySQL is unavailable or its connection settings are missing.",
        ) from exc


class QueryConnection:
    """Keep report handlers independent of the MySQL connector cursor API."""
    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql: str, params=()):
        cursor = self.connection.cursor()
        cursor.execute(sql.replace("?", "%s"), tuple(params))
        return cursor

    def close(self):
        self.connection.close()


def store_filter_sql(column: str, store_ids: list[int] | None) -> tuple[str, list[int]]:
    """Build a parameterized store filter; column is supplied by route code only."""
    if not store_ids:
        return "", []
    placeholders = ", ".join("?" for _ in store_ids)
    return f" AND {column} IN ({placeholders})", store_ids


@app.get("/api/health")
def health() -> dict[str, str]:
    con = QueryConnection(connect_database())
    con.close()
    return {"status": "ok", "database": "ready"}


def current_sync_state() -> dict[str, Any]:
    global _sync_process
    with _sync_lock:
        if _sync_process is not None:
            return_code = _sync_process.poll()
            if return_code is not None and _sync_state["status"] == "running":
                try:
                    with SYNC_LOG.open("rb") as log_file:
                        log_file.seek(_sync_log_offset)
                        new_log = log_file.read().decode("utf-8", errors="replace")
                except OSError:
                    new_log = ""
                _sync_state.update({
                    "status": "succeeded" if return_code == 0 else "failed",
                    "finished_at": datetime.now().astimezone().isoformat(),
                    "return_code": return_code,
                    "updated_tables": updated_tables_from_log(new_log),
                })
        return dict(_sync_state)


def updated_tables_from_log(log_text: str) -> list[str]:
    """Extract destination tables whose ETL output reports positive updates."""
    rules = {
        "etl_top_selling_skus.py": [("top_selling_skus", r"newly_processed=(\d+)", 1)],
        "etl_store_performance.py": [("store_performance", r"newly_processed=(\d+)", 1)],
        "etl_sales_by_food_type.py": [("sales_by_food_type", r"aggregates_updated=(\d+)", 1)],
        "etl_sales_per_hour.py": [
            ("sales_per_hour", r"newly_processed=(\d+)", 1),
            ("store_hourly_sales", r"store_hourly_sales refreshed rows=(\d+)", 1),
        ],
        "etl_top_customers_by_order.py": [("top_customers_by_order", r"customers_updated=(\d+)", 1)],
        "etl_repeat_nonrepeated_customers.py": [("repeat_nonrepeated_customers", r"refreshed identified customers=(\d+)", 1)],
        "etl_store_feedback.py": [("store_feedback", r"feedback_rows=(\d+)", 1)],
        "etl_low_sales_high_inventory.py": [("low_sales_high_inventory", r"Updated (\d+) rows", 1)],
        "etl_high_sales_low_inventory.py": [("high_sales_low_inventory", r"Updated (\d+) rows", 1)],
    }
    updated: list[str] = []
    for script, signals in rules.items():
        marker = re.search(rf"{re.escape(script)} (?:output|log):", log_text)
        if marker is None:
            continue
        next_script = re.search(r"\netl_[\w]+\.py (?:output|log):", log_text[marker.end():])
        end = marker.end() + next_script.start() if next_script else len(log_text)
        section = log_text[marker.end():end]
        for table, pattern, minimum in signals:
            if any(int(match.group(1)) >= minimum for match in re.finditer(pattern, section, re.IGNORECASE)):
                updated.append(table)
    return updated


@app.get("/api/sync/status")
def sync_status() -> dict[str, Any]:
    return current_sync_state()


@app.post("/api/sync/run")
def run_incremental_sync() -> dict[str, Any]:
    global _sync_process, _sync_log_offset
    with _sync_lock:
        if _sync_process is not None and _sync_process.poll() is None:
            raise HTTPException(status_code=409, detail="An incremental sync is already running.")
        if not SYNC_SCRIPT.is_file():
            raise HTTPException(status_code=503, detail=f"Sync script not found: {SYNC_SCRIPT}")
        try:
            _sync_log_offset = SYNC_LOG.stat().st_size if SYNC_LOG.exists() else 0
        except OSError:
            _sync_log_offset = 0
        try:
            _sync_process = subprocess.Popen(
                [sys.executable, str(SYNC_SCRIPT)],
                cwd=str(SYNC_SCRIPT.parent),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise HTTPException(status_code=503, detail="Could not start the incremental sync process.") from exc
        _sync_state.update({
            "status": "running", "pid": _sync_process.pid,
            "started_at": datetime.now().astimezone().isoformat(),
            "finished_at": None, "return_code": None, "updated_tables": [],
        })
        return dict(_sync_state)


@app.get("/api/metadata")
def metadata() -> dict[str, Any]:
    con = QueryConnection(connect_database())
    try:
        row = con.execute(
            "SELECT MIN(sold_date), MAX(sold_date) FROM top_selling_skus"
        ).fetchone()
        stores = con.execute(
            """SELECT store_id, COALESCE(MAX(store_name), 'Unknown store')
               FROM (
                   SELECT store_id, store_name FROM store_performance
                   WHERE store_id IS NOT NULL AND store_id <> 0
                   UNION ALL
                   SELECT store_id, store_name FROM customer_journey WHERE store_id <> 0
               ) AS all_stores
               GROUP BY store_id ORDER BY MAX(store_name), store_id"""
        ).fetchall()
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Could not read sales date coverage.") from exc
    finally:
        con.close()
    return {
        "min_date": row[0],
        "latest_date": row[1],
        "stores": [{"store_id": store[0], "store_name": store[1]} for store in stores],
    }


@app.get("/api/tables")
def list_tables() -> dict[str, Any]:
    """List tables and column metadata in the configured MySQL database."""
    con = QueryConnection(connect_database())
    try:
        rows = con.execute(
            """SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY
               FROM INFORMATION_SCHEMA.COLUMNS
               WHERE TABLE_SCHEMA = ?
               ORDER BY TABLE_NAME, ORDINAL_POSITION""",
            [os.environ["MYSQL_DATABASE"]],
        ).fetchall()
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Could not list MySQL tables.") from exc
    finally:
        con.close()

    tables: dict[str, list[dict[str, Any]]] = {}
    for table_name, column_name, column_type, nullable, key in rows:
        tables.setdefault(table_name, []).append({
            "column_name": column_name,
            "column_type": column_type,
            "is_nullable": nullable == "YES",
            "key": key or None,
        })
    return {"tables": [{"table_name": name, "columns": columns} for name, columns in tables.items()]}


@app.get("/api/tables/{table_name}/rows")
def table_rows(
    table_name: str,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Return a bounded, read-only preview of a table in the configured database."""
    con = QueryConnection(connect_database())
    try:
        exists = con.execute(
            "SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?",
            [os.environ["MYSQL_DATABASE"], table_name],
        ).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="Table not found in the configured MySQL database.")
        safe_name = table_name.replace("`", "``")
        cursor = con.execute(f"SELECT * FROM `{safe_name}` LIMIT ? OFFSET ?", [limit, offset])
        columns = [item[0] for item in cursor.description]
        rows = cursor.fetchall()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Could not read the selected MySQL table.") from exc
    finally:
        con.close()
    return {"table_name": table_name, "columns": columns, "rows": rows, "limit": limit, "offset": offset}


@app.get("/api/reports/top-selling-skus-count")
def top_selling_skus_count(
    start_date: date = Query(..., description="First included date, YYYY-MM-DD"),
    end_date: date = Query(..., description="Last included date, YYYY-MM-DD"),
    chart_limit: int = Query(10, description="Number of SKUs shown in the chart: 5, 10, or 20"),
    store_ids: list[int] | None = Query(None, alias="store_id", description="Optional repeated store filter"),
) -> dict[str, Any]:
    if start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must be on or before end_date")
    if chart_limit not in (5, 10, 20):
        raise HTTPException(status_code=422, detail="chart_limit must be 5, 10, or 20")

    con = QueryConnection(connect_database())
    store_filter, store_params = store_filter_sql("store_id", store_ids)
    daily_store_filter, daily_store_params = store_filter_sql("d.store_id", store_ids)
    try:
        summary = con.execute(
            f"""
            SELECT
                (SELECT COUNT(DISTINCT sku_id) FROM top_selling_skus WHERE sold_date BETWEEN ? AND ?{store_filter}),
                (SELECT COALESCE(SUM(total_quantity), 0) FROM top_selling_skus WHERE sold_date BETWEEN ? AND ?{store_filter}),
                (SELECT COALESCE(SUM(total_orders), 0) FROM store_performance WHERE sold_date BETWEEN ? AND ?{store_filter}),
                (SELECT COALESCE(SUM(total_sales), 0) FROM top_selling_skus WHERE sold_date BETWEEN ? AND ?{store_filter})
            """,
            [start_date, end_date, *store_params, start_date, end_date, *store_params,
             start_date, end_date, *store_params, start_date, end_date, *store_params],
        ).fetchone()
        quantity_rows = con.execute(
            f"""
            SELECT sku_id, COALESCE(MAX(sku_name), 'Unknown SKU') AS sku_name,
                   SUM(COALESCE(total_quantity, 0)) AS total_quantity
            FROM top_selling_skus
            WHERE sold_date BETWEEN ? AND ? AND sku_id IS NOT NULL{store_filter}
            GROUP BY sku_id
            ORDER BY total_quantity DESC, sku_id
            """,
            [start_date, end_date, *store_params],
        ).fetchall()
        quantity_records = [(index, *row) for index, row in enumerate(quantity_rows, 1)]
        value_rows = con.execute(
            f"""
            SELECT sku_id, COALESCE(MAX(sku_name), 'Unknown SKU') AS sku_name,
                   SUM(COALESCE(total_sales, 0)) AS total_sales_value
            FROM top_selling_skus
            WHERE sold_date BETWEEN ? AND ? AND sku_id IS NOT NULL{store_filter}
            GROUP BY sku_id
            ORDER BY total_sales_value DESC, sku_id
            """,
            [start_date, end_date, *store_params],
        ).fetchall()
        value_records = [(index, *row) for index, row in enumerate(value_rows, 1)]

        value_ids = [row[1] for row in value_records[:chart_limit]]
        if value_ids:
            value_placeholders = ", ".join("?" for _ in value_ids)
            trend = con.execute(
                f"""
                SELECT d.sku_id, COALESCE(MAX(d.sku_name), 'Unknown SKU'),
                       d.store_id, COALESCE(MAX(d.store_name), 'Unknown store'),
                       SUM(COALESCE(d.total_sales, 0)) AS total_sales_value
                FROM top_selling_skus d
                WHERE d.sold_date BETWEEN ? AND ? AND d.sku_id IN ({value_placeholders}){daily_store_filter}
                GROUP BY d.sku_id, d.store_id
                ORDER BY d.sku_id, d.store_id
                """,
                [start_date, end_date, *value_ids, *daily_store_params],
            ).fetchall()
        else:
            trend = []

        quantity_ids = [row[1] for row in quantity_records[:chart_limit]]
        if quantity_ids:
            quantity_placeholders = ", ".join("?" for _ in quantity_ids)
            quantity_trend = con.execute(
                f"""
                SELECT d.sku_id, COALESCE(MAX(d.sku_name), 'Unknown SKU'),
                       d.store_id, COALESCE(MAX(d.store_name), 'Unknown store'),
                       SUM(COALESCE(d.total_quantity, 0)) AS total_quantity
                FROM top_selling_skus d
                WHERE d.sold_date BETWEEN ? AND ? AND d.sku_id IN ({quantity_placeholders}){daily_store_filter}
                GROUP BY d.sku_id, d.store_id
                ORDER BY d.sku_id, d.store_id
                """,
                [start_date, end_date, *quantity_ids, *daily_store_params],
            ).fetchall()
        else:
            quantity_trend = []
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("SKU report query failed against MySQL table top_selling_skus")
        raise HTTPException(
            status_code=500,
            detail=f"Could not calculate the SKU ranking from top_selling_skus: {exc}",
        ) from exc
    finally:
        con.close()

    return {
        "start_date": start_date,
        "end_date": end_date,
        "total_skus": summary[0],
        "total_quantity": summary[1],
        "total_orders": summary[2],
        "total_sales_value": summary[3],
        "trend": [
            {"sku_id": row[0], "sku_name": row[1], "store_id": row[2], "store_name": row[3], "sales_value": row[4]}
            for row in trend
        ],
        "quantity_trend": [
            {"sku_id": row[0], "sku_name": row[1], "store_id": row[2], "store_name": row[3], "quantity": row[4]}
            for row in quantity_trend
        ],
        "quantity_records": [
            {"rank": row[0], "sku_id": row[1], "sku_name": row[2], "quantity": row[3]}
            for row in quantity_records
        ],
        "value_records": [
            {"rank": row[0], "sku_id": row[1], "sku_name": row[2], "sales_value": row[3]}
            for row in value_records
        ],
    }


@app.get("/api/reports/store-performance")
def store_performance(
    start_date: date = Query(..., description="First included date, YYYY-MM-DD"),
    end_date: date = Query(..., description="Last included date, YYYY-MM-DD"),
    store_ids: list[int] | None = Query(None, alias="store_id", description="Optional repeated store filter"),
) -> dict[str, Any]:
    if start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must be on or before end_date")
    con = QueryConnection(connect_database())
    store_filter, store_params = store_filter_sql("store_id", store_ids)
    try:
        records = con.execute(
            f"""
            SELECT store_id, COALESCE(MAX(store_name), 'Unknown store'),
                   SUM(COALESCE(total_orders, 0)), NULL,
                   SUM(COALESCE(total_sales, 0)), SUM(COALESCE(total_quantity, 0)),
                   SUM(COALESCE(total_sales, 0)),
                   CASE WHEN SUM(COALESCE(total_orders, 0)) = 0 THEN 0
                        ELSE SUM(COALESCE(total_sales, 0)) / SUM(COALESCE(total_orders, 0)) END
            FROM store_performance
            WHERE sold_date BETWEEN ? AND ? AND store_id IS NOT NULL{store_filter}
            GROUP BY store_id
            ORDER BY SUM(COALESCE(total_sales, 0)) DESC, MAX(store_name)
            """,
            [start_date, end_date, *store_params],
        ).fetchall()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not calculate store performance: {exc}") from exc
    finally:
        con.close()
    return {
        "start_date": start_date, "end_date": end_date,
        "stores": [
            {"store_id": row[0], "store_name": row[1], "total_orders": row[2],
             "total_customers": row[3], "total_order_value": row[4], "total_quantity": row[5],
             "item_sales_value": row[6], "average_order_value": row[7]}
            for row in records
        ],
    }





@app.get("/api/reports/inventory-demand")
def inventory_demand(
    start_date: date = Query(..., description="First included date, YYYY-MM-DD"),
    end_date: date = Query(..., description="Last included date, YYYY-MM-DD"),
    store_ids: list[int] | None = Query(None, alias="store_id", description="Optional repeated store filter"),
) -> dict[str, Any]:
    if start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must be on or before end_date")
    con = QueryConnection(connect_database())
    store_filter, store_params = store_filter_sql("store_id", store_ids)
    try:
        rows = con.execute(
            f"""
            SELECT h.sku_id, MAX(h.sku_name), h.store_id, MAX(h.store_name),
                   MAX(h.inventory_quantity), MAX(h.units_sold_30d),
                   MAX(h.sales_amount_30d), h.snapshot_date
            FROM low_sales_high_inventory h
            JOIN (
                SELECT sku_id, store_id, MAX(snapshot_date) AS snapshot_date
                FROM low_sales_high_inventory
                WHERE snapshot_date BETWEEN ? AND ?{store_filter}
                GROUP BY sku_id, store_id
            ) snap ON snap.sku_id = h.sku_id AND snap.store_id = h.store_id
                 AND snap.snapshot_date = h.snapshot_date
            GROUP BY h.sku_id, h.store_id, h.snapshot_date
            ORDER BY MAX(h.inventory_quantity), h.sku_id, h.store_id
            """,
            [start_date, end_date, *store_params],
        ).fetchall()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not calculate inventory demand: {exc}") from exc
    finally:
        con.close()
    return {
        "start_date": start_date, "end_date": end_date,
        "points": [
            {"sku_id": row[0], "sku_name": row[1] or "Unknown SKU", "store_id": row[2],
             "store_name": row[3] or "Unknown store", "stock_quantity": row[4],
             "total_quantity": row[5], "total_sales_value": row[6], "report_date": row[7]}
            for row in rows
        ],
    }





@app.get("/api/reports/food-type-sales")
def food_type_sales(
    start_date: date = Query(..., description="First included date, YYYY-MM-DD"),
    end_date: date = Query(..., description="Last included date, YYYY-MM-DD"),
    store_ids: list[int] | None = Query(None, alias="store_id", description="Optional repeated store filter"),
) -> dict[str, Any]:
    if start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must be on or before end_date")
    con = QueryConnection(connect_database())
    store_filter, store_params = store_filter_sql("store_id", store_ids)
    try:
        rows = con.execute(
            f"""SELECT COALESCE(NULLIF(TRIM(food_type_name), ''), 'Uncategorized'),
                      SUM(COALESCE(total_quantity, 0)), SUM(COALESCE(total_sales, 0)),
                      SUM(COALESCE(total_orders, 0))
               FROM sales_by_food_type WHERE sold_date BETWEEN ? AND ?{store_filter}
               GROUP BY food_type_id, food_type_name
               ORDER BY SUM(COALESCE(total_sales, 0)) DESC, food_type_name""",
            [start_date, end_date, *store_params],
        ).fetchall()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not calculate food type sales: {exc}") from exc
    finally:
        con.close()
    return {
        "start_date": start_date, "end_date": end_date,
        "food_types": [{"food_type_name": row[0], "total_quantity_sold": row[1],
                         "total_sales_value": row[2], "total_orders": row[3]} for row in rows],
    }





@app.get("/api/reports/hourly-store-sales")
def hourly_store_sales(
    start_date: date = Query(..., description="First included date, YYYY-MM-DD"),
    end_date: date = Query(..., description="Last included date, YYYY-MM-DD"),
    store_ids: list[int] | None = Query(None, alias="store_id", description="Optional repeated store filter"),
) -> dict[str, Any]:
    if start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must be on or before end_date")
    store_filter, store_params = store_filter_sql("store_id", store_ids)
    con = QueryConnection(connect_database())
    try:
        rows = con.execute(
            f"""SELECT hour_of_day, SUM(COALESCE(total_sales_value, 0)), SUM(COALESCE(total_orders, 0))
               FROM store_hourly_sales WHERE sales_date BETWEEN ? AND ?{store_filter}
               GROUP BY hour_of_day ORDER BY hour_of_day""",
            [start_date, end_date, *store_params],
        ).fetchall()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not calculate hourly sales: {exc}") from exc
    finally:
        con.close()
    hour_data = {hour: [0, 0] for hour in range(24)}
    for hour, sales, orders in rows:
        if hour is not None and 0 <= hour < 24:
            hour_data[hour] = [sales or 0, orders or 0]
    return {
        "start_date": start_date, "end_date": end_date,
        "hourly_sales": [{"hour": hour, "total_sales_value": hour_data[hour][0],
                          "total_orders": hour_data[hour][1]} for hour in range(24)],
        "unassigned": {"rows": 0, "total_orders": 0, "total_sales_value": 0},
        "all_time_unassigned_date_rows_included": False,
        "store_filter_supported": True,
    }





@app.get("/api/reports/top-customers")
def top_customers(
    start_date: date = Query(..., description="First included last-order date, YYYY-MM-DD"),
    end_date: date = Query(..., description="Last included last-order date, YYYY-MM-DD"),
    chart_limit: int = Query(10, ge=5, le=20),
    store_ids: list[int] | None = Query(None, alias="store_id", description="Optional repeated store filter"),
) -> dict[str, Any]:
    """Rank customer aggregates by order count and sales value."""
    if start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must be on or before end_date")
    con = QueryConnection(connect_database())
    store_filter, store_params = store_filter_sql("store_id", store_ids)
    try:
        by_orders = con.execute(
            f"""SELECT customer_id, MAX(customer_name) AS customer_name,
                       SUM(total_orders) AS total_orders, SUM(total_quantity) AS total_quantity,
                       SUM(total_sales) AS total_sales, MAX(last_order_date) AS last_order_date
                FROM top_customers_by_order
                WHERE last_order_date BETWEEN ? AND ?{store_filter}
                GROUP BY customer_id
                ORDER BY total_orders DESC, total_sales DESC, customer_id
                LIMIT ?""",
            [start_date, end_date, *store_params, chart_limit],
        ).fetchall()
        by_sales = con.execute(
            f"""SELECT customer_id, MAX(customer_name) AS customer_name,
                       SUM(total_orders) AS total_orders, SUM(total_quantity) AS total_quantity,
                       SUM(total_sales) AS total_sales, MAX(last_order_date) AS last_order_date
                FROM top_customers_by_order
                WHERE last_order_date BETWEEN ? AND ?{store_filter}
                GROUP BY customer_id
                ORDER BY total_sales DESC, total_orders DESC, customer_id
                LIMIT ?""",
            [start_date, end_date, *store_params, chart_limit],
        ).fetchall()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not calculate top customers: {exc}") from exc
    finally:
        con.close()

    def serialize(rows):
        return [{
            "customer_id": row[0], "customer_name": row[1] or "Unknown customer",
            "total_orders": int(row[2] or 0), "total_quantity": float(row[3] or 0),
            "total_sales": float(row[4] or 0),
            "last_order_date": row[5].isoformat() if hasattr(row[5], "isoformat") else str(row[5] or ""),
        } for row in rows]

    return {
        "start_date": start_date, "end_date": end_date,
        "top_by_orders": serialize(by_orders), "top_by_sales": serialize(by_sales),
    }


@app.get("/api/reports/store-hourly-sales")
def store_hourly_sales(
    start_date: date = Query(..., description="First included sales date, YYYY-MM-DD"),
    end_date: date = Query(..., description="Last included sales date, YYYY-MM-DD"),
    store_ids: list[int] | None = Query(None, alias="store_id", description="Optional repeated store filter"),
) -> dict[str, Any]:
    """Return one aggregate per store and hour for a pivot-style sales matrix."""
    if start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must be on or before end_date")
    store_clause, store_params = store_filter_sql("stores.store_id", store_ids)
    hours_sql = " UNION ALL ".join(
        f"SELECT {hour} AS hour_of_day" if hour == 0 else f"SELECT {hour}"
        for hour in range(24)
    )
    con = QueryConnection(connect_database())
    try:
        rows = con.execute(
            f"""SELECT stores.store_id, stores.store_name, hours.hour_of_day,
                      COALESCE(hour_data.total_sales_value, 0),
                      COALESCE(hour_data.total_orders, 0),
                      COALESCE(hour_data.total_quantity, 0)
               FROM (
                   SELECT store_id, MAX(store_name) AS store_name FROM store_performance
                   WHERE store_id IS NOT NULL GROUP BY store_id
               ) AS stores
               CROSS JOIN ({hours_sql}) AS hours
               LEFT JOIN (
                   SELECT store_id, hour_of_day,
                          SUM(COALESCE(total_sales_value, 0)) AS total_sales_value,
                          SUM(COALESCE(total_orders, 0)) AS total_orders,
                          SUM(COALESCE(total_quantity, 0)) AS total_quantity
                   FROM store_hourly_sales
                   WHERE sales_date BETWEEN ? AND ?
                   GROUP BY store_id, hour_of_day
               ) AS hour_data
                 ON hour_data.store_id = stores.store_id AND hour_data.hour_of_day = hours.hour_of_day
               WHERE 1 = 1{store_clause}
               ORDER BY stores.store_name, stores.store_id, hours.hour_of_day""",
            [start_date, end_date, *store_params],
        ).fetchall()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not calculate store hourly sales: {exc}") from exc
    finally:
        con.close()
    return {
        "start_date": start_date, "end_date": end_date,
        "rows": [{
            "store_id": row[0], "store_name": row[1] or f"Store {row[0]}", "hour": int(row[2]),
            "total_sales_value": float(row[3] or 0), "total_orders": int(row[4] or 0),
            "total_quantity": float(row[5] or 0),
        } for row in rows],
    }


@app.get("/api/reports/repeat-nonrepeated-customers")
def repeat_nonrepeated_customers(
    start_date: date = Query(..., description="First included last-order date, YYYY-MM-DD"),
    end_date: date = Query(..., description="Last included last-order date, YYYY-MM-DD"),
    store_ids: list[int] | None = Query(None, alias="store_id", description="Optional repeated store filter"),
) -> dict[str, Any]:
    """Summarize repeat and one-time customer segments from the MySQL aggregate table."""
    if start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must be on or before end_date")
    con = QueryConnection(connect_database())
    store_filter, store_params = store_filter_sql("store_id", store_ids)
    try:
        rows = con.execute(
            f"""SELECT customer_id, SUM(total_orders), SUM(total_quantity), SUM(total_sales)
               FROM repeat_nonrepeated_customers
               WHERE last_order_date BETWEEN ? AND ?{store_filter}
               GROUP BY customer_id""",
            [start_date, end_date, *store_params],
        ).fetchall()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not calculate customer segments: {exc}") from exc
    finally:
        con.close()
    grouped_segments: dict[str, dict[str, Any]] = {}
    for _, total_orders, total_quantity, total_sales in rows:
        segment = "Repeat" if int(total_orders or 0) >= 2 else "One-time"
        aggregate = grouped_segments.setdefault(segment, {
            "customer_segment": segment, "customer_count": 0, "total_orders": 0,
            "total_quantity": 0.0, "total_sales": 0.0,
        })
        aggregate["customer_count"] += 1
        aggregate["total_orders"] += int(total_orders or 0)
        aggregate["total_quantity"] += float(total_quantity or 0)
        aggregate["total_sales"] += float(total_sales or 0)
    segments = sorted(grouped_segments.values(), key=lambda item: (-item["customer_count"], item["customer_segment"]))
    return {"start_date": start_date, "end_date": end_date, "segments": segments}



@app.get("/api/reports/high-inventory-low-sales")
def high_inventory_low_sales(
    start_date: date = Query(..., description="First included snapshot date, YYYY-MM-DD"),
    end_date: date = Query(..., description="Last included snapshot date, YYYY-MM-DD"),
    store_ids: list[int] | None = Query(None, alias="store_id", description="Optional repeated store filter"),
) -> dict[str, Any]:
    """Return high-stock, low-sales rows from the dashboard's 30-day ETL."""
    if start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must be on or before end_date")
    con = QueryConnection(connect_database())
    store_filter, store_params = store_filter_sql("store_id", store_ids)
    try:
        rows = con.execute(
            f"""SELECT sku_id, store_id, snapshot_date, sku_name, store_name,
                       inventory_quantity, min_inventory_quantity, inventory_value,
                       units_sold_30d, sales_amount_30d
                FROM low_sales_high_inventory
                WHERE snapshot_date BETWEEN ? AND ?{store_filter}
                ORDER BY inventory_quantity DESC, sku_name, store_id""",
            [start_date, end_date, *store_params],
        ).fetchall()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not calculate high-inventory low-sales items: {exc}") from exc
    finally:
        con.close()
    candidate_count = len(rows)
    rows = rows[:12]
    return {
        "start_date": start_date, "end_date": end_date, "candidate_count": candidate_count,
        "items": [
            {"report_date": row[2], "sku_id": row[0], "store_id": row[1],
             "store_name": row[4] or "Unknown store", "sku_name": row[3] or "Unknown SKU",
             "stock_quantity": row[5], "low_quantity_alert": row[6], "inventory_value": row[7],
             "sold_quantity_30day": row[8], "sales_value_30day": row[9]}
            for row in rows
        ],
    }


@app.get("/api/reports/inventory-alerts")
def inventory_alerts(
    start_date: date = Query(..., description="First included snapshot date, YYYY-MM-DD"),
    end_date: date = Query(..., description="Last included snapshot date, YYYY-MM-DD"),
    store_ids: list[int] | None = Query(None, alias="store_id", description="Optional repeated store filter"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Return paginated rows from both inventory alert tables."""
    if start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must be on or before end_date")
    store_filter, store_params = store_filter_sql("store_id", store_ids)
    table_specs = {
        "low_sales_high_inventory": ("max_units_sold, min_inventory_quantity", "inventory_quantity DESC, sku_name, store_id"),
        "high_sales_low_inventory": ("min_units_sold, max_inventory_quantity", "units_sold_30d DESC, sku_name, store_id"),
    }
    con = QueryConnection(connect_database())
    result: dict[str, Any] = {}
    try:
        for table_name, (threshold_columns, ordering) in table_specs.items():
            where = f"snapshot_date BETWEEN ? AND ?{store_filter}"
            params = [start_date, end_date, *store_params]
            total = con.execute(
                f"SELECT COUNT(*) FROM `{table_name}` WHERE {where}", params,
            ).fetchone()[0]
            rows = con.execute(
                f"""SELECT sku_id, store_id, snapshot_date, sku_name, store_name,
                           inventory_quantity, inventory_value, units_sold_30d, sales_amount_30d,
                           {threshold_columns}
                    FROM `{table_name}` WHERE {where}
                    ORDER BY {ordering} LIMIT ? OFFSET ?""",
                [*params, limit, offset],
            ).fetchall()
            result[table_name] = {
                "total": int(total or 0), "limit": limit, "offset": offset,
                "rows": [{
                    "sku_id": row[0], "store_id": row[1], "snapshot_date": row[2],
                    "sku_name": row[3] or "Unknown SKU", "store_name": row[4] or "Unknown store",
                    "inventory_quantity": row[5], "inventory_value": row[6],
                    "units_sold_30d": row[7], "sales_amount_30d": row[8],
                    threshold_columns.split(", ")[0]: row[9], threshold_columns.split(", ")[1]: row[10],
                } for row in rows],
            }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not load inventory alert tables: {exc}") from exc
    finally:
        con.close()
    return {"start_date": start_date, "end_date": end_date, **result}


@app.get("/api/reports/store-feedback")
def store_feedback(
    start_date: date = Query(..., description="First included feedback date, YYYY-MM-DD"),
    end_date: date = Query(..., description="Last included feedback date, YYYY-MM-DD"),
    store_ids: list[int] | None = Query(None, alias="store_id", description="Optional repeated store filter"),
    rating: int | None = Query(None, ge=1, le=5),
    search: str = Query("", max_length=200),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Return a review feed and rating summary for the selected stores/date range."""
    if start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must be on or before end_date")
    store_filter, store_params = store_filter_sql("store_id", store_ids)
    base_where = f"feedback_created_at >= ? AND feedback_created_at < DATE_ADD(?, INTERVAL 1 DAY){store_filter}"
    base_params = [start_date, end_date, *store_params]
    details_where = base_where
    details_params = list(base_params)
    if rating is not None:
        details_where += " AND rating = ?"
        details_params.append(rating)
    search_text = search.strip()
    if search_text:
        details_where += " AND (COALESCE(description, '') LIKE ? OR COALESCE(customer_name, '') LIKE ? OR COALESCE(store_name, '') LIKE ?)"
        pattern = f"%{search_text}%"
        details_params.extend([pattern, pattern, pattern])

    con = QueryConnection(connect_database())
    try:
        summary = con.execute(
            f"""SELECT COUNT(*), AVG(rating),
                       SUM(CASE WHEN rating = 1 THEN 1 ELSE 0 END),
                       SUM(CASE WHEN rating = 2 THEN 1 ELSE 0 END),
                       SUM(CASE WHEN rating = 3 THEN 1 ELSE 0 END),
                       SUM(CASE WHEN rating = 4 THEN 1 ELSE 0 END),
                       SUM(CASE WHEN rating = 5 THEN 1 ELSE 0 END)
                FROM store_feedback_details WHERE {base_where}""",
            base_params,
        ).fetchone()
        store_rows = con.execute(
            """SELECT store_id, MAX(store_name), COUNT(*), AVG(rating)
               FROM store_feedback_details
               WHERE feedback_created_at >= ? AND feedback_created_at < DATE_ADD(?, INTERVAL 1 DAY)
               GROUP BY store_id ORDER BY MAX(store_name), store_id""",
            [start_date, end_date],
        ).fetchall()
        total = con.execute(
            f"SELECT COUNT(*) FROM store_feedback_details WHERE {details_where}", details_params,
        ).fetchone()[0]
        rows = con.execute(
            f"""SELECT feedback_id, order_id, store_id, store_name, customer_id, customer_name,
                       rating, description, feedback_created_at
                FROM store_feedback_details WHERE {details_where}
                ORDER BY feedback_created_at DESC, feedback_id DESC LIMIT ? OFFSET ?""",
            [*details_params, limit, offset],
        ).fetchall()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not load store feedback: {exc}") from exc
    finally:
        con.close()

    return {
        "summary": {
            "total": int(summary[0] or 0),
            "average_rating": float(summary[1] or 0),
            "rating_counts": {str(star): int(summary[star + 1] or 0) for star in range(1, 6)},
        },
        "stores": [{
            "store_id": row[0], "store_name": row[1] or f"Store {row[0]}",
            "review_count": int(row[2] or 0), "average_rating": float(row[3] or 0),
        } for row in store_rows],
        "total": int(total or 0), "limit": limit, "offset": offset,
        "reviews": [{
            "feedback_id": row[0], "order_id": row[1], "store_id": row[2],
            "store_name": row[3] or "Unknown store", "customer_id": row[4],
            "customer_name": row[5] or "Customer", "rating": int(row[6] or 0),
            "description": row[7] or "", "feedback_created_at": row[8],
        } for row in rows],
    }


@app.get("/api/reports/customer-journey")
def customer_journey(
    store_ids: list[int] | None = Query(None, alias="store_id", description="Optional repeated store filter"),
) -> dict[str, Any]:
    """Return the latest customer journey snapshot, optionally filtered by store."""
    if store_ids:
        store_filter, store_params = store_filter_sql("store_id", store_ids)
        scope = "selected_stores"
    else:
        store_filter, store_params = " AND store_id = 0", []
        scope = "all_stores"
    con = QueryConnection(connect_database())
    try:
        rows = con.execute(
            f"""SELECT journey_stage, SUM(customer_count), MAX(refreshed_at),
                       COUNT(DISTINCT CASE WHEN store_id <> 0 THEN store_id END),
                       MAX(store_name)
                FROM customer_journey WHERE 1=1{store_filter}
                GROUP BY journey_stage""",
            store_params,
        ).fetchall()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not load customer journey counts: {exc}") from exc
    finally:
        con.close()

    by_stage = {row[0]: row for row in rows}
    refreshed_at = max((row[2] for row in rows if row[2] is not None), default=None)
    store_count = max((int(row[3] or 0) for row in rows), default=0)
    store_name = next((row[4] for row in rows if row[4]), "All Stores")
    return {
        "scope": scope,
        "store_name": store_name,
        "store_count": store_count,
        "refreshed_at": refreshed_at,
        "stages": [
            {"stage": stage, "customer_count": int(by_stage[stage][1] or 0) if stage in by_stage else 0}
            for stage in ("Cart", "Order", "Payment")
        ],
    }

