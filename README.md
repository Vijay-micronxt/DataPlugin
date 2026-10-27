# DataPlugin

DataPlugin is a Windows-friendly analytics dashboard. It has a MySQL-backed dashboard API, an optional AI chat API, a React/Vite frontend, and optional ETL scripts that copy data from a D2 MySQL source into a separate analytics MySQL database.

The repository contains **synthetic demo data**, not production D2 records. A new developer can run the dashboard with the demo database, or configure their own D2 source and destination to run the ETL. These are separate setup choices; do not run the ETL against the demo schema.

## Prerequisites

- Windows 10/11, PowerShell, and Git.
- 64-bit standard CPython 3.12. Standard CPython 3.13 also works; avoid a free-threaded build such as `3.13t`.
- Node.js 20 LTS or newer (includes npm).
- Docker Desktop with Docker Compose for the easiest demo database setup. Or install/reuse MySQL 8.0+ locally; MySQL Workbench can be used instead of the `mysql` command-line client to import SQL.
- Internet access to install Python/npm packages. AI chat also needs a key or endpoint for a configured model provider.

In GitHub, select the `feature/dashboard` branch before cloning; that branch contains the ETL scripts and demo SQL. If the repository is already cloned on a different branch, switch to it from the repository root:

```powershell
git switch feature/dashboard
```

Run commands below from the repository root. PowerShell commands call the virtual environment's `python.exe` directly, so activating the environment is not required.

## 1. Create a Python environment and install packages

```powershell
py -3.12 -m venv .venv
& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -r "duckdb\backend\requirements.txt" -r "Fast API\backend\requirements.txt" -r "fetching\requirements.txt"
```

If you installed standard CPython 3.13 instead, replace `py -3.12` with `py -3.13`. Check `py -0p` to see Python versions registered with the Windows launcher.

## 2. Choose and configure a database

### Option A: run the demo

The demo patch creates a separate `dashboard_demo` database and fills it with synthetic rows for the reports. The recommended path uses Docker Desktop to start MySQL and import the patch automatically:

```powershell
docker compose up -d --wait mysql
```

The container publishes MySQL only on `127.0.0.1:3307`. For this synthetic demo only, it allows a passwordless local root connection. In both API `.env` files, use `MYSQL_HOST=127.0.0.1`, `MYSQL_PORT=3307`, `MYSQL_DATABASE=dashboard_demo`, `MYSQL_USER=root`, and leave `MYSQL_PASSWORD` blank. Do not use this passwordless configuration with real data or expose the MySQL port to other machines. For a separately installed MySQL server, use your own credentials; the API only needs `SELECT` access to the demo schema.

If you already have a local MySQL server, import `duckdb/database/demo_database_patch.sql` from the repository root using an account allowed to create databases/tables and insert rows:

```powershell
cmd.exe /c 'mysql --user=root --password < "duckdb\database\demo_database_patch.sql"'
```

Or open the SQL file in MySQL Workbench and run it. For local MySQL, use its host, port, username, and password in the API `.env` files (the usual port is `3306`). Use the patch only in a local/demo environment; it seeds synthetic rows and is not a production migration. A local runtime API account only needs `SELECT` on `dashboard_demo` and access to `INFORMATION_SCHEMA`.

Copy the API templates without overwriting existing local settings:

```powershell
if (-not (Test-Path "duckdb\backend\.env")) { Copy-Item "duckdb\backend\.env.example" "duckdb\backend\.env" }
if (-not (Test-Path "Fast API\backend\.env")) { Copy-Item "Fast API\backend\.env.example" "Fast API\backend\.env" }
```

Edit `duckdb/backend/.env` and set `MYSQL_HOST`, `MYSQL_USER`, and `MYSQL_PASSWORD` to a local MySQL account. Set `MYSQL_DATABASE=dashboard_demo`. The dashboard API only needs `SELECT` access to this schema (and normal access to MySQL `INFORMATION_SCHEMA`). If you want the chat API too, make the same database settings in `Fast API/backend/.env`. Add your own `GEMINI_API_KEY` there to get Gemini responses; without a model provider key, the chat API can start but cannot generate AI replies.

The SQL import account needs permission to create the demo database and its tables and to insert the sample data. The runtime API account can be a separate read-only account.

### Option B: sync your own D2 data

This option requires access to a D2 MySQL source with the schema expected by the ETLs. At minimum, the scripts read `materialized_orders`, `materialized_order_items`, and related tables such as `stores`, `skus`, `food_types`, `sku_daily_snapshots`, payment records, and order ratings. The source account needs `SELECT` access to the tables used by the ETLs and network access to the MySQL server.

The ETL writes reports and checkpoint/ledger tables to a **separate destination database**. Create that database first; the ETLs create and manage tables inside it. Use a dedicated destination schema because some ETLs replace or rebuild report tables. Grant the ETL account `SELECT`, `INSERT`, `UPDATE`, `DELETE`, `CREATE`, `ALTER`, and `DROP` access on that destination schema. The source database is read-only to these scripts.

Create the fetching settings file if it does not exist, then edit it locally:

