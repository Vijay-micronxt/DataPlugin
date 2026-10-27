# Analytics dashboard

This folder contains the MySQL-backed dashboard API and the React/Vite frontend. The optional chat API and D2 source-to-destination ETL live in `../Fast API` and `../fetching`.

Use the repository [README](../README.md) for the Windows PowerShell quick start. It documents the demo SQL import, real MySQL ETL setup, environment files, database permissions, all three service commands, health checks, and common fixes.

The frontend runs from `frontend/` and normally opens at `http://localhost:5173`. The dashboard API defaults to port `8000`; its health endpoint is `http://127.0.0.1:8000/api/health` and its API docs are at `http://127.0.0.1:8000/docs`.

The default Vite proxy sends `/api` requests to `http://127.0.0.1:8000`. The floating chat widget calls `http://127.0.0.1:8002/api` unless `VITE_CHAT_API_BASE` is set at frontend build time.

The demo database name is `dashboard_demo`. The ETL destination must use its own dedicated MySQL schema and should be selected consistently in `fetching/.env` (`DEST_DATABASE`) and each API `.env` (`MYSQL_DATABASE`).
