# Top selling SKU ETL

This project reads from the configured D2 MySQL database and writes aggregates to the separate destination database. `etl_top_selling_skus.py` builds `top_selling_skus`, grouped by SKU, store, and sold date. `etl_store_performance.py` independently builds `store_performance`, grouped by store and sold date, with units sold, sales total, and distinct order count. `etl_sales_by_food_type.py` builds daily food-type/store sales totals, units, and order counts. `etl_sales_per_hour.py` builds daily sales totals by hour across all stores and refreshes `store_hourly_sales` by store/date/hour. `etl_top_customers_by_order.py` summarizes delivered order activity by customer; `etl_repeat_nonrepeated_customers.py` classifies identified customers as repeat or one-time buyers. `etl_customer_journey.py` builds Cart, Order, and Payment customer counts by store for a funnel chart. `etl_store_feedback.py` summarizes customer order ratings by store. `etl_low_sales_high_inventory.py` and `etl_high_sales_low_inventory.py` build inventory reports from the latest inventory snapshot. None of these ETLs writes to the source.

## Source inspection findings

The inspected source schema is `materialized_orders` plus `materialized_order_items`. `materialized_order_items.id` is the primary key and is used as the stable ledger key; `order_id` joins to `materialized_orders.order_id`; `sku_id`, `sku_name`, `store_id`, `quantity`, and `amount` provide the row details. `sold_date` is `DATE(materialized_orders.order_created_at)`. Missing row SKU names fall back to `skus.name`; store names come from `stores.name`. The source amount is a `DOUBLE`, and it matched `quantity * selling_price` for 12,865 of 12,866 inspected rows. The ETL uses `amount` directly and does not multiply it again.

The source has order and item cancellation/deletion fields. `materialized_orders.return_amount` and `return_item_tax_amount` are order-level; these tables do not provide a reliable SKU-level return allocation. Therefore the selected policy includes delivered gross line amounts and does not subtract returns. If a SKU-level return source becomes available, reconcile using `--rebuild` after extending the mapping. The `order_status_name` field was selected for delivered status because status IDs/names are inconsistent in the inspected rows.

## Setup

1. Install dependencies: `python -m pip install -r requirements.txt`
2. Keep the existing `.env` source settings. Add destination settings (`DEST_HOST`, `DEST_PORT`, `DEST_DATABASE`, `DEST_USER`, `DEST_PASSWORD`). Do not commit `.env`; it is ignored by `.gitignore`.
3. Set `ETL_SALES_POLICY=delivered_gross` and optionally `ETL_DELIVERED_STATUSES=delivered` (comma-separated lowercase names).
4. Optional: set `ETL_BATCH_SIZE` (default 500 complete orders) and `ETL_RETRY_COUNT` (default 3).
5. Low sales/high inventory defaults: no more than 5 units sold over the trailing 30 calendar days, and at least 20 units in the latest inventory snapshot. Override with `LOW_SALES_WINDOW_DAYS`, `LOW_SALES_MAX_UNITS`, and `HIGH_INVENTORY_MIN_UNITS`.
6. High sales/low inventory defaults: at least 20 units sold over the trailing 30 calendar days, and no more than 5 units in the latest inventory snapshot. Override with `HIGH_SALES_WINDOW_DAYS`, `HIGH_SALES_MIN_UNITS`, and `LOW_INVENTORY_MAX_UNITS`. `LOW_INVENTORY_BATCH_SIZE` controls source fetch batches for both inventory reports (default 2000).
7. Store-hourly reports convert the verified UTC source timestamp to India Standard Time (`ETL_REPORT_UTC_OFFSET=+05:30`). Set another fixed UTC offset there if the stores use a different timezone.

## Commands