```powershell
if (-not (Test-Path "fetching\.env")) { Copy-Item "fetching\.env.example" "fetching\.env" }
```

In `fetching/.env`, set `DB_HOST`, `DB_PORT`, `DB_DATABASE`, `DB_USERNAME`, and `DB_PASSWORD` for the D2 **source**. Set `DEST_HOST`, `DEST_PORT`, `DEST_DATABASE`, `DEST_USER`, and `DEST_PASSWORD` for the **destination**. Choose `ETL_SALES_POLICY` based on your business rules (`delivered_gross` or `noncancelled_gross`); review [`fetching/README.md`](fetching/README.md) for the source mapping, report definitions, and other ETL settings.

Set `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_DATABASE`, `MYSQL_USER`, and `MYSQL_PASSWORD` in each API `.env` to the destination database so the dashboard and chat read the tables written by the ETL. `MYSQL_DATABASE` must match `DEST_DATABASE`. If you configure only the demo database, the **Run incremental sync** button will not work because it also requires valid source and destination values in `fetching/.env`.

After the database settings and permissions are in place, you can start one sync manually from the repository root:

```powershell
& ".\.venv\Scripts\python.exe" "fetching\sync_incremental.py"
```

This command writes to the configured destination. The dashboard button runs the same sync. Logs are written to `fetching/logs/incremental_sync.log`.

## 3. Start the dashboard API

Open a PowerShell terminal at the repository root:

```powershell
& ".\.venv\Scripts\python.exe" -m uvicorn app.main:app --app-dir "duckdb\backend" --reload --host 127.0.0.1 --port 8000
```

Dashboard health: <http://127.0.0.1:8000/api/health>

Interactive API docs: <http://127.0.0.1:8000/docs>

## 4. Optionally start the chat API

In a second PowerShell terminal at the repository root, after configuring `Fast API/backend/.env`:

```powershell
& ".\.venv\Scripts\python.exe" -m uvicorn app:app --app-dir "Fast API\backend" --reload --host 127.0.0.1 --port 8002
```

Chat health: <http://127.0.0.1:8002/api/health>

Interactive API docs: <http://127.0.0.1:8002/docs>

The dashboard's floating chat widget uses this API. Set `FRONTEND_ORIGIN` to the exact frontend origin(s) you use. Configure `GEMINI_API_KEY` or your own OpenAI-compatible proxy settings in the chat API `.env` for model responses.

## 5. Install and start the frontend

In a third PowerShell terminal:

```powershell
Set-Location "duckdb\frontend"
npm.cmd ci
npm.cmd run dev
```

Open the Vite URL shown in the terminal, normally <http://localhost:5173>. Use `npm.cmd` in PowerShell to avoid execution-policy errors from `npm.ps1`.

## Database and local file notes

- The demo patch defines the report tables used by the dashboard and includes synthetic sample rows. Use the ETL option to populate a separate destination with D2-derived reports.
- Chat conversation history is stored in `Fast API/backend/data/conversations.sqlite3` by default and is created automatically.
- The source-to-destination sync requires `fetching/.env`; the report and chat APIs use their own `.env` files.
- `.env` files, SQLite files, DuckDB files, Python environments, and frontend dependencies are ignored by Git. Only `.env.example` templates should be committed. Never put real credentials in an example file.

## Troubleshooting

- **MySQL connection or health check fails:** Confirm MySQL is running and the API `.env` has the correct host, port, database, username, and password. For the demo, import the SQL patch into `dashboard_demo`. For ETL mode, make sure the APIs point to the same database as `DEST_DATABASE`.
- **Missing tables or an empty dashboard:** In demo mode, import the demo patch. In ETL mode, create the destination schema, configure `fetching/.env`, run the sync, and check `fetching/logs/incremental_sync.log`. The source schema must match the ETLs.
- **ETL login or permission error:** Check source credentials and network/IP allowlisting. The source needs read access; the destination needs the write/DDL permissions listed above. Also check `ETL_SALES_POLICY` and the source status names.
- **CORS error:** Use `http://localhost:5173`, or set both API CORS settings for the exact origin. In `duckdb/backend/.env`, set `CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173`. In `Fast API/backend/.env`, set `FRONTEND_ORIGIN` to the same comma-separated origins, then restart the APIs.
- **Port is already in use:** Stop the other process or choose a free port. If Docker reports port `3307` is busy, change the host side of the MySQL mapping in `compose.yaml` and set the same `MYSQL_PORT` in both API `.env` files. If changing the dashboard API port, update the `/api` proxy in `duckdb/frontend/vite.config.ts`. If changing the chat API port, set `VITE_CHAT_API_BASE` for the frontend and update `FRONTEND_ORIGIN` as needed.
- **PowerShell blocks `npm`:** Run `npm.cmd` instead of `npm`.
- **Python package or native install errors:** Confirm you used 64-bit standard CPython, not a free-threaded build, and recreated `.venv` with the intended Python version.

## Security

The repository does not need your production database credentials or production rows. Keep local `.env` files private, use the placeholder `.env.example` files as templates, and rotate any credential that was accidentally committed or shared.
