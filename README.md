# ResearchForge

Multi-agent research report generation system with human-in-the-loop outline review, parallel fact-checking, and PDF export.

![Python 3.11](https://img.shields.io/badge/python-3.11-blue) ![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688) ![LangGraph](https://img.shields.io/badge/LangGraph-1.x-1c3d5a) ![React 19](https://img.shields.io/badge/React-19-61dafb) ![PostgreSQL 16](https://img.shields.io/badge/PostgreSQL-16-336791)

---

## What It Does

ResearchForge generates citation-backed research reports through a six-agent
LangGraph pipeline. A user submits a topic; the system performs live web
research (Tavily), processes any uploaded PDFs, fan-outs claim verification
across the corpus in parallel via the LangGraph `Send()` API, drafts an
outline, then pauses for human approval before writing. After outline
approval, the synthesis agent writes one section at a time, the citation
agent deduplicates and numbers references, and the system emits a final
PDF via ReportLab. All session state is persisted to Postgres so the
backend is safe to restart mid-run.

---

## Architecture

```
              ┌─────────────────────────────────────────────────────┐
              │                  Supervisor (routes)                │
              └─────────────────────────────────────────────────────┘
                                       │
   ┌─────────────┬─────────────┬───────┴───────┬─────────────┬─────────────┐
   ▼             ▼             ▼               ▼             ▼             ▼
Research     Document     FactCheck         Outline      Synthesis     Citations
(Tavily)     (PDF text)   (Send-fanout)     (HITL gate)  (per-section) (dedupe)
                              │
                              ▼
                       N parallel claim
                       verifications, then
                       merge → supervisor

User → Research → Document → FactCheck (parallel) → Outline →
       [interrupt: human approves outline] →
       Synthesis (per-section) → Citations → PDF Export
```

| Agent      | Responsibility                                                | LLM            |
| ---------- | ------------------------------------------------------------- | -------------- |
| Research   | Live web search via Tavily; collects source documents         | — (tool only)  |
| Document   | Extracts and summarizes text from uploaded PDFs               | GPT-4o         |
| FactCheck  | Verifies extracted claims in parallel against the source set  | GPT-4o         |
| Outline    | Drafts section structure; graph pauses for human approval     | GPT-4o         |
| Synthesis  | Writes each section with inline source references             | GPT-4o         |
| Citations  | Deduplicates URLs across sections; assigns numbered refs      | — (rule-based) |

---

## Repository Structure

```
.
├── backend/
│   ├── Dockerfile                  Multi-stage build, non-root user
│   ├── server.py                   FastAPI app, SSE streaming, rate limiting
│   ├── db.py                       Postgres session store + connection pool
│   ├── requirements.txt            Human-edited top-level deps
│   ├── requirements.lock           Pinned transitive deps for reproducible builds
│   ├── graph/
│   │   ├── graph.py                LangGraph compilation; Send() fan-out wiring
│   │   ├── supervisor.py           Routes between agents
│   │   ├── state.py                ReportState TypedDict
│   │   └── agents/                 Six agent node modules (one per pipeline step)
│   ├── utils/                      Shared OpenAI/Tavily client singletons,
│   │                               URL validation, scoring helpers
│   ├── eval/langsmith_tracer.py    LangSmith trace setup
│   └── export/pdf_exporter.py      ReportLab PDF generation
├── frontend/
│   ├── Dockerfile                  Two-stage: yarn build → nginx
│   ├── nginx.conf                  SPA routing + SSE-friendly /api proxy
│   ├── vercel.json                 Production rewrites → Railway backend
│   ├── package.json                React 19 + Zustand + Tailwind + craco
│   └── src/                        App, store, components
├── docker-compose.yml              Local-dev Postgres (only)
├── railway.toml                    Railway backend deploy config
├── .ruff.toml                      Lint + format rules
└── README.md
```

---

## Local Development

ResearchForge runs the backend and frontend directly on the host.
Docker is used only to provide Postgres.

### Prerequisites

- Python 3.11+
- Node.js 20+ with Yarn
- Docker Desktop (for the Postgres container)
- An OpenAI API key and a Tavily API key

### 1. Clone and configure

```bash
git clone <your-repo-url>
cd Research_Forge
cp backend/.env.example backend/.env
# Edit backend/.env and fill in OPENAI_API_KEY and TAVILY_API_KEY
```

### 2. Start Postgres

```bash
docker-compose up -d
```

This launches `researchforge-postgres` on `localhost:5432` with the
credentials baked into `docker-compose.yml`. The default
`DATABASE_URL` in `backend/.env.example` matches.

### 3. Install backend dependencies and run

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn server:app --reload --port 8000
```

The server validates required env vars on startup and creates the
`sessions` table on first connection. Health: `http://localhost:8000/api/health`.

### 4. Install frontend dependencies and run

```bash
cd frontend
yarn install
yarn start
```

The CRA dev server runs on `http://localhost:3000` and proxies `/api/*`
to `localhost:8000` via the `proxy` field in `package.json`.

### 5. Open the app

Visit `http://localhost:3000`.

---

## Environment Variables

All variables below live in `backend/.env`. The frontend needs none in
either local dev (handled by CRA proxy) or production (handled by
`vercel.json` rewrites).

| Variable               | Required | Default                | Description                                                                                                          |
| ---------------------- | -------- | ---------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `OPENAI_API_KEY`       | Yes      | —                      | Used by every agent that calls GPT-4o (Document, FactCheck, Outline, Synthesis).                                     |
| `TAVILY_API_KEY`       | Yes      | —                      | Used by the Research agent for live web search.                                                                       |
| `DATABASE_URL`         | Yes      | —                      | Postgres DSN. Persists sessions and LangGraph checkpoints. Railway injects this when a Postgres addon is provisioned. |
| `CORS_ORIGINS`         | Yes      | —                      | Comma-separated list of allowed frontend origins. Use the Vercel deployment URL in production.                       |
| `LANGCHAIN_TRACING_V2` | No       | `false`                | Enables LangSmith tracing of every LLM call and graph node.                                                          |
| `LANGCHAIN_API_KEY`    | No       | —                      | Required if `LANGCHAIN_TRACING_V2=true`.                                                                             |
| `LANGCHAIN_PROJECT`    | No       | `ResearchForge`        | LangSmith project name; traces are bucketed under it.                                                                 |
| `LANGCHAIN_ENDPOINT`   | No       | LangSmith default      | Override only if using a self-hosted LangSmith instance.                                                              |

---

## Production Deployment — Railway (Backend)

1. Push the repo to GitHub.
2. Create a new Railway project and connect the GitHub repo.
3. Add the **Postgres** addon. Railway injects `DATABASE_URL`
   automatically into the backend service.
4. In **Variables**, set:
   - `OPENAI_API_KEY`
   - `TAVILY_API_KEY`
   - `CORS_ORIGINS` — e.g. `https://your-app.vercel.app`
   - LangSmith vars (optional): `LANGCHAIN_TRACING_V2`,
     `LANGCHAIN_API_KEY`, `LANGCHAIN_PROJECT`
5. Railway detects `railway.toml` at the repo root and builds the
   backend from `backend/Dockerfile`. No build command is needed.
6. The deploy section in `railway.toml` configures `/api/health` as the
   health-check path. Railway will mark the deploy healthy only when
   that endpoint returns 200.

---

## Production Deployment — Vercel (Frontend)

1. Edit `frontend/vercel.json` and replace the `destination` URL with
   your Railway backend URL (`https://<service>.up.railway.app/api/:path*`).
2. Connect the GitHub repo to Vercel.
3. In Vercel project settings, set **Root Directory** to `frontend/`.
4. Deploy. No env vars are required — `vercel.json` rewrites handle
   API routing.
5. After deploy, copy the Vercel URL into the Railway `CORS_ORIGINS`
   variable so the backend accepts requests from it.

---

## Running Checks / Linting

```bash
# Lint
ruff check backend/

# Format
ruff format backend/
```

Configuration lives at `.ruff.toml` at the repo root.

---

## Operational Notes (Runbook)

- **Session persistence**: Sessions and LangGraph checkpoints live in
  Postgres (`sessions` table + `langgraph-checkpoint-postgres`
  schema). The backend is safe to restart mid-run — in-flight runs
  resume from the last checkpoint after the next user action.
- **Uploaded PDFs**: stored at `/tmp/researchforge_uploads/`.
  Removed when the owning session expires.
- **Generated PDFs**: stored at `/tmp/researchforge_pdfs/`.
  Removed when the owning session expires.
- **Session TTL**: 2 hours (`SESSION_TTL_SECONDS = 7200` in
  `server.py`). The cleanup loop runs every 30 minutes.
- **LangSmith traces**: set `LANGCHAIN_TRACING_V2=true` to capture
  every LLM call, every graph node, and Tavily searches. Trace URL
  is returned in the `/api/run` response when tracing is enabled.
- **Rate limits**: `/api/run` is capped at 10/min per IP;
  `/api/upload-pdf` is capped at 30/min per IP (via SlowAPI).
- **Graph execution timeout**: 600 seconds per run
  (`GRAPH_EXECUTION_TIMEOUT` in `server.py`). Long runs hit this and
  are marked errored.
- **SSE streaming**: `/api/session/{id}/stream` emits progress
  events. The nginx config in `frontend/nginx.conf` disables proxy
  buffering so events reach the browser in real time.
- **Outbound proxy hygiene**: `server.py` strips `HTTP_PROXY` /
  `HTTPS_PROXY` env vars at startup because Tavily and OpenAI need
  direct outbound connections. If you must run behind a proxy, add
  the relevant domains to `NO_PROXY`.

---

## Tech Stack

| Layer              | Technology                       | Version    |
| ------------------ | -------------------------------- | ---------- |
| LLM Orchestration  | LangGraph                        | >=1.0      |
| LLM Provider       | OpenAI GPT-4o                    | —          |
| Web Research       | Tavily                           | —          |
| Backend            | FastAPI + uvicorn                | >=0.115    |
| Database           | PostgreSQL + psycopg3            | 16         |
| Checkpointing      | langgraph-checkpoint-postgres    | >=2.0      |
| Frontend           | React 19 + Zustand               | —          |
| PDF Export         | ReportLab                        | >=4.0      |
| Tracing            | LangSmith                        | optional   |
