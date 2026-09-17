"""
ResearchForge - Multi-Agent Research Report System
FastAPI Backend with SSE Streaming
"""

import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.cors import CORSMiddleware

# Clear any system proxy environment variables that block outbound API calls
# Tavily, OpenAI, and LangSmith all require direct outbound connections
for _proxy_var in [
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "http_proxy",
    "https_proxy",
    "ALL_PROXY",
    "all_proxy",
    # tavily-python can honor these explicitly if present
    "TAVILY_HTTP_PROXY",
    "TAVILY_HTTPS_PROXY",
    "tavily_http_proxy",
    "tavily_https_proxy",
]:
    os.environ.pop(_proxy_var, None)

# Extra safety: ensure common API domains bypass any remaining proxy config.
_no_proxy_domains = (
    "api.tavily.com,api.openai.com,api.smith.langchain.com,smith.langchain.com"
)
for _no_proxy_var in ["NO_PROXY", "no_proxy"]:
    _existing = os.environ.get(_no_proxy_var, "").strip()
    if _existing:
        if _no_proxy_domains not in _existing:
            os.environ[_no_proxy_var] = f"{_existing},{_no_proxy_domains}"
    else:
        os.environ[_no_proxy_var] = _no_proxy_domains

# Load environment
ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

# Import graph components
from auth import (
    SESSION_TOKEN_HEADER,
    enforce_session_ownership,
    extract_session_token,
    generate_session_token,
    hash_session_token,
    require_api_key,
    require_session_ownership,
)
from chat import (
    MAX_CHAT_MESSAGE_CHARS,
    MAX_TRANSCRIPT_MESSAGES,
    run_chat_turn,
)
from db import cleanup_old_sessions as db_cleanup_sessions
from db import create_session, get_session, list_sessions, setup_db, update_session
from eval.langsmith_tracer import (
    get_trace_url,
    is_tracing_enabled,
    setup_tracing,
)
from export.bibtex_exporter import build_bibtex_report
from export.markdown_exporter import build_html_report, build_markdown_report
from graph.graph import get_checkpointer, get_graph
from graph.state import create_initial_state
from graph.supervisor import max_research_rounds
from utils import token_budget
from utils.clients import validate_env_vars
from utils.validation import validate_url

limiter = Limiter(key_func=get_remote_address)

import uuid as _uuid


class RequestIDMiddleware:
    """Injects a unique request ID into each request and response."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            request_id = str(_uuid.uuid4())[:8]
            scope["state"] = scope.get("state", {})
            scope["state"]["request_id"] = request_id

            async def send_with_id(message):
                if message["type"] == "http.response.start":
                    headers = dict(message.get("headers", []))
                    headers[b"x-request-id"] = request_id.encode()
                    message["headers"] = list(headers.items())
                await send(message)

            await self.app(scope, receive, send_with_id)
        else:
            await self.app(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown logic."""
    # --- Startup ---
    validate_env_vars()
    await asyncio.to_thread(setup_db)
    await asyncio.to_thread(get_checkpointer)
    setup_tracing()

    async def run_cleanup_loop():
        try:
            while True:
                await asyncio.sleep(1800)
                await asyncio.to_thread(_cleanup_sessions_and_files)
        except asyncio.CancelledError:
            logger.info("Session cleanup task cancelled — shutting down")

    cleanup_task = asyncio.create_task(run_cleanup_loop())
    logger.info(
        "ResearchForge API started — session cleanup scheduled every 30 minutes"
    )

    yield

    # --- Shutdown ---
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    logger.info("ResearchForge API shut down cleanly")


