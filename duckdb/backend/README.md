# StoreNxt analytics backend

FastAPI API for the analytics dashboard. It connects to MySQL and recalculates SKU ranks across the requested date range.

## Start

From the `backend` directory:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
# Edit .env and enter your MySQL connection settings.
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

The backend loads `backend/.env` automatically at startup. Set `MYSQL_HOST`, `MYSQL_DATABASE`, `MYSQL_USER`, and `MYSQL_PASSWORD`; `MYSQL_PORT` defaults to `3306`. Keep `.env` private and do not commit it. The dashboard opens on **MySQL Tables**, which lists tables and columns from the configured database and previews rows 100 at a time. It reads table metadata from `INFORMATION_SCHEMA` and only selects data from the chosen table. **Run incremental sync** launches `fetching/sync_incremental.py` from this repository; sync settings are read from `fetching/.env`, and logs are written to `fetching/logs/incremental_sync.log`. The analytics report pages still expect the separate prepared analytics schema. The browser app runs on `http://localhost:5173`; set `CORS_ORIGINS` in `.env` if its origin differs.

## Routes

- `GET /api/health`
- `GET /api/metadata` returns the available sales date range and stores.
- `GET /api/tables` lists the configured database's tables and columns.
- `GET /api/tables/{table_name}/rows?limit=100&offset=0` returns a paginated table preview.
- `POST /api/sync/run` starts the incremental sync; `GET /api/sync/status` reports its state.
- `GET /api/reports/top-selling-skus-count?start_date=2026-10-01&end_date=2026-10-17&limit=50`

Date bounds are inclusive. Rankings are calculated over the entire selected range, so a range spanning multiple days gets one combined SKU ranking.
