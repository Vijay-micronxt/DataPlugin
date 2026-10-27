"""Run every report ETL once; intended for Windows Task Scheduler."""
from __future__ import annotations

import logging
import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    filename=LOG_DIR / "incremental_sync.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
LOG = logging.getLogger("incremental_sync")
ETL_SCRIPTS = (
    "etl_top_selling_skus.py",
    "etl_store_performance.py",
    "etl_sales_by_food_type.py",
    "etl_sales_per_hour.py",
    "etl_top_customers_by_order.py",
    "etl_repeat_nonrepeated_customers.py",
    "etl_customer_journey.py",
    "etl_store_feedback.py",
    "etl_low_sales_high_inventory.py",
    "etl_high_sales_low_inventory.py",
)


def run_once() -> int:
    LOG.info("sync run started")
    failed = []
    for script in ETL_SCRIPTS:
        path = ROOT / script
        if not path.is_file():
            LOG.error("%s is missing", script)
            failed.append(script)
            continue
        result = subprocess.run(
            [sys.executable, str(path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.stdout.strip():
            LOG.info("%s output:\n%s", script, result.stdout.strip())
        if result.stderr.strip():
            LOG.info("%s log:\n%s", script, result.stderr.strip())
        if result.returncode:
            LOG.error("%s failed with exit code %s", script, result.returncode)
            failed.append(script)
        else:
            LOG.info("%s completed", script)
    if failed:
        LOG.error("sync run completed with failures: %s", ", ".join(failed))
        return 1
    LOG.info("all ETLs completed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", action="store_true", help="Repeat the sync until stopped")
    parser.add_argument("--interval-minutes", type=int, default=5, help="Polling interval when using --watch (default: 5)")
    args = parser.parse_args()
    if not 1 <= args.interval_minutes <= 1440:
        parser.error("--interval-minutes must be between 1 and 1440")
    if not args.watch:
        return run_once()
    LOG.info("watch mode started; interval=%s minutes", args.interval_minutes)
    try:
        while True:
            try:
                run_once()
            except Exception:
                LOG.exception("sync run failed; will retry on the next interval")
            time.sleep(args.interval_minutes * 60)
    except KeyboardInterrupt:
        LOG.info("watch mode stopped")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
