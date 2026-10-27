"""Refresh a store-level summary of customer order feedback from D2."""
from __future__ import annotations

import logging
import sys
from decimal import Decimal

from etl_top_selling_skus import config, connect_with_retry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("store_feedback")


def ensure_destination(cn):
    cur = cn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS store_feedback (
      store_id INT UNSIGNED NOT NULL,
      store_name VARCHAR(255) NOT NULL,
      feedback_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
      average_rating DECIMAL(4,2) NULL,
      rating_1_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
      rating_2_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
      rating_3_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
      rating_4_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
      rating_5_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
      latest_feedback_at DATETIME NULL,
      last_updated TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      PRIMARY KEY (store_id),
      KEY idx_store_feedback_average (average_rating),
      KEY idx_store_feedback_count (feedback_count)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
    cur.execute("""CREATE TABLE IF NOT EXISTS store_feedback_details (
      feedback_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
      order_id BIGINT UNSIGNED NOT NULL,
      store_id INT UNSIGNED NOT NULL,
      store_name VARCHAR(255) NOT NULL,
      customer_id INT UNSIGNED NULL,
      customer_name VARCHAR(255) NULL,
      employee_id INT UNSIGNED NULL,
      rating TINYINT UNSIGNED NOT NULL,
      description TEXT NULL,
      feedback_created_at DATETIME NOT NULL,
      last_updated TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      PRIMARY KEY (feedback_id),
      KEY idx_feedback_detail_store_time (store_id,feedback_created_at),
      KEY idx_feedback_detail_order (order_id),
      KEY idx_feedback_detail_rating (rating)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
    # CREATE TABLE IF NOT EXISTS does not add columns to an existing destination.
    cur.execute("""SELECT COUNT(*) FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA=%s AND TABLE_NAME='store_feedback_details' AND COLUMN_NAME='customer_name'""",
        (cn.database,))
    if not cur.fetchone()[0]:
        cur.execute("ALTER TABLE store_feedback_details ADD COLUMN customer_name VARCHAR(255) NULL AFTER customer_id")
    cn.commit()
    cur.close()


def run(source, dest):
    src_cur = source.cursor(dictionary=True)
    dst_cur = dest.cursor()
    src_cur.execute("""SELECT s.id AS store_id,
          COALESCE(NULLIF(TRIM(s.name), ''), CONCAT('Store ', s.id)) AS store_name,
          COUNT(r.order_id) AS feedback_count,
          AVG(r.rating) AS average_rating,
          COALESCE(SUM(r.rating = 1), 0) AS rating_1_count,
          COALESCE(SUM(r.rating = 2), 0) AS rating_2_count,
          COALESCE(SUM(r.rating = 3), 0) AS rating_3_count,
          COALESCE(SUM(r.rating = 4), 0) AS rating_4_count,
          COALESCE(SUM(r.rating = 5), 0) AS rating_5_count,
          MAX(r.created_at) AS latest_feedback_at
        FROM stores s
        LEFT JOIN order_ratings r
          ON r.store_id = s.id AND r.rating BETWEEN 1 AND 5
        GROUP BY s.id, s.name
        ORDER BY s.id""")
    values = []
    for row in src_cur.fetchall():
        store_id = row["store_id"]
        if store_id is None:
            raise ValueError("D2 stores contains a row without an ID")
        count = int(row["feedback_count"] or 0)
        average = Decimal(str(row["average_rating"])) if row["average_rating"] is not None else None
        if count < 0 or (average is not None and not Decimal("1") <= average <= Decimal("5")):
            raise ValueError(f"Invalid feedback aggregate for store {store_id}")
        rating_counts = [int(row[f"rating_{n}_count"] or 0) for n in range(1, 6)]
        if sum(rating_counts) != count:
            raise ValueError(f"Rating counts do not match feedback count for store {store_id}")
        values.append((store_id, row["store_name"], count, average, *rating_counts, row["latest_feedback_at"]))

    src_cur.execute("""SELECT r.order_id, r.store_id,
          COALESCE(NULLIF(TRIM(s.name), ''), CONCAT('Store ', r.store_id)) AS store_name,
          r.customer_id,
          NULLIF(TRIM(CONCAT_WS(' ', c.first_name, c.last_name)), '') AS customer_name,
          r.employee_id, r.rating, r.description,
          r.created_at AS feedback_created_at
        FROM order_ratings r
        LEFT JOIN stores s ON s.id = r.store_id
        LEFT JOIN customers c ON c.id = r.customer_id
        WHERE r.rating BETWEEN 1 AND 5
        ORDER BY r.created_at, r.store_id, r.order_id""")
    detail_values = []
    for row in src_cur.fetchall():
        if any(row[k] is None for k in ("order_id", "store_id", "rating", "feedback_created_at")):
            raise ValueError("D2 order_ratings contains a missing required feedback field")
        rating = int(row["rating"])
        if not 1 <= rating <= 5:
            raise ValueError(f"Invalid rating {rating} for order {row['order_id']}")
        description = row["description"]
        description = description.strip() if description and description.strip() else None
        detail_values.append((row["order_id"], row["store_id"], row["store_name"], row["customer_id"],
                              row["customer_name"],
                              row["employee_id"], rating, description, row["feedback_created_at"]))

    try:
        dest.commit()
        dest.start_transaction()
        dst_cur.execute("DELETE FROM store_feedback_details")
        dst_cur.execute("DELETE FROM store_feedback")
        if detail_values:
            dst_cur.executemany("""INSERT INTO store_feedback_details
                (order_id,store_id,store_name,customer_id,customer_name,employee_id,rating,description,feedback_created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""", detail_values)
        if values:
            dst_cur.executemany("""INSERT INTO store_feedback
                (store_id,store_name,feedback_count,average_rating,
                 rating_1_count,rating_2_count,rating_3_count,rating_4_count,rating_5_count,
                 latest_feedback_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", values)
        dest.commit()
    except Exception:
        dest.rollback()
        raise
    finally:
        src_cur.close()
        dst_cur.close()
    descriptions = sum(v[6] is not None for v in detail_values)
    LOG.info("store_feedback refreshed stores=%s rated_stores=%s feedback_rows=%s with_descriptions=%s",
             len(values), sum(v[2] > 0 for v in values), len(detail_values), descriptions)
    return len(values)


def main():
    source_cfg, dest_cfg = config()
    source = connect_with_retry(source_cfg, 3)
    dest = connect_with_retry(dest_cfg, 3)
    try:
        ensure_destination(dest)
        count = run(source, dest)
        print(f"Refreshed store_feedback: {count} stores.")
    finally:
        source.close()
        dest.close()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        LOG.exception("Store feedback ETL failed")
        sys.exit(1)
