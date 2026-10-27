# DataPlugin

DataPlugin is a local analytics dashboard with two FastAPI services and a Vite frontend. The report and chat APIs use MySQL. The repository does not contain the original production database; a small synthetic demo database is included so a fresh clone can run locally.

## Local setup

Requirements: Python, Node.js/npm, and a running MySQL server.

### 1. Create the demo database

From the repository root, run the included SQL patch with a MySQL account that can create databases and tables:

```powershell
cmd /c 'mysql -u root -p < "duckdb\database\demo_database_patch.sql"'
```

This creates `dashboard_demo` and fills it with sample data for the dashboard reports. It does not contain production records. You can also open `duckdb/database/demo_database_patch.sql` in MySQL Workbench and run it there.

### 2. Configure the APIs

Copy the example environment files:

```powershell
Copy-Item "duckdb\backend\.env.example" "duckdb\backend\.env"
Copy-Item "Fast API\backend\.env.example" "Fast API\backend\.env"
```

Edit both `.env` files with the MySQL host, username, and password for your local MySQL server. Both should use `MYSQL_DATABASE=dashboard_demo`. Add a Gemini API key to `Fast API/backend/.env` if you want AI chat; the dashboard reports work without it. `.env` files and local databases are ignored by Git.

### 3. Install dependencies and start services

Run each command in its own PowerShell terminal from the repository root.

Dashboard API (port 8000):

```powershell
python -m venv "duckdb\backend\.venv"
"duckdb\backend\.venv\Scripts\python.exe" -m pip install -r "duckdb\backend\requirements.txt"
"duckdb\backend\.venv\Scripts\python.exe" -m uvicorn app.main:app --app-dir "duckdb\backend" --reload --host 127.0.0.1 --port 8000
```

Chat API (port 8002):

```powershell
python -m venv "Fast API\backend\.venv"
"Fast API\backend\.venv\Scripts\python.exe" -m pip install -r "Fast API\backend\requirements.txt"
"Fast API\backend\.venv\Scripts\python.exe" -m uvicorn app:app --app-dir "Fast API\backend" --reload --host 127.0.0.1 --port 8002
```

Frontend (port 5173):

```powershell
cd "duckdb\frontend"
npm install
npm run dev
```

Open the Vite URL, normally <http://localhost:5173>. The APIs are available at <http://127.0.0.1:8000/docs> and <http://127.0.0.1:8002/docs>.

## Using your own database

Set `MYSQL_DATABASE` to your own prepared analytics schema in both API `.env` files. It must contain the analytics tables queried by the dashboard. The included demo SQL is intended for local evaluation, not as a replacement for your production data or its ETL process.
