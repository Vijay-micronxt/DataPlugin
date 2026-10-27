# Dashboard API

FastAPI service for the analytics dashboard. It reads MySQL using the `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_DATABASE`, `MYSQL_USER`, and `MYSQL_PASSWORD` values in this folder's `.env` file. The example defaults to the local demo schema `dashboard_demo`.

Use the repository [README](../../README.md) for the Windows PowerShell install and run steps. The API normally listens on `http://127.0.0.1:8000`:

- Health: `GET /api/health`
- Metadata and store list: `GET /api/metadata`
- MySQL table list and preview: `GET /api/tables` and `/api/tables/{table_name}/rows`
- Reports: `/api/reports/...`
- Incremental ETL sync: `POST /api/sync/run`; status: `GET /api/sync/status`

The sync endpoint runs `fetching/sync_incremental.py` relative to this repository. The sync scripts read their own settings from `fetching/.env`; configure source and destination access as described in the root README. Keep all `.env` files private.
