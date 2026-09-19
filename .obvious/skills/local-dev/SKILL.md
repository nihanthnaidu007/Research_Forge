---
name: local-dev
description: Bring up and verify the ResearchForge dev stack (Postgres + FastAPI backend + Vite frontend) in this sandbox
---

# Local Dev — ResearchForge

Durable record of the 2026-09-17 onboarding run in sandbox `cmp_cRIS4QDw`
(snapshot `i0lim2flcer19du8gmluq`, built 2026-09-17T15:59:50.575Z).

## Requirements

- Python 3.11+ (sandbox has 3.13 — `requirements.txt` installs cleanly), Node 20+, Yarn 1.22.22
- Postgres on localhost:5432 with db/user/pass `researchforge`
- `backend/.env` with OPENAI_API_KEY, TAVILY_API_KEY, DATABASE_URL, CORS_ORIGINS
  (placeholders pass startup validation; real keys only matter for live LLM/research calls)

## Bring-up sequence (verified working)

1. **Postgres** — `docker-compose up -d` when Docker exists. This sandbox has no Docker:
   Postgres 17 was installed via apt and is already provisioned
   (`sudo pg_ctlcluster 17 main start`). DB `researchforge`, role `researchforge`,
   password `researchforge`, port 5432.
2. **Backend** — `cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
   then `tmux new-session -d -s backend '.venv/bin/uvicorn server:app --reload --port 8000'`.
   Startup validates env vars, creates the `sessions` table and LangGraph checkpoint schema.
   Verify: `curl http://localhost:8000/api/health` → 200 with `database: ok`.
3. **Frontend** — `cd frontend && corepack enable && yarn install --frozen-lockfile`
   then `tmux new-session -d -s frontend 'yarn dev'`.
   Wait for Vite's "ready" banner → http://localhost:5173 (proxies `/api` → 8000).
4. **Verify the flow** — browser: load :5173, fill `[data-testid="topic-input"]`, pick a
   depth tab, click `[data-testid="run-report-btn"]`; the UI polls
   `/api/session/{id}/status`. API: `POST /api/run`, then `GET /api/session/{id}` and
   `GET /api/session/{id}/stream` (SSE).

## Sandbox-specific deviations (env-only, no repo changes)

- **No Docker** → apt-installed Postgres 17 instead of the compose `postgres:16-alpine`
  image. Same credentials/DB/port, so `DATABASE_URL` matches the README default.
- **CRA-era host-check workaround is obsolete**: the old
  `DANGEROUSLY_DISABLE_HOST_CHECK=true yarn start` dance applied to
  react-scripts (webpack-dev-server) and no longer applies — PR #3 migrated
  the frontend to Vite, whose dev server starts cleanly on a link-local NIC.
- **No python3.11** in this image — Python 3.13 + `requirements.txt` (>= ranges) works.
- **No preinstalled yarn** — `sudo corepack enable` activates yarn 1.22.22 per the
  packageManager field.
- Playwright Chromium for browser verification: `mkdir /tmp/pwtest && cd /tmp/pwtest &&
  npm i playwright && ./node_modules/.bin/playwright install --with-deps chromium`.

## Evidence captured during onboarding (paths in sandbox)

- `/tmp/evidence/01-initial-ui.png`, `02-run-in-progress.png`, `03-run-outcome.png` —
  browser flow screenshots (zero console errors/pageerrors)
- `/tmp/evidence/api-run-response.json`, `completed-session.json`, `sse-stream.txt` (36 SSE
  events), `sse-headers.txt`, `session_id.txt`
- `ruff check backend/` → all checks passed; `yarn build` → compiled successfully
- `/tmp/uvicorn.log`, `/tmp/cra.log`, `/tmp/cra-build.log` — server logs

## Known boundaries

- Full report generation requires real OPENAI_API_KEY + TAVILY_API_KEY. With placeholders
  the pipeline runs, retries 3x per query, and ends with an explicit in-state error
  ("Research returned no results after searching Tavily…") — session still reaches a
  terminal state and persists to Postgres.
- Backend has an 81-test pytest suite (`cd backend && python -m pytest`); no
  frontend unit tests and no typecheck config yet — frontend verification is
  lint + build + runtime flows.
- `server.py` strips HTTP(S)_PROXY env vars at startup (Tavily/OpenAI need direct egress).