```powershell
python etl_top_selling_skus.py --inspect
python etl_top_selling_skus.py --dry-run
python etl_top_selling_skus.py
python etl_top_selling_skus.py --rebuild
python etl_store_performance.py --dry-run
python etl_store_performance.py --rebuild
python etl_sales_by_food_type.py --dry-run
python etl_sales_by_food_type.py --rebuild
python etl_sales_per_hour.py --dry-run
python etl_sales_per_hour.py --rebuild
python etl_top_customers_by_order.py --dry-run
python etl_top_customers_by_order.py --rebuild
python etl_repeat_nonrepeated_customers.py --dry-run
python etl_repeat_nonrepeated_customers.py
python etl_customer_journey.py --dry-run
python etl_customer_journey.py
python etl_store_feedback.py
python export_dashboard_to_duckdb.py --dry-run
python export_dashboard_to_duckdb.py
python etl_low_sales_high_inventory.py --dry-run
python etl_low_sales_high_inventory.py --init-destination
python etl_low_sales_high_inventory.py
python etl_high_sales_low_inventory.py --dry-run
python etl_high_sales_low_inventory.py --init-destination
python etl_high_sales_low_inventory.py
python sync_incremental.py
python sync_incremental.py --watch --interval-minutes 5
python -m unittest discover -s tests -v
```

## Run from any folder or move the ETL bundle

Every ETL locates `.env` next to its own Python file, and `sync_incremental.py` locates the ETL scripts next to itself. From the repository root, launch the sync with `python .\fetching\sync_incremental.py`; from another folder, provide the path to this repository's `fetching/sync_incremental.py`. The scripts use the Python interpreter that launches the sync. Create a local `fetching/.env` with the source and destination database settings and install `fetching/requirements.txt`.

`sync_incremental.py` runs all ten report ETLs once, including the customer journey and store feedback reports, and `--watch` repeats them every five minutes until stopped with Ctrl+C. Run it from a machine/session that can reach the configured source and destination MySQL servers. The inventory reports reread the latest inventory snapshot on every sync and may take longer than the order-based incremental reports. A failed ETL is logged, and the wrapper continues with the remaining ETLs before returning a failure code. Logs go to `logs/incremental_sync.log`.

`--dry-run` displays the source mapping and counts eligible items without creating or changing destination data. A normal run creates the destination tables, reads complete orders after the committed order-ID checkpoint, and commits ledger rows, daily SKU/store deltas, and checkpoint in one transaction per batch. A MySQL advisory lock serializes ETL runs. The unique ledger key prevents replays from counting an item twice.

For the SKU ETL, `--rebuild` clears its destination ledger and summary, resets its checkpoint, and reloads the current source snapshot. The store ETL has its own ledger and checkpoint; its `--rebuild` only clears and reloads `store_performance`. Run rebuild periodically to reconcile changed items and late-arriving items at/below a checkpoint. A rebuild is restart-safe: if interrupted, run it again. Without source change tracking, incremental mode cannot detect edits to already processed rows or old-order late arrivals. A rebuild reflects current source rows but cannot recover deleted history or determine SKU-level returns.

## Store feedback

`etl_store_feedback.py` refreshes one row per source store in `store_feedback`. It summarizes `order_ratings` only (the inspected `order_item_ratings` table is empty), counts ratings from 1 through 5, calculates the average rating and latest feedback timestamp, and includes stores with no valid ratings (`feedback_count=0`, `average_rating=NULL`). Each individual rating is also copied to `store_feedback_details`, including the customer's name and ID, `description` text, order, store, rating, and timestamp. Customer names are built from the customer's first and last name when available. Blank descriptions are stored as `NULL`; feedback records without a written comment are still included. Both tables are full refreshes in one transaction, so repeated runs do not duplicate feedback. The ETL runs as part of `sync_incremental.py`.

