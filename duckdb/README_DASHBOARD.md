# SKU count dashboard

The dashboard is split into two independent projects:

- `backend/` — FastAPI service querying the configured MySQL analytics database.
- `frontend/` — React and Vite single-page dashboard.

The UI opens on **MySQL Tables**, where it lists the configured database's tables and columns and lets you preview table rows. Set the reports API's `MYSQL_DATABASE` to `dashboard` (along with the matching host, user, and password) to browse the database schema shown in the provided export.

## Start the backend

From the repository root:

```powershell
python -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
backend\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000
```

Before starting the API, configure `MYSQL_HOST`, `MYSQL_PORT` (optional, defaults to `3306`), `MYSQL_DATABASE`, `MYSQL_USER`, and `MYSQL_PASSWORD` in the shell. `MYSQL_DATABASE` must contain the dashboard's prepared analytics tables; see `backend/README.md` for the table list. Set `CORS_ORIGINS` if the frontend uses a different origin.

## Start the frontend

In a second terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open the Vite URL, normally `http://localhost:5173`.

The dashboard starts with the last seven available sales days. **Last 7 days** ends on the newest sales date in MySQL; **Last month** selects the previous calendar month relative to that date. Custom start and end dates are inclusive. The report ranks SKUs over the entire selected range, not by adding daily rank numbers.

## Analytics assistant

The floating assistant uses the chat service in `../Fast API`. Run the dashboard API on port `8000` and the chat API on port `8002`. In `Fast API/backend/.env`, set `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_DATABASE=dashboard`, `MYSQL_USER`, and `MYSQL_PASSWORD` to the same MySQL connection values as the dashboard API. Also set `FRONTEND_ORIGIN=http://localhost:5173` (add `http://127.0.0.1:5173` if you open Vite with that host) and configure `GEMINI_API_KEY` as described in `Fast API/README.md`. Start the chat service from `Fast API/backend`:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app:app --reload --host 127.0.0.1 --port 8002
```

The dashboard points chat requests to `http://127.0.0.1:8002/api` by default. Its model selector shows Gemini models and the models discovered from the Kaggle proxy. Set `VITE_CHAT_API_BASE` at frontend build time to use a different chat API URL.
