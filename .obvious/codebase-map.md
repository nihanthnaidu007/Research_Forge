# ResearchForge — Codebase Map

Folder-level map (depth ≤ 2). Backend is a FastAPI + LangGraph pipeline; frontend is a Vite React 19 SPA (migrated from CRA/craco in PR #3).

| Path | Kind | Purpose |
| --- | --- | --- |
| `backend/` | Python app | FastAPI service + multi-agent pipeline; run `uvicorn server:app` from here |
| `backend/server.py` | file | Entire API surface: SSE streaming, rate limits, PDF upload/export, CORS, lifespan (env validation, DB setup, cleanup loop) |
| `backend/db.py` | file | Postgres session store — psycopg3 `ConnectionPool`, `sessions` table CRUD |
| `backend/graph/` | package | LangGraph wiring: `graph.py` (compile + checkpointer), `supervisor.py` (routing), `state.py` (`ReportState`) |
| `backend/graph/agents/` | package | Six agent nodes: research, document, factcheck (+ factcheck_parallel Send-fanout), outline, synthesis, citations |
| `backend/utils/` | package | OpenAI/Tavily client singletons, env validation, URL validation, scoring, versioning |
| `backend/eval/` | package | LangSmith tracer setup |
| `backend/export/` | package | ReportLab PDF exporter |
| `frontend/` | Vite app | React 19 SPA; `yarn dev` on :5173 (proxies `/api` → 8000 via `vite.config.js`), `yarn build` → `dist/` → nginx image; `vercel.json` rewrites for prod |
| `frontend/src/` | source | App shell, Zustand store, components |
| `frontend/src/components/` | dir | Sidebar (topic/depth/PDF/URL input), MainPanel, AgentStatus, OutlineApproval (HITL gate), ReportOutput, TraceLog |
| `frontend/src/components/ui/` | dir | Radix-based primitives (button, input, tabs, textarea, scroll-area…) |
| `frontend/index.html` | file | Vite entry HTML (project root — Vite does not use `CRA public/` + `react-scripts`) |
| repo root | files | `docker-compose.yml` (Postgres only), `railway.toml`, `.ruff.toml`, README, architecture SVG |
