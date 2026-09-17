# ResearchForge — Codebase Map

Folder-level map (depth ≤ 2). Backend is a FastAPI + LangGraph pipeline; frontend is a CRA React 19 SPA.

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
| `frontend/` | CRA app | React 19 SPA; `yarn start` dev on :3000, `yarn build` → nginx image; `vercel.json` rewrites for prod |
| `frontend/src/` | source | App shell, Zustand store, components |
| `frontend/src/components/` | dir | Sidebar (topic/depth/PDF/URL input), MainPanel, AgentStatus, OutlineApproval (HITL gate), ReportOutput, TraceLog |
| `frontend/src/components/ui/` | dir | Radix-based primitives (button, input, tabs, textarea, scroll-area…) |
| `frontend/public/` | static | CRA public assets + `index.html` |
| repo root | files | `docker-compose.yml` (Postgres only), `railway.toml`, `.ruff.toml`, README, architecture SVG |
