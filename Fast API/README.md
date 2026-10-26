# StoreNxt Analytics Chat

FastAPI backend for the dashboard's MySQL database. The assistant reads tables and views from the same database configured for the dashboard (default schema name: `dashboard`). Conversation history remains in a separate SQLite file. The backend supports Google Gemini and the configured OpenAI-compatible Kaggle proxy.

## Inspected workspace

- Analytics database: MySQL database selected by `MYSQL_DATABASE` (defaults to `dashboard`); the chatbot discovers its tables, views, and columns at runtime.
- Backend entry: `backend/app.py`; Gemini adapter: `backend/services/gemini_service.py`.
- Frontend: `duckdb/frontend/src/ChatWidget.tsx`, the dashboard's floating chat widget and model picker.
- Persistent conversations: `backend/data/conversations.sqlite3`; it is separate from MySQL and excluded from Git.

## Gemini model

The default is `gemini-3.5-flash-lite`, selected as a cost-conscious model for conversational and structured-output requests. Google currently lists this and `gemini-3.8-flash` as models for new projects; the Google Gen AI SDK lists models available to your API key, and the widget selector uses that list. Check Google's [model guide](https://ai.google.dev/gemini-api/docs/models) if you want to change the configured default.

The backend uses Google's official `google-genai` Python SDK. Gemini returns validated structured intent (`conversation`, `database_query`, or `clarification`) and optional SQL. The backend validates MySQL SQL with SQLGlot, restricts references to the discovered `dashboard` schema, executes it in a read-only transaction, applies a query timeout, and caps results at 200 rows. A single correction attempt is allowed for invalid or failed SQL. Gemini only receives relevant schema and conversation context, never database contents unless those rows are specifically included for result explanation.

The assistant also discovers models from the configured `OLLAMA_BASE_URL` plus `/models` and sends requests to its OpenAI-compatible `/chat/completions` endpoint. The default base URL already ends in `/v1`. Select a provider and model in the assistant dropdown. `OLLAMA_API_KEY` is optional and is sent as a bearer token when set. The existing variable names are retained for configuration compatibility.

## Create and configure a Gemini API key

1. Create an API key in [Google AI Studio](https://aistudio.google.com/app/apikey).
2. From `E:\BOT\Fast API\backend`, copy the example file if you do not already have a local `.env`:

   ```powershell
   Copy-Item .env.example .env
   ```

3. Edit `backend\.env` locally and set your Gemini API key. Keep the key only in this backend file; `.env` is excluded from Git. Do not paste the key into React, chat, or README files.
4. Set `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_DATABASE`, `MYSQL_USER`, and `MYSQL_PASSWORD` to the same values used by the dashboard API. `MYSQL_DATABASE` defaults to `dashboard`.
5. Keep `GEMINI_MODEL=gemini-3.5-flash-lite` initially. You can choose another model from the widget selector if Google lists it for your key.

## Install and run on Windows PowerShell

Use a regular 64-bit Python, not a free-threaded build. Python 3.13 or regular Python 3.14 works.

### Backend terminal

```powershell
cd 'E:\BOT\Fast API\backend'
py -3.14 -m venv .venv
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app:app --reload --host 127.0.0.1 --port 8002
```

If you use Python 3.13, replace `py -3.14` with `py -3.13`. The activation script is optional; calling the virtual environment's `python.exe` directly avoids PowerShell execution-policy issues.

### Frontend terminal

```powershell
cd 'E:\BOT\duckdb\frontend'
npm install
npm run dev
```

Open the dashboard at <http://localhost:5173>. API health: <http://127.0.0.1:8002/api/health>. Swagger UI: <http://127.0.0.1:8002/docs>.

## Verification

- `/api/health` reports API and MySQL status, Gemini model discovery and generation, and the Kaggle proxy model list.
- `available_models` contains Gemini models; `available_kaggle_models` contains model IDs from the configured Kaggle proxy.
- Choose a model in the dashboard assistant. The backend routes the request to that model's provider and validates its ID against the provider's current model list.
- Backend checks: `python -m unittest discover -s tests -v` from `backend`.
- Frontend production build: `npm run build` from `frontend`.

## Troubleshooting

- **Missing key:** Set `GEMINI_API_KEY` in `backend\.env`, save, and restart Uvicorn.
- **401/403:** Confirm the key is valid and the selected model is available to its Google AI Studio project.
- **429/quota:** Check usage and quota in Google AI Studio; retry later or select an available Flash-Lite model.
- **Gemini model list empty:** Check `/api/health`, internet access to Google's Gemini API, and API-key permissions.
- **Kaggle model list empty:** Confirm `OLLAMA_BASE_URL` points to the OpenAI-compatible `/v1` endpoint, check that `/v1/models` is reachable from the backend machine, and configure `OLLAMA_API_KEY` if required.
- **SQL clarification/error:** Provide a specific date range, product, store, or metric. Unsafe SQL is rejected; failed read-only queries get at most one repair attempt.
- **Database unavailable:** Confirm the chatbot `.env` has the same `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_DATABASE`, `MYSQL_USER`, and `MYSQL_PASSWORD` values as the dashboard API. The MySQL user should have read-only access for the chatbot.
- **CORS:** `FRONTEND_ORIGIN` accepts comma-separated exact origins. Defaults cover `localhost:5173` and `127.0.0.1:5173`.
- **Port conflicts:** Change Uvicorn's port and `VITE_CHAT_API_BASE`, or update Vite's port and `FRONTEND_ORIGIN` together.
# OpenRouter chat provider

The assistant can use OpenRouter alongside Gemini and the configured Kaggle proxy. Add these settings to `backend/.env`, then restart the chatbot API:

```dotenv
OPENROUTER_API_KEY=your_openrouter_api_key
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_MODEL=
```

Create a key in [OpenRouter](https://openrouter.ai/keys). Open the assistant's model menu and select an **OpenRouter** model. The chat continues to query the same configured MySQL database.