# Create the main app
app = FastAPI(
    title="ResearchForge API",
    description="Multi-Agent Research Report Generation System",
    version="1.0.0",
    lifespan=lifespan,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(RequestIDMiddleware)

# Create router with /api prefix
api_router = APIRouter(prefix="/api")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(process)d - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Temp directory for uploads
UPLOAD_DIR = Path("/tmp/researchforge_uploads")
UPLOAD_DIR.mkdir(exist_ok=True)
MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB


# --- Pydantic Models ---


class RunReportRequest(BaseModel):
    topic: str = Field(..., min_length=3, max_length=500)
    depth: str = Field(default="quick", pattern="^(quick|deep)$")
    input_urls: list[str] = Field(default_factory=list)
    uploaded_pdfs: list[str] = Field(default_factory=list)


class OutlineSection(BaseModel):
    section_id: str
    title: str
    description: str
    order: int


class ApproveOutlineRequest(BaseModel):
    session_id: str
    outline: list[OutlineSection]
    edits: str | None = None


class UpdateOutlineRequest(BaseModel):
    outline: list[OutlineSection]


class ChatRequest(BaseModel):
    # One chat turn: a single user message, capped at the schema level.
    message: str = Field(max_length=MAX_CHAT_MESSAGE_CHARS)


class SteerRequest(BaseModel):
    # Mid-run steering (W4). The command is validated manually (not a
    # Literal) because unknown commands must answer 400, not 422.
    command: str
    focus: str | None = Field(default=None, max_length=2000)


# ReportSession defines the schema for session metadata.
# Note: sessions are currently persisted as plain dicts in the database
# via db.py for flexibility. This model serves as the canonical schema
# reference and is used for response validation where applicable.
class ReportSession(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    topic: str
    depth: str
    # "paused" (W4) is non-terminal: a steering-paused run holds its place in
    # the checkpoint and resumes on command; polling continues while paused.
    status: Literal[
        "pending", "running", "waiting_approval", "complete", "error", "paused"
    ] = "pending"
    state: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


async def require_session_ownership_body(
    request: ApproveOutlineRequest,
    x_session_token: str | None = Header(default=None, alias=SESSION_TOKEN_HEADER),
    session_token: str | None = Query(default=None),
) -> None:
    """Ownership check for routes whose session_id arrives in the JSON body."""
    provided = extract_session_token(x_session_token, session_token)
    await enforce_session_ownership(request.session_id, provided)


# --- API Endpoints ---


@api_router.get("/", dependencies=[Depends(require_api_key)])
async def root():
    return {"message": "ResearchForge API", "version": "1.0.0"}


def _check_db() -> None:
    """Execute a trivial query to verify database connectivity."""
    from db import get_pool

    with get_pool().connection() as conn:
        conn.execute("SELECT 1")


@api_router.get("/health")
async def health_check():
    """
    Health check endpoint. Verifies database connectivity and
    required environment variables. Used by orchestration to detect
    broken instances.
    """
    health = {
        "status": "ok",
        "agents": [
            "research",
            "document",
            "factcheck",
            "outline",
            "synthesis",
            "citations",
        ],
        "model": "gpt-4o",
        "checks": {},
    }

    # Check required env vars
    required = ["OPENAI_API_KEY", "TAVILY_API_KEY", "DATABASE_URL", "CORS_ORIGINS"]
    missing = [v for v in required if not os.getenv(v, "").strip()]
    health["checks"]["env_vars"] = (
        "ok" if not missing else f"missing: {', '.join(missing)}"
    )

    # Check database connectivity
    try:
        await asyncio.to_thread(_check_db)
        health["checks"]["database"] = "ok"
    except Exception as e:
        health["checks"]["database"] = "error"
        health["status"] = "degraded"
        logger.error(f"Health check — database error: {e}")

    if health["status"] != "ok":
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=503, content=health)

    return health


@api_router.post("/run", dependencies=[Depends(require_api_key)])
@limiter.limit("10/minute")
async def run_report(
    request: Request, run_request: RunReportRequest, background_tasks: BackgroundTasks
):
    """Start a new research report generation"""

    # Input sanitization
    topic = run_request.topic.strip()
    if len(topic) < 5:
        raise HTTPException(
            status_code=400, detail="Topic must be at least 5 characters long"
        )
    if len(topic) > 500:
        raise HTTPException(
            status_code=400, detail="Topic must be under 500 characters"
        )

    # Validate URLs if provided
    valid_urls = []
    for url in run_request.input_urls:
        url = url.strip()
        if not url:
            continue
        is_safe, reason = validate_url(url)
        if is_safe:
            valid_urls.append(url)
        else:
            logger.warning(f"Rejected URL '{url[:80]}': {reason}")

    session_id = str(uuid.uuid4())
    session_token = generate_session_token()

    # Build LangSmith run name from topic (truncated, URL-safe)
    safe_topic = topic[:40].replace(" ", "-").lower()
    run_name = f"research-report-{safe_topic}-{session_id[:8]}"

    valid_pdfs = []
    for raw_path in run_request.uploaded_pdfs:
        try:
            pdf_path = Path(raw_path).resolve()
            if (
                pdf_path.parent == UPLOAD_DIR.resolve()
                and pdf_path.suffix.lower() == ".pdf"
                and pdf_path.exists()
            ):
                valid_pdfs.append(str(pdf_path))
            else:
                logger.warning(f"Rejected invalid PDF path: {raw_path}")
        except Exception:
            logger.warning(f"Could not resolve PDF path: {raw_path}")

    # Create initial state with sanitized inputs
    initial_state = create_initial_state(
        topic=topic,
        depth=run_request.depth,
        uploaded_pdfs=valid_pdfs,
        input_urls=valid_urls,
    )

    # Global cost ceiling: refuse to queue new graph executions at capacity.
    await acquire_run_slot()
    try:
        # Store session (persisting only the hash of the ownership token)
        await asyncio.to_thread(
            create_session,
            session_id,
            {
                "id": session_id,
                "topic": topic,
                "depth": run_request.depth,
                "status": "running",
                "run_name": run_name,
                "trace_url": get_trace_url() if is_tracing_enabled() else None,
                "state": initial_state,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            hash_session_token(session_token),
        )
    except Exception:
        release_run_slot()
        raise

    # Run graph in background
    background_tasks.add_task(run_graph_async, session_id)

    return {
        "session_id": session_id,
        "session_token": session_token,
        "status": "running",
        "message": f"Started research report generation for: {run_request.topic}",
    }


async def run_graph_async(session_id: str):
    """
    Run the LangGraph workflow with interrupt() support.
    The graph runs until it hits interrupt_before=['synthesis'] or completes.
    Uses thread_id for state persistence across invocations.
    """
    session = await asyncio.to_thread(get_session, session_id)
    if not session:
        logger.warning(f"Session {session_id} not found in run_graph_async")
        return

    token_budget.ensure_budget(session_id)
    session_ctx = token_budget.set_session_context(session_id)
    try:
        graph = get_graph()
        state = session["state"]

        # thread_id is the key that MemorySaver uses to persist state between invocations
        # Using session_id as thread_id ensures state continuity across the interrupt
        config = {
            "configurable": {"thread_id": session_id},
            "run_name": session.get("run_name", f"research-report-{session_id[:8]}"),
            "tags": ["research-forge", state.get("depth", "quick")],
            "metadata": {
                "session_id": session_id,
                "topic": state.get("topic", ""),
                "project": "Multi-Agent-Research",
            },
        }

        logger.info(
            f"Starting graph execution for session {session_id}, topic: {state.get('topic', 'unknown')}"
        )

        # Run graph in thread to avoid blocking the async event loop
        # The graph will pause at interrupt_before=['synthesis'] automatically
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(graph.invoke, state, config),
                timeout=GRAPH_EXECUTION_TIMEOUT,
            )
        except asyncio.TimeoutError:
            timeout_msg = (
                f"Graph execution timed out after "
                f"{int(GRAPH_EXECUTION_TIMEOUT)}s for session {session_id}"
            )
            logger.error(timeout_msg)
            # Terminal error — per-run budget released (W4 semantics).
            token_budget.release_budget(session_id)
            if session_id:
                existing = await asyncio.to_thread(get_session, session_id)
                if existing:
                    existing["state"]["error"] = (
                        "Report generation timed out. "
                        "This can happen when external APIs are slow. "
                        "Please try again."
                    )
                    await asyncio.to_thread(
                        update_session,
                        session_id,
                        {
                            "status": "error",
                            "state": existing["state"],
                        },
                    )
            return

        # Update session with result
        session["state"] = result
        await asyncio.to_thread(update_session, session_id, {"state": result})

        # Determine status from result
        next_agent = result.get("next_agent", "END")
        is_complete = result.get("is_complete", False)
        has_error = result.get("error")

        # When interrupt fires, next_agent will be "synthesis" (paused before it)
        # or "WAIT_FOR_HUMAN" from the supervisor
        if next_agent in ("synthesis", "WAIT_FOR_HUMAN") and not is_complete:
            session["status"] = "waiting_approval"
            timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
            session["state"]["stream_updates"].append(
                f"[{timestamp}] ⏸ Graph paused - outline ready for review (interrupt checkpoint saved)"
            )
            await asyncio.to_thread(
                update_session,
                session_id,
                {
                    "status": "waiting_approval",
                    "state": session["state"],
                },
            )
            logger.info(
                f"Session {session_id} paused at interrupt checkpoint - waiting for outline approval"
            )
        elif is_complete or next_agent == "END":
            session["status"] = "complete"
            # True completion — per-run budget released (W4 semantics).
            token_budget.release_budget(session_id)
            await asyncio.to_thread(update_session, session_id, {"status": "complete"})
            logger.info(f"Session {session_id} completed successfully")
        elif has_error:
            session["status"] = "error"
            session["state"]["error"] = has_error
            # Terminal error — per-run budget released (W4 semantics).
            token_budget.release_budget(session_id)
            await asyncio.to_thread(
                update_session,
                session_id,
                {
                    "status": "error",
                    "state": session["state"],
                },
            )
            logger.error(f"Session {session_id} error: {has_error}")
        else:
            session["status"] = "complete"
            # True completion — per-run budget released (W4 semantics).
            token_budget.release_budget(session_id)
            await asyncio.to_thread(update_session, session_id, {"status": "complete"})

    except token_budget.TokenBudgetExceeded:
        logger.error(f"Session {session_id}: per-run token budget exhausted")
        # Terminal error — per-run budget released (W4 semantics).
        token_budget.release_budget(session_id)
        existing = await asyncio.to_thread(get_session, session_id)
        if existing:
            existing["state"]["error"] = (
                "Report generation stopped: the token budget for this run "
                "was exhausted. Start a new run or raise RUN_TOKEN_BUDGET."
            )
            await asyncio.to_thread(
                update_session,
                session_id,
                {
                    "status": "error",
                    "state": existing["state"],
                },
            )
    except Exception as e:
        # Client-visible state gets a generic message plus a short reference;
        # raw exception text stays in the server log only.
        error_ref = uuid.uuid4().hex[:8]
        logger.error(
            f"Graph execution error for session {session_id} [ref {error_ref}]: {e}"
        )
        import traceback

        logger.error(traceback.format_exc())

        # Terminal error — per-run budget released (W4 semantics).
        token_budget.release_budget(session_id)
        existing = await asyncio.to_thread(get_session, session_id)
        if existing:
            existing["state"]["error"] = (
                "Report generation failed due to an internal error. "
                f"Reference: {error_ref}. Please retry."
            )
            await asyncio.to_thread(
                update_session,
                session_id,
                {
                    "status": "error",
                    "state": existing["state"],
                },
            )
    finally:
        token_budget.reset_session_context(session_ctx)
        # W4 per-run budget semantics: the budget is NOT released here. It
        # must survive the waiting_approval pause and any deep-research
        # rounds so one RUN_TOKEN_BUDGET ceilings the whole run; it is
        # released only at true completion or terminal error (the branches
        # above), and for abandoned sessions at TTL cleanup.
        release_run_slot()


async def resume_graph_after_approval(session_id: str, updated_state: dict):
    """
    Resume the graph from the interrupt checkpoint after outline approval.

    Critical LangGraph pattern:
      1. graph.update_state(config, updates) - merge approved outline into checkpoint
      2. graph.invoke(None, config) - None input = resume from interrupt, NOT restart

    Passing the full state to invoke() would start a NEW execution and hit the
    interrupt_before=['synthesis'] again. None tells LangGraph to continue.
    """
    session = await asyncio.to_thread(get_session, session_id)
    if not session:
        logger.warning(f"Session {session_id} not found in resume_graph_after_approval")
        return

    token_budget.ensure_budget(session_id)
    session_ctx = token_budget.set_session_context(session_id)
    try:
        graph = get_graph()

        # Same config as the initial run - thread_id must match for checkpoint continuity
        config = {
            "configurable": {"thread_id": session_id},
            "run_name": session.get("run_name", f"research-report-{session_id[:8]}"),
            "tags": ["research-forge", "resumed", updated_state.get("depth", "quick")],
            "metadata": {
                "session_id": session_id,
                "topic": updated_state.get("topic", ""),
                "phase": "synthesis-resume",
                "project": "Multi-Agent-Research",
            },
        }

        logger.info(f"Resuming graph from checkpoint for session {session_id}")

        # Step 1: Update the checkpointed state with the approved outline fields
        # This merges our updates into the existing checkpoint without restarting
        state_updates = {
            "approved_outline": updated_state["approved_outline"],
            "outline_approved": True,
            "user_outline_edits": updated_state.get("user_outline_edits"),
            "current_section_index": 0,
            "original_outline": updated_state.get("original_outline", []),
            "stream_updates": updated_state.get("stream_updates", []),
            "changed_section_ids": updated_state.get("changed_section_ids", []),
            "sections_needing_rewrite": updated_state.get(
                "sections_needing_rewrite", []
            ),
        }
        await asyncio.to_thread(graph.update_state, config, state_updates)
        logger.info(
            f"Checkpoint state updated for session {session_id} - outline_approved=True"
        )

        # Step 2: Resume from interrupt by passing None as input
        # interrupt_before=["synthesis"] fires for EVERY synthesis call (one per section).
        # We loop invoke(None, config) until the graph reaches END, an error,
        # or a steering pause command — see _drive_post_approval_iterations.
        await _drive_post_approval_iterations(session_id, graph, config, session)

    except token_budget.TokenBudgetExceeded:
        logger.error(
            f"Session {session_id}: per-run token budget exhausted during resume"
        )
        # Terminal error — per-run and round budgets released (W4 semantics).
        token_budget.release_budget(session_id)
        token_budget.release_round_budget(session_id)
        existing = await asyncio.to_thread(get_session, session_id)
        if existing:
            existing["state"]["error"] = (
                "Report generation stopped: the token budget for this run "
                "was exhausted. Start a new run or raise RUN_TOKEN_BUDGET."
            )
            await asyncio.to_thread(
                update_session,
                session_id,
                {
                    "status": "error",
                    "state": existing["state"],
                },
            )
    except Exception as e:
        # Client-visible state gets a generic message plus a short reference;
        # raw exception text stays in the server log only.
        error_ref = uuid.uuid4().hex[:8]
        logger.error(
            f"Graph resume error for session {session_id} [ref {error_ref}]: {e}"
        )
        import traceback

        logger.error(traceback.format_exc())

        # Terminal error — per-run and round budgets released (W4 semantics).
        token_budget.release_budget(session_id)
        token_budget.release_round_budget(session_id)
        existing = await asyncio.to_thread(get_session, session_id)
        if existing:
            existing["state"]["error"] = (
                "Report generation failed due to an internal error. "
                f"Reference: {error_ref}. Please retry."
            )
            await asyncio.to_thread(
                update_session,
                session_id,
                {
                    "status": "error",
                    "state": existing["state"],
                },
            )
    finally:
        token_budget.reset_session_context(session_ctx)
        # W4 per-run budget semantics: NOT released here. The budget persists
        # across the waiting_approval pause and deep-research rounds and is
        # released only at true completion or terminal error (the branches
        # above), or for abandoned sessions at TTL cleanup.
        release_run_slot()


# --- Mid-run steering (W4) -------------------------------------------------
#
# pause / resume / redirect for an active post-approval run. Commands ride
# the session row (inside the state JSONB) so they are visible to polling
# clients and survive process restarts; the shared driver consumes each
# command exactly once under the same per-session lock the endpoint writes
# with. No websockets — the UI observes effects via its status poll.

_steer_locks: dict[str, asyncio.Lock] = {}


def _get_steer_lock(session_id: str) -> asyncio.Lock:
    """One lock per session serializing steering writes against
    per-iteration state persists (chat-lock pattern)."""
    return _steer_locks.setdefault(session_id, asyncio.Lock())


def _apply_redirect_focus(state: dict, focus: str) -> dict:
    """
    Merge redirect focus into state and flag targeted sections for rewrite
    (pure function — returns exactly the checkpoint updates applied).

    A section is targeted when title+description share >= 2 words with the
    focus (the same overlap rule synthesis uses to match fact-check
    verdicts). Targeted sections are pruned from written_sections and
    current_section_index restarts at 0, so the unchanged-section versioning
    skip reuses every section NOT flagged. With no keyword match but written
    sections present, the weakest-confidence section is targeted — a
    redirect must have an observable effect. The returned updates mirror the
    outline-edit approval shape, so no graph node learns about redirects.
    """
    timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    merged_focus = f"{state.get('redirect_focus') or ''} {focus}".strip()

    outline = state.get("approved_outline") or state.get("outline", [])
    written = state.get("written_sections", [])
    written_ids = {s.get("section_id") for s in written}
    focus_words = set(focus.lower().split())

    def _matches(section: dict) -> bool:
        text = f"{section.get('title', '')} {section.get('description', '')}".lower()
        return len(focus_words & set(text.split())) >= 2

    flagged = [
        s.get("section_id")
        for s in outline
        if _matches(s) and s.get("section_id") in written_ids
    ]

    if not flagged and written:
        # Deterministic fallback: nothing matched by keywords — steer the
        # rewrite at the weakest written section so the redirect always has
        # an observable effect.
        scores = state.get("confidence_scores", {})
        if scores:
            weakest = min(scores.items(), key=lambda kv: kv[1])[0]
            if weakest in written_ids:
                flagged = [weakest]

    if not flagged:
        # Nothing to rewrite (e.g. redirect landed before any section was
        # written) — the focus still merges and will ground future synthesis
        # prompts via redirect_focus.
        state["redirect_focus"] = merged_focus
        state["stream_updates"] = state.get("stream_updates", []) + [
            f'[{timestamp}] ↗️ Redirect focus recorded: "{focus[:80]}"'
        ]
        return {
            "redirect_focus": merged_focus,
            "stream_updates": state["stream_updates"],
        }

    state["redirect_focus"] = merged_focus
    state["changed_section_ids"] = list(
        dict.fromkeys(state.get("changed_section_ids", []) + flagged)
    )
    state["sections_needing_rewrite"] = list(
        dict.fromkeys(state.get("sections_needing_rewrite", []) + flagged)
    )
    state["written_sections"] = [
        s for s in written if s.get("section_id") not in set(flagged)
    ]
    state["current_section_index"] = 0
    state["stream_updates"] = state.get("stream_updates", []) + [
        f"[{timestamp}] ↗️ Redirect applied — re-writing {', '.join(flagged)} "
        f'with focus: "{focus[:80]}"'
    ]
    return {
        "redirect_focus": state["redirect_focus"],
        "changed_section_ids": state["changed_section_ids"],
        "sections_needing_rewrite": state["sections_needing_rewrite"],
        "written_sections": state["written_sections"],
        "current_section_index": state["current_section_index"],
        "stream_updates": state["stream_updates"],
    }


async def _persist_iteration_state(session_id: str, result: dict) -> dict:
    """
    Persist one post-approval iteration's result under the steer lock.

    Session state is one JSONB blob updated by read-modify-write, so a
    steering command that arrives while the graph runs would be clobbered by
    this persist — preserve it for the next pre-invoke consumption (R3).
    """
    async with _get_steer_lock(session_id):
        fresh = await asyncio.to_thread(get_session, session_id)
        pending = ((fresh or {}).get("state") or {}).get("pending_command")
        state_to_store = dict(result)
        if pending is not None:
            state_to_store["pending_command"] = pending
        await asyncio.to_thread(update_session, session_id, {"state": state_to_store})
    return state_to_store


async def _consume_pending_command(session_id: str, graph, config: dict) -> dict | None:
    """
    Read-and-clear the pending steering command exactly once, under the
    per-session steer lock.

    A redirect additionally merges its focus updates into the LangGraph
    checkpoint: the driver resumes from the checkpoint (invoke(None)), not
    from the session row, so the prune/flag changes must land in both.
    """
    async with _get_steer_lock(session_id):
        fresh = await asyncio.to_thread(get_session, session_id)
        state = (fresh or {}).get("state") or {}
        payload = state.get("pending_command")
        if not payload:
            return None
        state["pending_command"] = None
        redirect_updates: dict | None = None
        focus = str(payload.get("focus") or "").strip()
        if payload.get("command") == "redirect" and focus:
            redirect_updates = _apply_redirect_focus(state, focus)
            state.update(redirect_updates)
        await asyncio.to_thread(update_session, session_id, {"state": state})
    if redirect_updates:
        await asyncio.to_thread(graph.update_state, config, redirect_updates)
    return payload


async def _drive_post_approval_iterations(
    session_id: str, graph, config: dict, session: dict
) -> None:
    """
    Shared post-approval driver loop (W4): iterate invoke(None) until the
    graph completes, errors, or a steering pause command lands.

    Before every invoke, a pending steering command is consumed (exactly
    once): pause stops the loop and parks the session in the non-terminal
    "paused" status; redirect merges focus into the checkpoint. After every
    invoke the result is persisted under the same lock, preserving any
    command that arrived mid-iteration. The caller holds the run slot and
    releases it in its own finally.

    Per-run budget semantics (W4): released ONLY at terminal exits —
    completion, error, or iteration-cap exhaustion. A pause exits without
    releasing: the run continues later on the same budget.
    """
    state = session.get("state", {})
    approved_outline_len = len(state.get("approved_outline", []))
    max_rounds = max_research_rounds()
    # Base headroom: one iteration per section plus buffer (pre-W4 rule);
    # each research round can add a research pass plus up to a full rewrite
    # walk, so the cap scales with the round limit.
    max_iterations = max(30, approved_outline_len * 3) + max_rounds * (
        approved_outline_len + 2
    )
    last_seen_rounds = int(state.get("research_rounds", 0) or 0)

    for iteration in range(max_iterations):
        command = await _consume_pending_command(session_id, graph, config)
        if command and command.get("command") == "pause":
            session["status"] = "paused"
            # State (incl. the cleared pending_command) is already persisted
            # by the consumer; only the status flip remains.
            await asyncio.to_thread(update_session, session_id, {"status": "paused"})
            logger.info(f"Session {session_id} paused by steering command")
            return

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(graph.invoke, None, config),
                timeout=GRAPH_RESUME_ITERATION_TIMEOUT,
            )
        except asyncio.TimeoutError:
            timeout_msg = (
                f"Synthesis iteration {iteration + 1} timed out after "
                f"{int(GRAPH_RESUME_ITERATION_TIMEOUT)}s for session {session_id}"
            )
            logger.error(timeout_msg)
            # Terminal error — per-run and round budgets released.
            token_budget.release_budget(session_id)
            token_budget.release_round_budget(session_id)
            existing = await asyncio.to_thread(get_session, session_id)
            if existing:
                existing["state"]["error"] = (
                    f"Report generation timed out on section "
                    f"{iteration + 1}. Please try again."
                )
                await asyncio.to_thread(
                    update_session,
                    session_id,
                    {
                        "status": "error",
                        "state": existing["state"],
                    },
                )
            return

        # Update session state after each section so streaming UI sees progress
        session["state"] = await _persist_iteration_state(session_id, result)
        await asyncio.to_thread(
            update_session, session_id, {"status": session["status"]}
        )

        # Round bookkeeping: a fresh per-round sub-cap for every gap-tripped
        # round (RESEARCH_ROUND_TOKEN_BUDGET; unset = unlimited).
        rounds = int(result.get("research_rounds", 0) or 0)
        if rounds > last_seen_rounds:
            token_budget.release_round_budget(session_id)
            token_budget.ensure_round_budget(session_id)
            last_seen_rounds = rounds
        next_agent = result.get("next_agent", "END")
        if next_agent in ("citations", "END") or result.get("is_complete"):
            # The round ended — drop the sub-cap so an idle round budget
            # cannot leak into a later round.
            token_budget.release_round_budget(session_id)

        is_complete = result.get("is_complete", False)
        has_error = result.get("error")

        logger.info(
            f"Post-approval iteration {iteration + 1} for session {session_id}: "
            f"next_agent={next_agent}, is_complete={is_complete}, "
            f"sections_written={len(result.get('written_sections', []))}, "
            f"research_rounds={rounds}"
        )

        if is_complete or next_agent == "END":
            session["status"] = "complete"
            await asyncio.to_thread(
                update_session,
                session_id,
                {
                    "status": session["status"],
                    "state": session["state"],
                },
            )
            # True completion — per-run budget released (W4 semantics).
            token_budget.release_budget(session_id)
            token_budget.release_round_budget(session_id)
            logger.info(f"Session {session_id} completed after outline approval")
            return
        elif has_error:
            session["status"] = "error"
            await asyncio.to_thread(
                update_session,
                session_id,
                {
                    "status": session["status"],
                    "state": session["state"],
                },
            )
            # Terminal error — per-run budget released (W4 semantics).
            token_budget.release_budget(session_id)
            token_budget.release_round_budget(session_id)
            logger.error(f"Session {session_id} error after resume: {has_error}")
            return
        # Otherwise interrupt fired again (next synthesis call) - keep resuming

    # max_iterations reached without completion
    logger.warning(
        f"Session {session_id} hit max post-approval iterations ({max_iterations})"
    )
    session["status"] = "error"
    session["state"]["error"] = "Graph did not complete within expected iterations"
    await asyncio.to_thread(
        update_session,
        session_id,
        {
            "status": session["status"],
            "state": session["state"],
        },
    )
    # Terminal error — per-run budget released (W4 semantics).
    token_budget.release_budget(session_id)
    token_budget.release_round_budget(session_id)


