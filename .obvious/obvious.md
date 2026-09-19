# ResearchForge — Agent Guide

Multi-agent research report generation system: a six-agent LangGraph pipeline
(Tavily web research → PDF/URL ingestion → parallel fact-checking → human-in-the-loop
outline approval → per-section synthesis → citation dedupe → ReportLab PDF export),
with all session state persisted to Postgres. FastAPI backend + React 19 SPA.

## Sandbox Snapshot

- **Snapshot ID:** `i0lim2flcer19du8gmluq` (computer `cmp_cRIS4QDw`, E2B template `vek56af6luvalspx0c77:default`)
- **Built at:** 2026-09-17T15:59:50.575Z (UTC)
- **Captured state:** Postgres 17 running on localhost:5432 (db/user/pass `researchforge`,
  `sessions` table + LangGraph checkpoint schema already created); backend deps installed
  in `backend/.venv`; frontend `node_modules` installed; Playwright Chromium in `/tmp/pwtest`.
  Dev servers do not survive a snapshot restore — restart them with the commands below.

## Stack

| Layer | Technology |
| --- | --- |
| Backend | Python 3.11+ (sandbox: 3.13), FastAPI + uvicorn, pydantic v2, SlowAPI rate limiting |
| Pipeline | LangGraph (>=1.0) + langgraph-checkpoint-postgres, OpenAI GPT-4o, Tavily |
| Database | PostgreSQL 16 (compose image; sandbox runs 17 via apt), psycopg3 + pool |
| Frontend | React 19, Vite 7 (migrated from CRA/craco in PR #3), Tailwind, Zustand, Yarn 1.22.22 |
| Export / tracing | ReportLab PDFs; LangSmith (optional) |
| Lint | ruff (config in `.ruff.toml`) |

## Commands

### 1. Postgres — localhost:5432
```bash
docker-compose up -d              # canonical (Docker available)
# In this sandbox Docker is NOT available; Postgres 17 is installed via apt:
sudo pg_ctlcluster 17 main start  # role/db/password: researchforge/researchforge/researchforge
```

### 2. Backend — http://localhost:8000
```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt        # lock file is only for the Docker build
uvicorn server:app --reload --port 8000
```
Health: `curl http://localhost:8000/api/health` → `{"status":"ok","checks":{"env_vars":"ok","database":"ok"}}`

### 3. Frontend — http://localhost:5173
```bash
cd frontend
yarn install      # yarn 1.22.22 (corepack enable)
yarn dev          # Vite dev server on :5173; proxies /api/* to localhost:8000
yarn build        # vite build → dist/
```

### Checks
```bash
backend/.venv/bin/ruff check backend/    # lint — passed at onboarding
(cd frontend && yarn build)              # production build — compiles clean
```
Backend has an 81-test pytest suite (`backend/.venv/bin/python -m pytest` from
`backend/`). CI (`.github/workflows/ci.yml`) runs ruff lint, the pytest suite,
and the Vite production build. No frontend unit-test suite and no typecheck
config exist yet.

## Environment Variables (backend/.env)

| Variable | Required | Notes |
| --- | --- | --- |
| `OPENAI_API_KEY` | yes | every LLM agent (document/factcheck/outline/synthesis) |
| `TAVILY_API_KEY` | yes | research agent web search |
| `DATABASE_URL` | yes | default local: `postgresql://researchforge:researchforge@localhost:5432/researchforge` |
| `CORS_ORIGINS` | yes | comma-separated origins, e.g. `http://localhost:3000` |
| `LANGCHAIN_*` | no | LangSmith tracing (off by default) |

- `backend/.env.example` is tracked and lists all required variables —
  copy it to `backend/.env` and fill in real values (resolved 2026-09-17;
  it had previously been referenced but missing).
- Onboarding used **placeholder** OpenAI/Tavily keys (startup validates presence only).
  Real keys are required for live research/LLM calls — TODO(confirm): inject via the
  credentials flow.

## Ports

| Service | Port |
| --- | --- |
| FastAPI backend | 8000 (binds 127.0.0.1) |
| Vite frontend | 5173 (proxies /api → 8000) |
| Postgres | 5432 |

## Local Verification Summary

**Dev stack healthy: true** — verified 2026-09-17 (UTC) in sandbox `cmp_cRIS4QDw`:

- Postgres online on localhost:5432; `sessions` table and LangGraph checkpoint schema
  auto-created at backend startup.
- Backend `uvicorn server:app --reload --port 8000` on 127.0.0.1:8000;
  `GET /api/health` → 200 `{"status":"ok","checks":{"env_vars":"ok","database":"ok"}}`.
- Frontend dev server on localhost:3000 — "Compiled successfully"; `GET /` → 200 and
  `GET /api/health` via the CRA proxy → 200.
- **Primary user flow (browser automation, Playwright Chromium):** loaded the SPA,
  filled the topic, selected depth, clicked Generate Report → `POST /api/run` →
  LangGraph pipeline executed (supervisor routing, Tavily research retries) → UI polled
  `GET /api/session/{id}/status` → session persisted in Postgres and reached a terminal
  state. Zero console errors or page errors. Screenshots: `/tmp/evidence/01-initial-ui.png`,
  `02-run-in-progress.png`, `03-run-outcome.png`.
- SSE: `GET /api/session/{id}/stream` → 200 `text/event-stream`, 36 events captured
  (`/tmp/evidence/sse-stream.txt`).
- API flow (curl): `POST /api/run` → 200 + session_id; `GET /api/session/{id}` returns
  the persisted state from Postgres.
- Lint: `ruff check backend/` → all checks passed. Build: `yarn build` → compiled successfully.
- **Boundary:** live web research and LLM calls need real `OPENAI_API_KEY` /
  `TAVILY_API_KEY`. With placeholder keys the pipeline runs, retries, and terminates with
  an explicit in-state error ("Research returned no results after searching Tavily…").
  Full report generation was therefore NOT exercised end-to-end.

## Codebase Map

See `codebase-map.md`. Highlights: `backend/graph/` is the LangGraph pipeline
(supervisor + six agents); `backend/server.py` is the entire FastAPI surface;
`backend/db.py` is the Postgres session store; `frontend/src/store.js` is the Zustand
store plus all API/SSE wiring.

## Gotchas

- `server.py` strips every proxy env var at startup (Tavily/OpenAI need direct egress).
  Add domains to `NO_PROXY` if you must run behind a proxy.
- (Resolved) The CRA-era note about link-local NIC IPs breaking the dev server
  (`empty allowedHosts` → `DANGEROUSLY_DISABLE_HOST_CHECK=true yarn start`)
  applied to react-scripts/webpack-dev-server and is obsolete — PR #3 migrated
  the frontend to Vite, whose dev server starts cleanly in this sandbox.
- `backend/Dockerfile` installs pinned `requirements.lock`; local dev uses
  `requirements.txt`. Keep both in sync.
- Rate limits: `/api/run` 10/min per IP; `/api/upload-pdf` 30/min per IP.
- Session TTL 2h; uploads/PDFs live under `/tmp/researchforge_uploads` and
  `/tmp/researchforge_pdfs`.
- Run detached servers under tmux (e.g. `tmux new-session -d -s backend …`);
  poll logs instead of sleeping >60s in a foreground command.
