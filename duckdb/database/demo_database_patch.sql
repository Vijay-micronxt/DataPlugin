-- Demo database patch for a fresh local checkout.
-- Creates a small synthetic MySQL dataset for all dashboard report endpoints.
-- This is sample data, not an export of the production database.

CREATE DATABASE IF NOT EXISTS dashboard_demo CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE dashboard_demo;

CREATE TABLE IF NOT EXISTS top_selling_skus (
    sold_date DATE NOT NULL,
    store_id BIGINT NOT NULL,
    store_name VARCHAR(160),
    sku_id BIGINT NOT NULL,
    sku_name VARCHAR(200),
    total_quantity DECIMAL(14,2) NOT NULL DEFAULT 0,
    total_orders BIGINT NOT NULL DEFAULT 0,
    total_sales DECIMAL(14,2) NOT NULL DEFAULT 0,
    PRIMARY KEY (sold_date, store_id, sku_id),
    KEY idx_sku_date (sku_id, sold_date)
);

CREATE TABLE IF NOT EXISTS store_performance (
    sold_date DATE NOT NULL,
    store_id BIGINT NOT NULL,
    store_name VARCHAR(160),
    total_orders BIGINT NOT NULL DEFAULT 0,
    total_sales DECIMAL(14,2) NOT NULL DEFAULT 0,
    total_quantity DECIMAL(14,2) NOT NULL DEFAULT 0,
    PRIMARY KEY (sold_date, store_id)
);

CREATE TABLE IF NOT EXISTS customer_journey (
    store_id BIGINT NOT NULL DEFAULT 0,
    store_name VARCHAR(160),
    journey_stage VARCHAR(32) NOT NULL,
    customer_count BIGINT NOT NULL DEFAULT 0,
    refreshed_at DATETIME NOT NULL,
    KEY idx_journey_store (store_id, journey_stage)
);

CREATE TABLE IF NOT EXISTS low_sales_high_inventory (
    sku_id BIGINT NOT NULL,
    store_id BIGINT NOT NULL,
    snapshot_date DATE NOT NULL,
    sku_name VARCHAR(200),
    store_name VARCHAR(160),
    inventory_quantity DECIMAL(14,2) NOT NULL DEFAULT 0,
    min_inventory_quantity DECIMAL(14,2) NOT NULL DEFAULT 0,
    inventory_value DECIMAL(14,2) NOT NULL DEFAULT 0,
    units_sold_30d DECIMAL(14,2) NOT NULL DEFAULT 0,
    sales_amount_30d DECIMAL(14,2) NOT NULL DEFAULT 0,
    max_units_sold DECIMAL(14,2) NOT NULL DEFAULT 0,
    PRIMARY KEY (sku_id, store_id, snapshot_date)
);

CREATE TABLE IF NOT EXISTS high_sales_low_inventory (
    sku_id BIGINT NOT NULL,
    store_id BIGINT NOT NULL,
    snapshot_date DATE NOT NULL,
    sku_name VARCHAR(200),
    store_name VARCHAR(160),
    inventory_quantity DECIMAL(14,2) NOT NULL DEFAULT 0,
    inventory_value DECIMAL(14,2) NOT NULL DEFAULT 0,
    units_sold_30d DECIMAL(14,2) NOT NULL DEFAULT 0,
    sales_amount_30d DECIMAL(14,2) NOT NULL DEFAULT 0,
    min_units_sold DECIMAL(14,2) NOT NULL DEFAULT 0,
    max_inventory_quantity DECIMAL(14,2) NOT NULL DEFAULT 0,
    PRIMARY KEY (sku_id, store_id, snapshot_date)
);

CREATE TABLE IF NOT EXISTS sales_by_food_type (
    sold_date DATE NOT NULL,
    store_id BIGINT NOT NULL,
    food_type_id BIGINT NOT NULL,
    food_type_name VARCHAR(120),
    total_quantity DECIMAL(14,2) NOT NULL DEFAULT 0,
    total_sales DECIMAL(14,2) NOT NULL DEFAULT 0,
    total_orders BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (sold_date, store_id, food_type_id)
);