async def resume_after_pause(session_id: str):
    """
    Continue a steering-paused run from its checkpoint (W4).

    Same shape as resume_graph_after_approval minus the approval merge: the
    checkpoint already holds the interrupted state, so there is nothing to
    update — just re-enter the shared driver loop. The steer endpoint has
    already reacquired the run slot; this driver releases it.
    """
    session = await asyncio.to_thread(get_session, session_id)
    if not session:
        return
    token_budget.ensure_budget(session_id)
    session_ctx = token_budget.set_session_context(session_id)
    try:
        graph = get_graph()
        config = {
            "configurable": {"thread_id": session_id},
            "run_name": session.get("run_name", f"research-report-{session_id[:8]}"),
            "tags": ["research-forge", "steering-resume"],
            "metadata": {
                "session_id": session_id,
                "topic": (session.get("state") or {}).get("topic", ""),
                "phase": "steering-resume",
                "project": "Multi-Agent-Research",
            },
        }
        await _drive_post_approval_iterations(session_id, graph, config, session)
    except token_budget.TokenBudgetExceeded:
        logger.error(
            f"Session {session_id}: per-run token budget exhausted during resume"
        )
        # Terminal error — per-run and round budgets released (W4 semantics).
        token_budget.release_budget(session_id)
        token_budget.release_round_budget(session_id)
        existing = await asyncio.to_thread(get_session, session_id)
        if existing:
            existing["state"]["error"] = (
                "Report generation stopped: the token budget for this run "
                "was exhausted. Start a new run or raise RUN_TOKEN_BUDGET."
            )
            await asyncio.to_thread(
                update_session,
                session_id,
                {
                    "status": "error",
                    "state": existing["state"],
                },
            )
    except Exception as e:
        # Client-visible state gets a generic message plus a short reference;
        # raw exception text stays in the server log only.
        error_ref = uuid.uuid4().hex[:8]
        logger.error(
            f"Graph resume error for session {session_id} [ref {error_ref}]: {e}"
        )
        import traceback

        logger.error(traceback.format_exc())

        # Terminal error — per-run and round budgets released (W4 semantics).
        token_budget.release_budget(session_id)
        token_budget.release_round_budget(session_id)
        existing = await asyncio.to_thread(get_session, session_id)
        if existing:
            existing["state"]["error"] = (
                "Report generation failed due to an internal error. "
                f"Reference: {error_ref}. Please retry."
            )
            await asyncio.to_thread(
                update_session,
                session_id,
                {
                    "status": "error",
                    "state": existing["state"],
                },
            )
    finally:
        token_budget.reset_session_context(session_ctx)
        release_run_slot()