```sql
SELECT store_id,store_name,feedback_count,average_rating,
       rating_1_count,rating_2_count,rating_3_count,rating_4_count,rating_5_count,
       latest_feedback_at
FROM store_feedback
ORDER BY average_rating DESC,feedback_count DESC;

SELECT store_id,store_name,order_id,customer_id,customer_name,rating,description,feedback_created_at
FROM store_feedback_details
WHERE description IS NOT NULL
ORDER BY feedback_created_at DESC;
```

## Filter by date

Each row in `top_selling_skus` represents one SKU/store/day. Filter a date range with inclusive bounds:

```sql
SELECT sku_id,sold_date,sku_name,store_id,store_name,total_quantity,total_sales
FROM top_selling_skus
WHERE sold_date BETWEEN '2026-01-01' AND '2026-01-31'
ORDER BY total_sales DESC;
```

## Verify results

```sql
SELECT sku_id,sold_date,sku_name,store_id,store_name,total_quantity,total_sales FROM top_selling_skus ORDER BY total_sales DESC LIMIT 10;
SELECT store_id,sold_date,store_name,total_quantity,total_sales,total_orders FROM store_performance ORDER BY total_sales DESC LIMIT 10;
SELECT process_name,last_order_id,last_fetch_timestamp FROM etl_metadata;
SELECT COUNT(*) ledger_items,MAX(processed_at) latest_item FROM etl_processed_order_items;
```

Compare to a source calculation using the same `order_status_name='Delivered'`/`amount` policy. Confirm source item row identity and status rules before changing the SQL. `last_fetch_timestamp` changes only in the successful batch transaction.

## Sales by food type

`sales_by_food_type` groups delivered sales by the SKU's `food_type_id` (`skus.food_type_id` joined to `food_types.id`), store, and sold date. It includes quantity, sales amount, and distinct order count. Rows without a food type are retained as ID `0`, named `Unclassified`, so totals are not dropped. Its ETL uses its own order checkpoint and duplicate-prevention ledger. It is included in `sync_incremental.py` and therefore runs with the other sales ETLs in watch mode.

```sql
SELECT food_type_id,food_type_name,store_id,store_name,sold_date,total_quantity,total_sales,total_orders
FROM sales_by_food_type
WHERE sold_date BETWEEN '2026-10-01' AND '2026-10-31'
ORDER BY total_sales DESC;

SELECT process_name,last_order_id,last_fetch_timestamp
FROM etl_metadata WHERE process_name='sales_by_food_type_v1';
```

## Sales per hour

`sales_per_hour` groups all delivered sales across all stores into one row per calendar date and hour (0 through 23), based on `materialized_orders.order_created_at`. It contains total units, sales amount, distinct order count, and order-item count. It uses the source session timezone (verified UTC). This ETL has its own checkpoint and duplicate-prevention ledger and runs through `sync_incremental.py`.

```sql
SELECT sold_date,sold_hour,total_quantity,total_sales,total_orders,total_order_items
FROM sales_per_hour
WHERE sold_date BETWEEN '2026-10-01' AND '2026-10-31'
ORDER BY sold_date,sold_hour;

SELECT process_name,last_order_id,last_fetch_timestamp
FROM etl_metadata WHERE process_name='sales_per_hour_v1';
```

The same ETL also refreshes `store_hourly_sales`: one normalized row per store, local sales date, and local hour. It includes `store_name`, looked up from D2's `stores.name`; if a name is missing, it stores a readable `Store <store_id>` fallback. It uses `materialized_order_items.store_id`, `materialized_orders.order_created_at`, and `materialized_order_items.amount` and `quantity`; `COUNT(DISTINCT order_id)` gives the order count. The source session timezone was verified as UTC and the default output timezone is India Standard Time (+05:30). It deduplicates by stable order-item ID, applies the delivered/cancellation/deletion rules, and atomically replaces the complete summary from current eligible D2 rows after the incremental hourly sales load succeeds. This full refresh reflects changes, cancellations, and late arrivals without adding a previous total twice. No destination store dimension exists for a valid foreign key, so store IDs remain unsigned integers.

