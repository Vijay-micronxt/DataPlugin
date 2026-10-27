# StoreNxt D2 analytics DuckDB

`storenxt_d2_analytics.duckdb` is a new, date-aware analytics database built from the D2 MySQL source. The original `store_next.duckdb` is unchanged.

## Build or refresh

Install `duckdb`, `mysql-connector-python`, and `pandas` for Python, then set the non-secret connection fields in PowerShell. The builder prompts for the password if `MYSQL_PASSWORD` is not set.

```powershell
$env:MYSQL_HOST = "your_mysql_host"
$env:MYSQL_PORT = "3306"
$env:MYSQL_DATABASE = "your_source_database"
$env:MYSQL_USER = "your_mysql_user"
python .\build_d2_analytics.py
```

The script reads only the fields needed for the reports. It does not copy customer contact details, credentials, store API secrets, or payment credentials. Output is written to `storenxt_d2_analytics.duckdb`.

## Run a date-filtered query

All report and fact tables use `report_date` (the customer cohort tables use the cohort start date). Date bounds are inclusive.

```powershell
python .\query_d2_analytics.py top_selling_skus_value --from-date 2026-10-01 --to-date 2026-10-17
python .\query_d2_analytics.py food_type_sales --from-date 2026-10-01 --to-date 2026-10-17
```

The query utility accepts any table containing `report_date` or `cohort_date`, and emits JSON rows. In Python, the same filter is:

```python
import duckdb

con = duckdb.connect("storenxt_d2_analytics.duckdb", read_only=True)
rows = con.execute(
    "SELECT * FROM sales_by_sku_daily WHERE report_date BETWEEN ? AND ?",
    ["2026-10-01", "2026-10-17"],
).fetchdf()
```

## Tables

- Sales and rank reports: `sales_item_facts`, `sales_by_sku_daily`, `top_selling_skus_value`, `top_selling_skus_count`, `store_performance`, `food_type_sales`.
- Inventory reports: `fact_inventory_snapshots`, `inventory_demand_daily`, `low_inventory_high_demand`, `high_inventory_low_sales`.
- Peak-hour reports: `hourly_sales_summary`, `hourly_store_sales`, `top_customers`.
- Funnel and customer reports: `fact_cart_items`, `fact_payments`, `fact_order_status_history`, `customer_adoption`, `customer_7day_repeat_orders`, `customer_7day_repeat`, `customer_7day_non_returning`, `customer_7day_food_type_segmentation`.
- Feedback reports: `item_level_feedback`, `store_level_feedback`, `store_feedback_summary`.
- Safe dimensions: `dim_skus`, `dim_stores`, `dim_food_types`, `dim_customers`.

## Metric definitions and source limits

- Sales use non-deleted, non-cancelled order items and sum the source `order_items.amount`; daily rankings are ranked within each day. Aggregate `sales_by_sku_daily` over the selected dates and rerank by the sum when a period-wide top list is needed.
- Store performance excludes cancelled and discarded orders. Item sales and order header totals are both retained because they can differ.
- Low inventory means stock is at or below the SKU's configured low-stock alert. High demand is at or above the store/date 75th percentile of seven-day unit sales. High inventory means stock exceeds that alert; low sales means seven-day units at or below the 25th percentile. Rows without a configured alert are not classified.
- Inventory snapshots cover 2026-09-17 through 2026-10-18 in the source. Sales facts cover 2022-05-23 through 2026-10-17.
- Cart rows come from `cart_items`, which contains only retained cart rows, not a complete historical cart event log. The funnel therefore labels Cart as a limited proxy. Funnel rows are event counts by event date, not a same-customer conversion cohort. Post-payment stages use the recorded order status history; only 69 such source events were present.
- Customer repeat reports use each customer's first valid order as the start of a seven-calendar-day cohort. The most recent six days are omitted because they have not had a full seven-day follow-up window.
- Store feedback comes from `order_ratings` (65 source rows). `order_item_ratings` currently has no rows, so the item feedback table is created but empty until item ratings are recorded.