STEER_RATE_LIMIT = "30/minute"


async def _write_pending_command(session_id: str, command: dict) -> None:
    """
    Write the pending steering command under the per-session steer lock (the
    chat-lock pattern): the driver reads-and-clears the same field, so the
    write must never interleave with a per-iteration state persist (R3).

    One pending slot, last writer wins — pause/redirect are momentary
    intents, and a queued command replaced by a newer one is documented
    semantics the UI surfaces via polling.
    """
    async with _get_steer_lock(session_id):
        fresh = await asyncio.to_thread(get_session, session_id)
        if not fresh:
            raise HTTPException(status_code=404, detail="Session not found")
        state = fresh.get("state", {})
        state["pending_command"] = command
        await asyncio.to_thread(update_session, session_id, {"state": state})


@api_router.post(
    "/session/{session_id}/steer",
    dependencies=[Depends(require_api_key), Depends(require_session_ownership)],
)
@limiter.limit(STEER_RATE_LIMIT)
async def steer_session(
    request: Request,
    session_id: str,
    steer_request: SteerRequest,
    background_tasks: BackgroundTasks,
):
    """
    Mid-run steering (W4): pause, resume, or redirect an active run.

    pause/redirect write a pending command onto the session row; the shared
    driver consumes it before its next invoke(None) iteration. resume only
    applies to a paused session: it reacquires a run slot (429 + Retry-When
    saturated) and restarts the driver loop from the checkpoint. Effects
    surface via status polling (2-5s cadence) — no websockets.
    """
    command = (steer_request.command or "").strip().lower()
    if command not in ("pause", "resume", "redirect"):
        raise HTTPException(
            status_code=400,
            detail="Unknown steering command. Use pause, resume, or redirect.",
        )

    session = await asyncio.to_thread(get_session, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    status = session.get("status")
    state = session.get("state", {})

    if command == "pause":
        if status != "running":
            raise HTTPException(
                status_code=400,
                detail=f"Only a running report can be paused. Current status: {status}",
            )
        if not state.get("outline_approved"):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Steering is available once section writing is underway "
                    "(after outline approval)."
                ),
            )
        await _write_pending_command(session_id, {"command": "pause"})
        return {
            "status": "accepted",
            "command": "pause",
            "message": (
                "Pause requested — it takes effect before the next section "
                "iteration (a few seconds, visible via status polling)."
            ),
        }

    if command == "redirect":
        if status not in ("running", "paused"):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Only an active or paused report can be redirected. "
                    f"Current status: {status}"
                ),
            )
        if not state.get("outline_approved"):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Steering is available once section writing is underway "
                    "(after outline approval)."
                ),
            )
        focus = (steer_request.focus or "").strip()
        if not focus:
            raise HTTPException(
                status_code=400,
                detail="Redirect requires non-empty focus text describing what to emphasize.",
            )
        await _write_pending_command(
            session_id, {"command": "redirect", "focus": focus}
        )
        return {
            "status": "accepted",
            "command": "redirect",
            "message": (
                "Redirect queued — focus applies before the next section "
                "iteration (a few seconds, visible via status polling)."
            ),
        }

    # command == "resume"
    if status != "paused":
        raise HTTPException(
            status_code=400,
            detail=f"Only a paused report can be resumed. Current status: {status}",
        )
    await acquire_run_slot()
    try:
        await asyncio.to_thread(update_session, session_id, {"status": "running"})
        background_tasks.add_task(resume_after_pause, session_id)
    except Exception:
        release_run_slot()
        raise
    return {
        "status": "accepted",
        "command": "resume",
        "message": "Resume requested — the run continues from its checkpoint.",
    }