CREATE TABLE IF NOT EXISTS store_hourly_sales (
    sales_date DATE NOT NULL,
    store_id BIGINT NOT NULL,
    hour_of_day TINYINT NOT NULL,
    total_sales_value DECIMAL(14,2) NOT NULL DEFAULT 0,
    total_orders BIGINT NOT NULL DEFAULT 0,
    total_quantity DECIMAL(14,2) NOT NULL DEFAULT 0,
    PRIMARY KEY (sales_date, store_id, hour_of_day)
);

CREATE TABLE IF NOT EXISTS top_customers_by_order (
    last_order_date DATE NOT NULL,
    store_id BIGINT NOT NULL,
    customer_id BIGINT NOT NULL,
    customer_name VARCHAR(160),
    total_orders BIGINT NOT NULL DEFAULT 0,
    total_quantity DECIMAL(14,2) NOT NULL DEFAULT 0,
    total_sales DECIMAL(14,2) NOT NULL DEFAULT 0,
    PRIMARY KEY (last_order_date, store_id, customer_id)
);

CREATE TABLE IF NOT EXISTS repeat_nonrepeated_customers (
    last_order_date DATE NOT NULL,
    store_id BIGINT NOT NULL,
    customer_id BIGINT NOT NULL,
    total_orders BIGINT NOT NULL DEFAULT 0,
    total_quantity DECIMAL(14,2) NOT NULL DEFAULT 0,
    total_sales DECIMAL(14,2) NOT NULL DEFAULT 0,
    PRIMARY KEY (last_order_date, store_id, customer_id)
);

CREATE TABLE IF NOT EXISTS store_feedback_details (
    feedback_id BIGINT NOT NULL PRIMARY KEY,
    order_id BIGINT NOT NULL,
    store_id BIGINT NOT NULL,
    store_name VARCHAR(160),
    customer_id BIGINT,
    customer_name VARCHAR(160),
    rating TINYINT NOT NULL,
    description TEXT,
    feedback_created_at DATETIME NOT NULL,
    KEY idx_feedback_date_store (feedback_created_at, store_id)
);

-- Repeatable seed data: replace prior demo rows, preserving any unrelated records.
DELETE FROM top_selling_skus WHERE sold_date BETWEEN '2026-10-20' AND '2026-10-27';
DELETE FROM store_performance WHERE sold_date BETWEEN '2026-10-20' AND '2026-10-27';
DELETE FROM sales_by_food_type WHERE sold_date BETWEEN '2026-10-20' AND '2026-10-27';
DELETE FROM store_hourly_sales WHERE sales_date BETWEEN '2026-10-20' AND '2026-10-27';
DELETE FROM top_customers_by_order WHERE last_order_date BETWEEN '2026-10-20' AND '2026-10-27';
DELETE FROM repeat_nonrepeated_customers WHERE last_order_date BETWEEN '2026-10-20' AND '2026-10-27';
DELETE FROM low_sales_high_inventory WHERE snapshot_date = '2026-10-27';
DELETE FROM high_sales_low_inventory WHERE snapshot_date = '2026-10-27';
DELETE FROM store_feedback_details WHERE feedback_created_at >= '2026-10-20' AND feedback_created_at < '2026-10-28';
DELETE FROM customer_journey WHERE store_id IN (0, 1, 2);

INSERT INTO top_selling_skus VALUES
('2026-10-27',1,'Downtown Store',101,'Classic Burger',42,36,630.00),
('2026-10-27',1,'Downtown Store',102,'Veggie Wrap',28,24,336.00),
('2026-10-27',1,'Downtown Store',103,'Cold Coffee',31,27,248.00),
('2026-10-27',2,'Riverside Store',101,'Classic Burger',35,29,525.00),
('2026-10-27',2,'Riverside Store',104,'Paneer Bowl',22,20,374.00),
('2026-10-27',2,'Riverside Store',103,'Cold Coffee',24,22,192.00),
('2026-10-26',1,'Downtown Store',101,'Classic Burger',38,33,570.00),
('2026-10-26',1,'Downtown Store',102,'Veggie Wrap',25,22,300.00),
('2026-10-26',1,'Downtown Store',103,'Cold Coffee',27,24,216.00),
('2026-10-26',2,'Riverside Store',101,'Classic Burger',32,27,480.00),
('2026-10-26',2,'Riverside Store',104,'Paneer Bowl',20,18,340.00),
('2026-10-26',2,'Riverside Store',103,'Cold Coffee',21,19,168.00);