The aggregation is `SUM(item.amount)` for sales, `SUM(item.quantity)` for units, and `COUNT(DISTINCT order_id)` for orders, grouped by `item.store_id`, the converted `order_created_at` date, and hour. The MySQL server is version 5.7, which parses but does not enforce `CHECK` constraints; the ETL validates every hour is 0–23 before writing, and the table's composite primary key prevents duplicate store/date/hour rows.

```sql
SELECT * FROM store_hourly_sales
ORDER BY sales_date DESC,store_id,hour_of_day
LIMIT 100;

SELECT store_id,sales_date,hour_of_day,COUNT(*) AS row_count
FROM store_hourly_sales
GROUP BY store_id,sales_date,hour_of_day
HAVING COUNT(*)>1;

SELECT store_id,sales_date,SUM(total_sales_value) AS daily_sales
FROM store_hourly_sales
WHERE sales_date='2026-10-23'
GROUP BY store_id,sales_date;

-- Independent source aggregation for comparison (run against D2)
SELECT store_id,sales_date,hour_of_day,ROUND(SUM(amount),2) AS total_sales_value,
       COUNT(DISTINCT order_id) AS total_orders,ROUND(SUM(quantity),4) AS total_quantity
FROM (
  SELECT DISTINCT i.id AS item_id,i.order_id,i.store_id,
         DATE(CONVERT_TZ(o.order_created_at,@@session.time_zone,'+05:30')) AS sales_date,
         HOUR(CONVERT_TZ(o.order_created_at,@@session.time_zone,'+05:30')) AS hour_of_day,
         i.amount,i.quantity
  FROM materialized_order_items i
  JOIN materialized_orders o ON o.order_id=i.order_id
  WHERE LOWER(COALESCE(o.order_status_name,''))='delivered'
    AND o.cancelled_at IS NULL AND o.deleted_at IS NULL
    AND i.cancelled_at IS NULL AND i.deleted_at IS NULL
) AS eligible_items
GROUP BY store_id,sales_date,hour_of_day;
```

## Top customers by order

`top_customers_by_order` contains one cumulative row per customer and store, with `store_id` and `store_name`. Order counts and sales are calculated for that store. Orders without a customer ID are kept together per store in customer ID `0`, named `Guest / unidentified`; filter `is_identified_customer=1` to rank actual identified customers. Sales amounts use the same delivered item `amount` policy as the other sales reports. Because the table's grain changed, run `python etl_top_customers_by_order.py --rebuild` once to recreate the store-level totals; then run `python etl_repeat_nonrepeated_customers.py` to refresh the segments. The ETL has its own checkpoint and duplicate-prevention ledger and runs through `sync_incremental.py`.

```sql
SELECT customer_id,customer_name,store_id,store_name,total_orders,total_quantity,total_sales,first_order_date,last_order_date
FROM top_customers_by_order
WHERE is_identified_customer=1
ORDER BY total_orders DESC,total_sales DESC
LIMIT 20;

SELECT * FROM top_customers_by_order WHERE is_identified_customer=0;
SELECT process_name,last_order_id,last_fetch_timestamp
FROM etl_metadata WHERE process_name='top_customers_by_order_v1';
```

## Repeat and one-time customers

`repeat_nonrepeated_customers` refreshes from `top_customers_by_order` after that incremental ETL runs. It contains one row per identified customer and store, including `store_id` and `store_name`. A customer with 2 or more delivered orders at that store is `Repeat`; a customer with exactly 1 delivered order there is `One-time`. The pooled `Guest / unidentified` rows are excluded because their orders cannot be attributed to distinct people. This is a full, atomic refresh and is included in `sync_incremental.py` immediately after the customer summary.

```sql
SELECT customer_id,customer_name,store_id,store_name,customer_segment,total_orders,total_sales,first_order_date,last_order_date
FROM repeat_nonrepeated_customers
ORDER BY customer_segment,total_orders DESC;

SELECT * FROM repeat_nonrepeated_customers_metadata;
```

