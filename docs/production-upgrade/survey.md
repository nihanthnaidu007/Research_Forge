# ResearchForge Production-Readiness Survey

*Read-only survey, performed directly by the orchestrator after two worker attempts failed with a transient runtime error. Evidence: README.md, backend/server.py (1,086 lines), backend/db.py, backend/Dockerfile, docker-compose.yml, .gitignore, git ls-files, frontend/package.json, frontend/vercel.json — all inspected on sandbox cmp_cRIS4QDw. No files were modified.*

## 1. What the product is

ResearchForge is a **multi-agent research-report generator** with human-in-the-loop outline review:

- **Backend:** FastAPI (`backend/server.py`, ~1,086 lines) + six-agent LangGraph pipeline (`backend/graph/graph.py`): Research (Tavily live web search) → Document (PDF text extraction + GPT-4o summary) → FactCheck (parallel claim verification via LangGraph `Send()` fan-out) → Outline (**interrupt: human approves the outline**) → Synthesis (per-section writing with inline source refs) → Citations (rule-based dedupe + numbering). Final report exports to PDF via ReportLab (`backend/export/pdf_exporter.py`, 655 lines).


- **Persistence:** Postgres `sessions` table + `langgraph-checkpoint-postgres` checkpoints — the backend is restart-safe mid-run. Session TTL 2h, cleanup loop every 30 min.


- **API surface:** `POST /api/run` (SSE-streamed pipeline), `/api/session/{id}` + `/status` + `/stream`, `POST /api/approve-outline`, `POST /api/upload-pdf`, PDF download, `/api/health`.


- **Frontend:** React 19 + Zustand + Tailwind on **CRA/craco** (`react-scripts` 5.0.1); SSE progress panel, outline-approval UI, PDF/report viewer.


- **Deploy:** Railway (backend, `railway.toml` + multi-stage non-root Dockerfile) + Vercel (frontend, `vercel.json` rewrites proxying `/api/*` to a **hardcoded** Railway URL).


- **Hygiene positives:** request-ID middleware, SlowAPI rate limits (10/min run, 30/min upload per IP), 600s graph execution timeout, upload validation (size-bounded read, `%PDF-` magic-byte check, filename sanitization), non-root container user, pinned `requirements.lock` (tracked in git, used by the Dockerfile), ruff config, thorough README with a runbook.



## 2. Maturity signals

| Signal | State |
| --- | --- |
| Tests | **None.** Zero project tests (only `.venv` site-packages noise matches `test*`) |
| CI/CD | **None** — no `.github/` at all |
| Lint / types | ruff configured (`.ruff.toml`) but no CI enforcement; no type checking |
| Error handling | Mixed: PDF export returns generic client errors (good); but graph-run failures persist raw `str(e)` into client-visible session state (`server.py` lines 468, 644) |
| Secrets | Clean scan — no hardcoded keys; env via `dotenv` + startup validation |
| Packaging | Solid Dockerfiles, but **`backend/.env.example` is missing** while README step 1 says `cp backend/.env.example backend/.env` — onboarding is broken |
| Dependencies | `requirements.txt` uses loose `>=` ranges for host installs; Docker uses the pinned lock — two sources of truth, drift-prone |
| Docs | Strong README (architecture, env table, runbook, deploy guides) |

## 3. Production-readiness gaps (specific to this product)

- **NO AUTH, NO OWNERSHIP (blocking):** every endpoint is public. `session_id` — a server-generated UUID returned in the run response — is the *only* access control on `/api/session/{id}`, its SSE stream, outline approval, and the PDF download. Anyone who learns or guesses an ID reads the full report and state, or approves an outline. There is no API-key option even for the run endpoint; the only limit is 10/min per IP.


- **No cost-control ceiling:** per-IP rate limits don't stop distributed abuse of an endpoint that triggers multi-minute GPT-4o pipeline runs (web research + parallel fact-check + section synthesis). No global concurrency cap, queue, or per-run token budget.


- **Raw exceptions reach clients:** `run_graph_async` and the resume path store `str(e)` in session state, which the frontend renders — internal error text (potential URLs, provider details) leaks to the UI.


- **Zero test / CI coverage:** the entire lifecycle (run → upload → approve → download) is untested; nothing prevents regressions.


- **Broken first-run setup:** missing `.env.example` breaks the documented onboarding path.


- **Dead/duplicated agent code:** `factcheck.py` and `factcheck_parallel.py` both exist — one is legacy; confusion risk.


- **Frontend on deprecated tooling:** CRA + `react-scripts` 5 with React 19 is an unsupported combination headed for breakage.


- **Hardcoded deploy URL:** `frontend/vercel.json` pins `https://researchforge-backend.up.railway.app` — forked/renamed deployments silently break.


- **Dual dependency truth:** host installs (loose ranges) vs Docker (lock) can diverge; no Dependabot/renovate.


- **Observability is thin:** LangSmith optional; no metrics, no token-cost accounting per run (cost visibility matters when every run bills GPT-4o + Tavily).



## 4. Prioritized upgrade shortlist

1. **BLOCKING — Add authentication and session ownership.** API-key (or lightweight token) gate on all routes; verify a session token/header before allowing stream, approve, or download. (`server.py`)


2. **BLOCKING — Global run concurrency limit + queue** (e.g. max N active graph executions) and per-run token budget, so one abusive client can't burn the OpenAI bill. (`server.py`, `graph/graph.py`)


3. **Stop leaking raw exceptions:** sanitize `str(e)` before persisting/returning; return structured error envelopes with request IDs. (`server.py:468,644`)


4. **Test suite:** pytest + FastAPI TestClient integration tests over the run/upload/approve/download lifecycle with mocked OpenAI/Tavily; unit tests for `utils/validation.py`, `utils/scoring.py`, `utils/versioning.py`.


5. **CI workflow:** GitHub Actions running pytest + `ruff check` + frontend build on PR.


6. **Ship `backend/.env.example`** matching the README env table — fixes the broken setup step.


7. **Migrate frontend CRA/craco → Vite** (React 19 support, faster builds, maintained toolchain).


8. **Single source of dependency truth:** install from `requirements.lock` everywhere (or generate `requirements.txt` from it); add Dependabot.


9. **De-parameterize `vercel.json`:** make the backend URL an env var (`BACKEND_URL`) instead of a hardcoded personal Railway URL.


10. **Observability baseline:** structured JSON logging, `/metrics` (Prometheus), per-run token/cost accounting logged with the session.


11. **Remove dead code:** delete or consolidate `graph/agents/factcheck.py` vs `factcheck_parallel.py`.


12. **Product features for a usable product:** report history list (past sessions with status), markdown/HTML export alongside PDF, editable outline before approval, shareable read-only report links.



*No blocking packaging defects found — the Dockerfile correctly builds from the tracked, pinned `requirements.lock` (unlike Nixus-Sql's lockfile issue).*