@api_router.get("/session/{session_id}/status", dependencies=[Depends(require_api_key)])
async def get_session_status(session_id: str):
    """Lightweight status check - returns only orchestration fields, not full state"""
    session = await asyncio.to_thread(get_session, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    state = session.get("state", {})

    return {
        "session_id": session_id,
        "status": session.get("status", "unknown"),
        "current_agent": state.get("current_agent", ""),
        "completed_agents": state.get("completed_agents", []),
        "stream_updates": state.get("stream_updates", []),
        "overall_confidence": state.get("overall_confidence", 0.0),
        "sections_written": len(state.get("written_sections", [])),
        "total_sections": len(state.get("approved_outline", [])),
        "sources_found": len(state.get("research_results", [])),
        "claims_checked": len(state.get("fact_check_results", [])),
        "research_rounds": int(state.get("research_rounds", 0) or 0),
        "coverage_gaps": state.get("coverage_gaps", []),
        "error": state.get("error"),
        "is_complete": state.get("is_complete", False),
        "has_outline": len(state.get("outline", [])) > 0,
        "outline": state.get("outline", [])
        if session.get("status") == "waiting_approval"
        else [],
        "trace_url": session.get("trace_url"),
        "tracing_enabled": is_tracing_enabled(),
        "updated_at": session.get("updated_at", session.get("created_at", "")),
        "versioning_report": session.get("versioning_report", None),
        "changed_section_ids": state.get("changed_section_ids", []),
        "unchanged_section_ids": [
            s.get("section_id")
            for s in state.get("approved_outline", [])
            if s.get("section_id") not in state.get("changed_section_ids", [])
        ],
    }


@api_router.get("/session/{session_id}", dependencies=[Depends(require_api_key)])
async def get_session_endpoint(session_id: str):
    """Get current session state"""
    session = await asyncio.to_thread(get_session, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return session


@api_router.get("/history", dependencies=[Depends(require_api_key)])
async def list_history(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """
    List past report sessions, newest first (history dashboard).

    Operator-level listing behind the API key. Returns metadata only —
    no report state and no token material.
    """
    sessions = await asyncio.to_thread(list_sessions, limit, offset)
    return {"count": len(sessions), "sessions": sessions}


@api_router.get(
    "/session/{session_id}/stream",
    dependencies=[Depends(require_api_key), Depends(require_session_ownership)],
)
async def stream_session(session_id: str):
    """Stream session updates via SSE"""
    session = await asyncio.to_thread(get_session, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    async def generate():
        last_update_count = 0
        heartbeat_counter = 0
        try:
            while True:
                session = await asyncio.to_thread(get_session, session_id)
                if not session:
                    break

                state = session.get("state", {})
                updates = state.get("stream_updates", [])

                if len(updates) > last_update_count:
                    for update in updates[last_update_count:]:
                        data = json.dumps(
                            {
                                "type": "update",
                                "message": update,
                                "status": session.get("status"),
                                "current_agent": state.get("current_agent", ""),
                                "completed_agents": state.get("completed_agents", []),
                            }
                        )
                        yield f"data: {data}\n\n"
                    last_update_count = len(updates)

                # "paused" (W4 steering) also ends the stream: it is
                # non-terminal, but nothing streams while paused — the UI
                # falls back to polling and re-subscribes on resume.
                if session.get("status") in [
                    "waiting_approval",
                    "paused",
                    "complete",
                    "error",
                ]:
                    data = json.dumps({"type": "state", "session": session})
                    yield f"data: {data}\n\n"
                    break

                # Send SSE comment as keepalive every 15 seconds (30 × 0.5s iterations)
                # SSE comments (": ...\n\n") are invisible to EventSource handlers
                # but prevent proxy idle-timeout disconnects.
                heartbeat_counter += 1
                if heartbeat_counter % 30 == 0:
                    yield ": keepalive\n\n"

                await asyncio.sleep(0.5)

        except asyncio.CancelledError:
            # Client disconnected — exit cleanly without logging an error
            logger.debug(f"SSE client disconnected for session {session_id}")

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@api_router.post(
    "/approve-outline",
    dependencies=[Depends(require_api_key), Depends(require_session_ownership_body)],
)
async def approve_outline(
    request: ApproveOutlineRequest, background_tasks: BackgroundTasks
):
    """
    Resume the graph after human-in-the-loop outline approval.
    Uses the same thread_id (session_id) to resume from the interrupt checkpoint.
    The graph continues from synthesis - research, factcheck, outline do NOT re-run.
    """
    session = await asyncio.to_thread(get_session, request.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.get("status") != "waiting_approval":
        raise HTTPException(
            status_code=400,
            detail=f"Session is not waiting for approval. Current status: {session.get('status')}",
        )

    state = session["state"]
    timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")

    # Update state with approved outline
    approved_outline = [s.model_dump() for s in request.outline]
    state["approved_outline"] = approved_outline
    state["outline_approved"] = True
    state["user_outline_edits"] = request.edits
    state["current_section_index"] = 0

    # Snapshot original outline for versioning diff
    if not state.get("original_outline"):
        state["original_outline"] = state.get("outline", []).copy()

    # Run versioning diff - identify which sections changed
    from utils.versioning import build_versioning_report

    original_outline = state.get("original_outline", [])
    versioning_report = build_versioning_report(
        original=original_outline,
        approved=approved_outline,
        written_sections=state.get("written_sections", []),
    )

    # Store versioning results in state
    state["changed_section_ids"] = versioning_report["changed_ids"]
    state["sections_needing_rewrite"] = versioning_report["rewrite_ids"]

    # Log and surface versioning summary to trace log
    state["stream_updates"].append(
        f"[{timestamp}] ✓ Outline approved - resuming graph from checkpoint (synthesis phase)"
    )
    state["stream_updates"].append(
        f"[{timestamp}] 🔍 Versioning → {versioning_report['summary']}"
    )

    if versioning_report["rewrite_ids"]:
        state["stream_updates"].append(
            f"[{timestamp}] 📝 Re-writing sections: {', '.join(versioning_report['rewrite_ids'])}"
        )

    if versioning_report["unchanged_ids"]:
        state["stream_updates"].append(
            f"[{timestamp}] ♻️ Reusing content for: {', '.join(versioning_report['unchanged_ids'])}"
        )

    logger.info(
        f"Versioning diff for session {request.session_id}: {versioning_report['summary']}"
    )

    await acquire_run_slot()
    try:
        await asyncio.to_thread(
            update_session,
            request.session_id,
            {
                "status": "running",
                "state": state,
                "versioning_report": versioning_report,
            },
        )

        # Resume graph from interrupt checkpoint using the SAME thread_id
        # This is the key difference from the old approach - we pass the UPDATED state
        # and the same config so MemorySaver knows which checkpoint to resume
        background_tasks.add_task(
            resume_graph_after_approval, request.session_id, state
        )
    except Exception:
        release_run_slot()
        raise

    return {
        "status": "approved",
        "message": "Outline approved - resuming report generation from checkpoint",
        "session_id": request.session_id,
    }


@api_router.post("/upload-pdf", dependencies=[Depends(require_api_key)])
@limiter.limit("30/minute")
async def upload_pdf(
    request: Request, session_id: str | None = Form(None), file: UploadFile = File(...)
):
    """Upload a PDF file for document ingestion"""
    if session_id is None:
        session_id = str(uuid.uuid4())

    # --- Size check ---
    # Read the file content once into memory (bounded by MAX_UPLOAD_BYTES + 1).
    # Reading one extra byte lets us detect oversized files without loading them
    # fully. UploadFile.read() is async-safe here.
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum upload size of {MAX_UPLOAD_BYTES // (1024 * 1024)} MB",
        )

    # --- Magic byte check ---
    # PDF files must begin with the %PDF- signature (hex 25 50 44 46 2D).
    # Extension-only checks are trivially bypassed by renaming any file.
    PDF_MAGIC = b"%PDF-"
    if not content[:5] == PDF_MAGIC:
        raise HTTPException(
            status_code=400,
            detail="File does not appear to be a valid PDF (invalid file signature)",
        )

    # --- Filename sanitization ---
    # Strip to the basename only (no directory components), remove null bytes,
    # remove all characters except alphanumeric, dots, hyphens, underscores,
    # and truncate to 100 characters to prevent filesystem issues.
    original_name = Path(file.filename).name  # basename only, drops any path
    original_name = original_name.replace("\x00", "")  # strip null bytes
    safe_name = "".join(c for c in original_name if c.isalnum() or c in (".", "-", "_"))
    if not safe_name or safe_name.startswith("."):
        safe_name = "upload.pdf"
    safe_name = safe_name[:100]
    safe_filename = f"{session_id}_{safe_name}"
    file_path = UPLOAD_DIR / safe_filename

    # --- Write to disk ---
    try:
        with open(file_path, "wb") as buffer:
            buffer.write(content)
    except Exception:
        raise HTTPException(
            status_code=500, detail="Failed to save uploaded file"
        ) from None

    # If session exists, update its state
    session = await asyncio.to_thread(get_session, session_id)
    if session:
        state = session["state"]
        uploaded_pdfs = state.get("uploaded_pdfs", [])
        if str(file_path) not in uploaded_pdfs:
            uploaded_pdfs.append(str(file_path))
        state["uploaded_pdfs"] = uploaded_pdfs
        state["has_documents"] = True
        await asyncio.to_thread(update_session, session_id, {"state": state})
    else:
        # No session exists yet — create a minimal tracking row so cleanup
        # can find and delete this file when the session expires.
        await asyncio.to_thread(
            create_session,
            session_id,
            {
                "id": session_id,
                "topic": f"upload-only/{file.filename}",
                "depth": "quick",
                "status": "pending",
                "state": {
                    "uploaded_pdfs": [str(file_path)],
                    "has_documents": True,
                },
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    logger.info(f"Uploaded PDF: {file.filename} -> {file_path}")
    return {
        "status": "uploaded",
        "filename": file.filename,
        "path": str(file_path),
        "session_id": session_id,
    }


def _export_attachment_filename(topic: str, session_id: str, ext: str) -> str:
    """Build a safe download filename: researchforge-<topic-slug>-<id8>.<ext>."""
    safe_topic = topic[:40]
    safe_topic = "".join(c if c.isalnum() or c in " -_" else "" for c in safe_topic)
    safe_topic = safe_topic.strip().replace(" ", "-").lower()
    return f"researchforge-{safe_topic}-{session_id[:8]}.{ext}"


def _get_completed_session_state(session: dict | None) -> dict:
    """
    Validate a session as a completed report and return its state.

    Raises 404 for unknown sessions and 400 when the report is not finished
    or carries no written sections. Shared by every export endpoint.
    """
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.get("status") != "complete":
        raise HTTPException(
            status_code=400,
            detail=f"Report is not complete yet. Current status: {session.get('status')}",
        )

    state = session.get("state", {})

    if not state.get("written_sections"):
        raise HTTPException(
            status_code=400, detail="No written sections found in report"
        )
    return state


@api_router.post(
    "/export-pdf",
    dependencies=[Depends(require_api_key), Depends(require_session_ownership)],
)
async def export_pdf_endpoint(session_id: str):
    """Export completed report as a styled PDF and return it as a file download"""
    session = await asyncio.to_thread(get_session, session_id)
    state = _get_completed_session_state(session)

    try:
        from export.pdf_exporter import export_report_to_pdf

        # Create output directory
        pdf_dir = Path("/tmp/researchforge_pdfs")
        pdf_dir.mkdir(exist_ok=True)

        # Safe filename from topic
        filename = _export_attachment_filename(
            state.get("topic", "report"), session_id, "pdf"
        )
        output_path = str(pdf_dir / filename)

        # Generate PDF in thread to avoid blocking event loop
        pdf_path = await asyncio.to_thread(export_report_to_pdf, state, output_path)

        logger.info(f"PDF exported for session {session_id}: {pdf_path}")

        return FileResponse(
            path=pdf_path,
            media_type="application/pdf",
            filename=filename,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    except ValueError as e:
        logger.error(f"PDF export validation error for session {session_id}: {str(e)}")
        raise HTTPException(
            status_code=400, detail="PDF generation failed — invalid report state"
        ) from e
    except Exception as e:
        logger.error(f"PDF export error for session {session_id}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="PDF generation failed — check server logs for details",
        ) from e


@api_router.post(
    "/export-markdown",
    dependencies=[Depends(require_api_key), Depends(require_session_ownership)],
)
async def export_markdown_endpoint(session_id: str):
    """Export completed report as Markdown and return it as a file download."""
    session = await asyncio.to_thread(get_session, session_id)
    state = _get_completed_session_state(session)

    try:
        markdown = await asyncio.to_thread(build_markdown_report, state)
    except ValueError as e:
        logger.error(
            f"Markdown export validation error for session {session_id}: {str(e)}"
        )
        raise HTTPException(
            status_code=400, detail="Markdown export failed — invalid report state"
        ) from e

    filename = _export_attachment_filename(
        state.get("topic", "report"), session_id, "md"
    )
    logger.info(f"Markdown exported for session {session_id}")

    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@api_router.post(
    "/export-html",
    dependencies=[Depends(require_api_key), Depends(require_session_ownership)],
)
async def export_html_endpoint(session_id: str):
    """Export completed report as a standalone HTML file download."""
    session = await asyncio.to_thread(get_session, session_id)
    state = _get_completed_session_state(session)

    try:
        html = await asyncio.to_thread(build_html_report, state)
    except ValueError as e:
        logger.error(f"HTML export validation error for session {session_id}: {str(e)}")
        raise HTTPException(
            status_code=400, detail="HTML export failed — invalid report state"
        ) from e

    filename = _export_attachment_filename(
        state.get("topic", "report"), session_id, "html"
    )
    logger.info(f"HTML exported for session {session_id}")

    return Response(
        content=html,
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@api_router.post(
    "/export-docx",
    dependencies=[Depends(require_api_key), Depends(require_session_ownership)],
)
async def export_docx_endpoint(session_id: str):
    """Export completed report as a DOCX file download"""
    session = await asyncio.to_thread(get_session, session_id)
    state = _get_completed_session_state(session)

    try:
        from export.docx_exporter import build_docx_report

        # Create output directory
        docx_dir = Path("/tmp/researchforge_docx")
        docx_dir.mkdir(exist_ok=True)

        # Safe filename from topic
        filename = _export_attachment_filename(
            state.get("topic", "report"), session_id, "docx"
        )
        output_path = str(docx_dir / filename)

        # Generate DOCX in thread to avoid blocking event loop
        docx_path = await asyncio.to_thread(build_docx_report, state, output_path)

        logger.info(f"DOCX exported for session {session_id}: {docx_path}")

        return FileResponse(
            path=docx_path,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=filename,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    except ValueError as e:
        logger.error(f"DOCX export validation error for session {session_id}: {str(e)}")
        raise HTTPException(
            status_code=400, detail="DOCX generation failed — invalid report state"
        ) from e
    except Exception as e:
        logger.error(f"DOCX export error for session {session_id}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="DOCX generation failed — check server logs for details",
        ) from e


@api_router.post(
    "/export-bibtex",
    dependencies=[Depends(require_api_key), Depends(require_session_ownership)],
)
async def export_bibtex_endpoint(session_id: str):
    """Export the report's citations as a BibTeX (.bib) bibliography download."""
    session = await asyncio.to_thread(get_session, session_id)
    state = _get_completed_session_state(session)

    try:
        bibtex = await asyncio.to_thread(build_bibtex_report, state)
    except ValueError as e:
        logger.error(
            f"BibTeX export validation error for session {session_id}: {str(e)}"
        )
        raise HTTPException(
            status_code=400, detail="BibTeX export failed — invalid report state"
        ) from e

    filename = _export_attachment_filename(
        state.get("topic", "report"), session_id, "bib"
    )
    logger.info(f"BibTeX exported for session {session_id}")

    return Response(
        content=bibtex,
        media_type="application/x-bibtex; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@api_router.put(
    "/session/{session_id}/outline",
    dependencies=[Depends(require_api_key), Depends(require_session_ownership)],
)
async def update_outline_endpoint(session_id: str, request: UpdateOutlineRequest):
    """
    Replace the outline of a session that is waiting for approval.

    Lets the operator edit section titles/descriptions/order before resuming
    synthesis. The session stays in waiting_approval — the existing approval
    gate remains responsible for resuming the graph with the edited outline.
    """
    session = await asyncio.to_thread(get_session, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.get("status") != "waiting_approval":
        raise HTTPException(
            status_code=400,
            detail="Outline can only be edited while the report is waiting for approval",
        )

    if not request.outline:
        raise HTTPException(status_code=400, detail="Outline must not be empty")

    updated_state = session.get("state", {})
    updated_state["outline"] = sorted(
        (s.model_dump() for s in request.outline), key=lambda s: s["order"]
    )

    timestamp = datetime.now().strftime("%H:%M:%S")
    updated_state.setdefault("stream_updates", []).append(
        f"[{timestamp}] Outline edited before approval: {len(request.outline)} sections"
    )

    await asyncio.to_thread(update_session, session_id, {"state": updated_state})

    return {"status": "updated", "outline": updated_state["outline"]}


# --- Chat with report (W3) ---

# Retry guidance returned when a chat turn exceeds the per-turn token budget.
CHAT_BUDGET_RETRY_AFTER_SECONDS = 60

# Chat turns are one section-sized LLM call each; the cap is per client IP.
CHAT_RATE_LIMIT = "20/minute"

# Per-session chat turns are serialized in-process: the whole `state` JSONB
# is a read-modify-write blob, so concurrent turns on one session must not
# interleave read and write. Locks are created on demand and dropped when
# their session is TTL-cleaned.
_chat_locks: dict[str, asyncio.Lock] = {}
_chat_locks_guard = asyncio.Lock()


async def _get_chat_lock(session_id: str) -> asyncio.Lock:
    """Return the per-session chat lock, creating it on first use."""
    async with _chat_locks_guard:
        lock = _chat_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            _chat_locks[session_id] = lock
        return lock


@api_router.post(
    "/session/{session_id}/chat",
    dependencies=[Depends(require_api_key), Depends(require_session_ownership)],
)
@limiter.limit(CHAT_RATE_LIMIT)
async def chat_with_report(
    request: Request, session_id: str, chat_request: ChatRequest
):
    """
    Ask a question about a completed report.

    Grounds strictly on the report's own sections, numbered sources, and
    ingested document chunks — no tools, no fresh retrieval. Fails closed:
    API key + session ownership, complete-gate, per-session write lock,
    per-turn token budget, sanitized errors.
    """
    message = chat_request.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Chat message must not be empty")

    async with await _get_chat_lock(session_id):
        session = await asyncio.to_thread(get_session, session_id)
        # 404 unknown / 400 not-complete — the shared export gate.
        state = _get_completed_session_state(session)

        try:
            token_budget.ensure_chat_budget(session_id)
            context_token = token_budget.set_session_context(session_id)
            try:
                result = await asyncio.to_thread(run_chat_turn, state, message)
            finally:
                token_budget.reset_session_context(context_token)
        except token_budget.TokenBudgetExceeded:
            logger.warning(f"Session {session_id}: chat turn exceeded token budget")
            raise HTTPException(
                status_code=429,
                detail={
                    "message": (
                        "This chat turn exceeded the per-turn token budget "
                        "(CHAT_TOKEN_BUDGET). Shorten the question or raise the limit."
                    ),
                    "retry_after_seconds": CHAT_BUDGET_RETRY_AFTER_SECONDS,
                },
                headers={"Retry-After": str(CHAT_BUDGET_RETRY_AFTER_SECONDS)},
            ) from None
        except HTTPException:
            raise
        except Exception as e:
            # Client-visible error is generic plus a short reference; raw
            # exception text stays in the server log only.
            error_ref = uuid.uuid4().hex[:8]
            logger.error(
                f"Chat turn error for session {session_id} [ref {error_ref}]: {e}"
            )
            raise HTTPException(
                status_code=500,
                detail=f"Chat failed due to an internal error. Reference: {error_ref}.",
            ) from e
        finally:
            # The budget is per turn — release it however the turn ends, so an
            # exhausted turn cannot poison the next one on the same session.
            token_budget.release_chat_budget(session_id)

        # Persist the turn on the session's transcript, capped so stored
        # state cannot grow unboundedly with the conversation.
        timestamp = datetime.now(timezone.utc).isoformat()
        transcript = list(state.get("chat_messages") or [])
        transcript.append({"role": "user", "content": message, "ts": timestamp})
        transcript.append(
            {"role": "assistant", "content": result["answer"], "ts": timestamp}
        )
        state["chat_messages"] = transcript[-MAX_TRANSCRIPT_MESSAGES:]
        await asyncio.to_thread(update_session, session_id, {"state": state})

    return {
        "answer": result["answer"],
        "resolved_citations": result["resolved_citations"],
        "unresolved_citations": result["unresolved_citations"],
        "usage": result["usage"],
    }


# --- Include router and middleware ---
app.include_router(api_router)

cors_origins_env = os.environ.get("CORS_ORIGINS", "").strip()

# CORS_ORIGINS must be set explicitly. validate_env_vars() at startup
# enforces this. The fallback here is a safety net only — if somehow
# reached without the env var, default to a closed posture.
if not cors_origins_env:
    cors_origins = []
    logger.error(
        "CORS_ORIGINS is not set — all cross-origin requests will be blocked. "
        "Set CORS_ORIGINS=* for development or a comma-separated list of "
        "allowed origins for production."
    )
elif cors_origins_env == "*":
    cors_origins = ["*"]
    logger.warning(
        "CORS_ORIGINS=* — all origins allowed. Acceptable for development only."
    )
else:
    cors_origins = [o.strip() for o in cors_origins_env.split(",") if o.strip()]
    logger.info(f"CORS restricted to {len(cors_origins)} origin(s): {cors_origins}")

app.add_middleware(
    CORSMiddleware,
    allow_credentials=cors_origins != ["*"] and bool(cors_origins),
    allow_origins=cors_origins if cors_origins else [],
    allow_methods=["*"],
    allow_headers=["*"],
)

SESSION_TTL_SECONDS = 7200

# Maximum wall-clock time for a full graph execution.
# Covers deep 6-section reports with retries. If exceeded, the session
# is marked error so the user gets a clear failure instead of waiting forever.
GRAPH_EXECUTION_TIMEOUT = 600.0  # 10 minutes

# Maximum time per synthesis iteration in the resume loop.
# Each iteration writes one section. 5 minutes is generous for a single
# GPT-4o call but accounts for rate limit retries.
GRAPH_RESUME_ITERATION_TIMEOUT = 300.0  # 5 minutes per section


# --- Cost controls ---


def _parse_positive_int_env(name: str, default: int) -> int:
    """Parse a positive-integer env var; fall back to default when unset/invalid."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning(f"Invalid {name}={raw!r} — using default {default}")
        return default
    if value < 1:
        logger.warning(
            f"Invalid {name}={raw!r} (must be >= 1) — using default {default}"
        )
        return default
    return value


MAX_CONCURRENT_RUNS = _parse_positive_int_env("MAX_CONCURRENT_RUNS", 3)

# Global cap on concurrent graph executions — initial runs AND resume
# executions after outline approval both hold a slot for their duration.
# At capacity, new requests get 429 with retry guidance instead of queueing.
_graph_run_slots = asyncio.Semaphore(MAX_CONCURRENT_RUNS)
_RUN_SLOT_RETRY_AFTER_SECONDS = 30


async def acquire_run_slot() -> None:
    """Take a graph-execution slot, or reject the request with 429 + retry guidance."""
    if _graph_run_slots.locked():
        raise HTTPException(
            status_code=429,
            detail={
                "message": (
                    "The server is at its concurrent report-generation limit. "
                    "Please retry shortly."
                ),
                "retry_after_seconds": _RUN_SLOT_RETRY_AFTER_SECONDS,
            },
            headers={"Retry-After": str(_RUN_SLOT_RETRY_AFTER_SECONDS)},
        )
    await _graph_run_slots.acquire()


def release_run_slot() -> None:
    """Release a graph-execution slot. Only call after a successful acquire_run_slot."""
    _graph_run_slots.release()


def _cleanup_sessions_and_files() -> None:
    """
    Delete expired sessions from the database and remove their files from disk.
    Runs in a thread pool worker via asyncio.to_thread.
    """
    deleted = db_cleanup_sessions(SESSION_TTL_SECONDS)
    if not deleted:
        return

    for session_info in deleted:
        # Delete uploaded PDFs associated with this session
        for pdf_path in session_info.get("uploaded_pdfs", []):
            try:
                Path(pdf_path).unlink(missing_ok=True)
                logger.info(f"Deleted uploaded PDF: {pdf_path}")
            except Exception as e:
                logger.warning(f"Could not delete PDF {pdf_path}: {e}")

        # Delete generated report PDF if it exists
        session_id = session_info["id"]
        _chat_locks.pop(session_id, None)
        _steer_locks.pop(session_id, None)
        # Abandoned sessions never reached a terminal status in-process —
        # release every budget so registries cannot leak (W4 per-run
        # semantics: TTL cleanup is the sanctioned non-terminal release).
        token_budget.release_budget(session_id)
        token_budget.release_round_budget(session_id)
        token_budget.release_chat_budget(session_id)
        pdf_dir = Path("/tmp/researchforge_pdfs")
        for pdf_file in pdf_dir.glob(f"researchforge-*-{session_id[:8]}.pdf"):
            try:
                pdf_file.unlink(missing_ok=True)
                logger.info(f"Deleted report PDF: {pdf_file}")
            except Exception as e:
                logger.warning(f"Could not delete report PDF {pdf_file}: {e}")

    logger.info(f"Session cleanup complete: removed {len(deleted)} expired sessions")
