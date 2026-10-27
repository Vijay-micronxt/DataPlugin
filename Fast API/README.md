# Analytics chat API

This FastAPI service provides the optional assistant shown in the dashboard at `duckdb/frontend`. It reads the same configured MySQL analytics database as the dashboard API and stores conversation history locally in SQLite.

For the full Windows PowerShell clone-and-run guide, including Python setup, demo/ETL database choices, service commands, permissions, health URLs, and troubleshooting, see the repository [README](../README.md).

## Configuration

Copy `backend/.env.example` to `backend/.env` and keep the real file private. Set `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_DATABASE`, `MYSQL_USER`, and `MYSQL_PASSWORD` to the analytics destination used by the dashboard API. The local demo schema is `dashboard_demo`; for a real ETL destination, `MYSQL_DATABASE` should match `DEST_DATABASE` in `fetching/.env`.

Configure at least one model provider if you want the assistant to generate responses:

- Gemini: set `GEMINI_API_KEY` and optionally `GEMINI_MODEL`.
- OpenAI-compatible proxy: set `OLLAMA_BASE_URL`, optionally `OLLAMA_API_KEY`, and optionally `OLLAMA_MODEL`.
- OpenRouter: set `OPENROUTER_API_KEY`, with optional base URL/model overrides.

The `.env.example` contains no working provider credentials or private service URL. The app does not use a preconfigured personal proxy endpoint.

## API endpoints

- Health: `GET /api/health`
- Model list: `GET /api/models`
- Database schema: `GET /api/database/schema`
- Conversation history and chat: `/api/conversations` and `POST /api/chat`

By default the API listens on port `8002`; its docs are at `http://127.0.0.1:8002/docs`.