INSERT INTO store_performance VALUES
('2026-10-27',1,'Downtown Store',87,1804.00,112),
('2026-10-27',2,'Riverside Store',71,1450.00,91),
('2026-10-26',1,'Downtown Store',79,1620.00,101),
('2026-10-26',2,'Riverside Store',66,1325.00,84);

INSERT INTO customer_journey VALUES
(0,'All Stores','Cart',38,'2026-10-27 18:00:00'),
(0,'All Stores','Order',29,'2026-10-27 18:00:00'),
(0,'All Stores','Payment',25,'2026-10-27 18:00:00'),
(1,'Downtown Store','Cart',21,'2026-10-27 18:00:00'),
(1,'Downtown Store','Order',16,'2026-10-27 18:00:00'),
(1,'Downtown Store','Payment',14,'2026-10-27 18:00:00'),
(2,'Riverside Store','Cart',17,'2026-10-27 18:00:00'),
(2,'Riverside Store','Order',13,'2026-10-27 18:00:00'),
(2,'Riverside Store','Payment',11,'2026-10-27 18:00:00');

INSERT INTO low_sales_high_inventory VALUES
(105,1,'2026-10-27','Mango Smoothie','Downtown Store',48,8,384.00,4,32.00,12),
(106,2,'2026-10-27','Garden Salad','Riverside Store',35,6,350.00,5,50.00,10);
INSERT INTO high_sales_low_inventory VALUES
(101,1,'2026-10-27','Classic Burger','Downtown Store',5,75.00,92,1380.00,40,12),
(104,2,'2026-10-27','Paneer Bowl','Riverside Store',3,51.00,48,816.00,20,8);

INSERT INTO sales_by_food_type VALUES
('2026-10-27',1,1,'Burgers',42,630.00,36),
('2026-10-27',1,2,'Beverages',31,248.00,27),
('2026-10-27',1,3,'Wraps',28,336.00,24),
('2026-10-27',2,1,'Burgers',35,525.00,29),
('2026-10-27',2,4,'Meals',22,374.00,20),
('2026-10-27',2,2,'Beverages',24,192.00,22);

INSERT INTO store_hourly_sales VALUES
('2026-10-27',1,12,420.00,21,27),('2026-10-27',1,18,610.00,28,39),
('2026-10-27',1,19,774.00,38,46),('2026-10-27',2,12,350.00,18,23),
('2026-10-27',2,18,490.00,24,31),('2026-10-27',2,19,610.00,29,37);

INSERT INTO top_customers_by_order VALUES
('2026-10-27',1,501,'Aarav Shah',4,9,168.00),
('2026-10-27',1,502,'Mira Patel',3,7,142.00),
('2026-10-27',2,503,'Rohan Mehta',5,11,214.00),
('2026-10-27',2,504,'Anaya Rao',2,4,88.00);
INSERT INTO repeat_nonrepeated_customers VALUES
('2026-10-27',1,501,4,9,168.00),('2026-10-27',1,505,1,2,42.00),
('2026-10-27',2,503,5,11,214.00),('2026-10-27',2,504,2,4,88.00);

INSERT INTO store_feedback_details VALUES
(9001,7001,1,'Downtown Store',501,'Aarav Shah',5,'Quick service and a great burger.','2026-10-27 13:15:00'),
(9002,7002,1,'Downtown Store',502,'Mira Patel',4,'Fresh food, busy at lunch.','2026-10-27 14:20:00'),
(9003,7003,2,'Riverside Store',503,'Rohan Mehta',5,'Excellent meal and friendly staff.','2026-10-27 19:10:00'),
(9004,7004,2,'Riverside Store',504,'Anaya Rao',3,'Order was good, pickup took a while.','2026-10-26 18:45:00');