## Customer journey funnel

`customer_journey` contains one row per store and stage (`Cart`, `Order`, `Payment`), with `customer_count` for the chart. Store ID `0` is the all-store total; other rows support the UI's store filter. Cart counts use identified customers with current cart items (including saved-for-later items); Order counts use identified customers with non-cancelled, non-deleted orders; Payment counts use identified customers with a captured payment amount above zero on those orders. The ETL fully refreshes the snapshot and runs in `sync_incremental.py`. Cart is a current-state count, while orders and payments include available history, so these totals show stage reach rather than a same-session conversion rate.

```sql
SELECT journey_stage,customer_count
FROM customer_journey
WHERE store_id=123
ORDER BY FIELD(journey_stage,'Cart','Order','Payment');

-- All stores
SELECT journey_stage,customer_count
FROM customer_journey
WHERE store_id=0
ORDER BY FIELD(journey_stage,'Cart','Order','Payment');
```

## Export dashboard tables to DuckDB

`export_dashboard_to_duckdb.py` copies every base table in the configured destination MySQL database into a local DuckDB database, including dashboard reports, ETL metadata, and processing ledgers. Each export replaces matching tables in DuckDB in one transaction. Install the added dependency with `python -m pip install -r requirements.txt`. The default file is `dashboard.duckdb` in this project folder; use `--output` or `DUCKDB_PATH` to choose another path.

```powershell
python export_dashboard_to_duckdb.py --dry-run
python export_dashboard_to_duckdb.py
python export_dashboard_to_duckdb.py --output "E:\\data\\dashboard.duckdb"
```

## Low sales and high inventory

Run the sales ETL before this report so `top_selling_skus` contains the latest delivered sales. The report uses the newest date in `sku_daily_snapshots` and compares each SKU/store inventory quantity with sales during the 30 calendar days ending on that snapshot date (inclusive). The default thresholds are at most 5 units sold and at least 20 units in stock. A no-sales SKU counts as zero sold. Each run replaces that snapshot date's report rows in one transaction and keeps prior snapshot dates for history. `low_sales_high_inventory_metadata` records the last successfully processed snapshot and the thresholds. Run this script daily, or whenever a new inventory snapshot is available.

```sql
SELECT sku_id,sku_name,store_id,store_name,snapshot_date,inventory_quantity,inventory_value,
       sales_window_start,sales_window_end,units_sold_30d,sales_amount_30d
FROM low_sales_high_inventory
WHERE snapshot_date BETWEEN '2026-10-01' AND '2026-10-31'
ORDER BY inventory_quantity DESC;

SELECT * FROM low_sales_high_inventory_metadata;
```

## High sales and low inventory

Run the sales ETL before this report. It uses the latest `sku_daily_snapshots` date and compares each SKU/store's inventory with delivered sales during the 30 calendar days ending on that date (inclusive). Defaults are at least 20 units sold and at most 5 units in stock. Each run replaces that snapshot date's rows and keeps prior snapshot dates for history. `high_sales_low_inventory_metadata` records the last successfully processed snapshot and thresholds. If a high-selling SKU/store pair has no inventory snapshot row, it is included with `inventory_snapshot_found=0` and NULL inventory values so missing data is visible without falsely reporting zero stock. This ETL runs separately from the five-minute sales sync because it reads a full inventory snapshot.

```sql
SELECT sku_id,sku_name,store_id,store_name,snapshot_date,inventory_quantity,inventory_value,
       sales_window_start,sales_window_end,units_sold_30d,sales_amount_30d
FROM high_sales_low_inventory
WHERE snapshot_date BETWEEN '2026-10-01' AND '2026-10-31'
ORDER BY units_sold_30d DESC;

SELECT * FROM high_sales_low_inventory_metadata;
```
