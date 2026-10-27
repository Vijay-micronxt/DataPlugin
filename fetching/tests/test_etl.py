import unittest

from etl_top_selling_skus import ident, policy_clause


class ETLUnitTests(unittest.TestCase):
    def test_sql_identifier_is_quoted(self):
        self.assertEqual(ident("materialized_orders"), "`materialized_orders`")

    def test_invalid_sql_identifier_rejected(self):
        with self.assertRaises(ValueError):
            ident("orders; DROP TABLE x")

    def test_delivered_policy_uses_configured_statuses(self):
        import os
        previous = os.environ.get("ETL_DELIVERED_STATUSES")
        try:
            os.environ["ETL_DELIVERED_STATUSES"] = "Delivered, fulfilled"
            sql = policy_clause("delivered_gross")
            self.assertIn("order_status_name", sql)
            self.assertIn("cancelled_at IS NULL", sql)
        finally:
            if previous is None:
                os.environ.pop("ETL_DELIVERED_STATUSES", None)
            else:
                os.environ["ETL_DELIVERED_STATUSES"] = previous

    def test_unconfirmed_policy_fails_closed(self):
        with self.assertRaises(ValueError):
            policy_clause("")


if __name__ == "__main__":
    unittest.main()
