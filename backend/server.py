"""
ResearchForge - Multi-Agent Research Report System
FastAPI Backend with SSE Streaming
"""
from fastapi import FastAPI, APIRouter, HTTPException, UploadFile, File, Form, BackgroundTasks, Request
from fastapi.responses import StreamingResponse, FileResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import os
import logging
import json
import asyncio
import uuid
import shutil
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict

# Clear any system proxy environment variables that block outbound API calls
# Tavily, OpenAI, and LangSmith all require direct outbound connections
for _proxy_var in [
    'HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy',
    # tavily-python can honor these explicitly if present
    'TAVILY_HTTP_PROXY', 'TAVILY_HTTPS_PROXY', 'tavily_http_proxy', 'tavily_https_proxy',
]:
    os.environ.pop(_proxy_var, None)

# Extra safety: ensure common API domains bypass any remaining proxy config.
_no_proxy_domains = 'api.tavily.com,api.openai.com,api.smith.langchain.com,smith.langchain.com'
for _no_proxy_var in ['NO_PROXY', 'no_proxy']:
    _existing = os.environ.get(_no_proxy_var, '').strip()
    if _existing:
        if _no_proxy_domains not in _existing:
            os.environ[_no_proxy_var] = f'{_existing},{_no_proxy_domains}'
    else:
        os.environ[_no_proxy_var] = _no_proxy_domains

# Load environment
ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# Import graph components
from graph.state import create_initial_state
from graph.graph import get_graph, get_checkpointer
from eval.langsmith_tracer import get_langsmith_config, is_tracing_enabled, get_trace_url, setup_tracing
from utils.clients import validate_env_vars
from utils.validation import validate_url
from db import setup_db, create_session, get_session, update_session, cleanup_old_sessions as db_cleanup_sessions

limiter = Limiter(key_func=get_remote_address)

# Create the main app
app = FastAPI(
    title="ResearchForge API",
    description="Multi-Agent Research Report Generation System",
    version="1.0.0"
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Create router with /api prefix
api_router = APIRouter(prefix="/api")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
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
    input_urls: List[str] = Field(default_factory=list)
    uploaded_pdfs: List[str] = Field(default_factory=list)


class OutlineSection(BaseModel):
    section_id: str
    title: str
    description: str
    order: int


class ApproveOutlineRequest(BaseModel):
    session_id: str
    outline: List[OutlineSection]
    edits: Optional[str] = None


class ReportSession(BaseModel):
    model_config = ConfigDict(extra="ignore")
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    topic: str
    depth: str
    status: str = "pending"  # pending, running, waiting_approval, complete, error
    state: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# --- API Endpoints ---

@api_router.get("/")
async def root():
    return {"message": "ResearchForge API", "version": "1.0.0"}


@api_router.get("/health")
async def health_check():
    return {
        "status": "ok",
        "agents": ["research", "document", "factcheck", "outline", "synthesis", "citations"],
        "model": "gpt-4o"
    }


@api_router.post("/run")
@limiter.limit("10/minute")
async def run_report(request: Request, run_request: RunReportRequest, background_tasks: BackgroundTasks):
    """Start a new research report generation"""

    # Input sanitization
    topic = run_request.topic.strip()
    if len(topic) < 5:
        raise HTTPException(status_code=400, detail="Topic must be at least 5 characters long")
    if len(topic) > 500:
        raise HTTPException(status_code=400, detail="Topic must be under 500 characters")

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

    # Build LangSmith run name from topic (truncated, URL-safe)
    safe_topic = topic[:40].replace(" ", "-").lower()
    run_name = f"research-report-{safe_topic}-{session_id[:8]}"

    valid_pdfs = []
    for raw_path in run_request.uploaded_pdfs:
        try:
            pdf_path = Path(raw_path).resolve()
            if (
                pdf_path.parent == UPLOAD_DIR.resolve()
                and pdf_path.suffix.lower() == '.pdf'
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
        input_urls=valid_urls
    )

    # Store session
    await asyncio.to_thread(create_session, session_id, {
        "id": session_id,
        "topic": topic,
        "depth": run_request.depth,
        "status": "running",
        "run_name": run_name,
        "trace_url": get_trace_url() if is_tracing_enabled() else None,
        "state": initial_state,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    
    # Run graph in background
    background_tasks.add_task(run_graph_async, session_id)
    
    return {
        "session_id": session_id,
        "status": "running",
        "message": f"Started research report generation for: {run_request.topic}"
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
                "project": "Multi-Agent-Research"
            }
        }

        logger.info(f"Starting graph execution for session {session_id}, topic: {state.get('topic', 'unknown')}")

        # Run graph in thread to avoid blocking the async event loop
        # The graph will pause at interrupt_before=['synthesis'] automatically
        result = await asyncio.to_thread(graph.invoke, state, config)

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
            timestamp = datetime.now().strftime("%H:%M:%S")
            session["state"]["stream_updates"].append(
                f"[{timestamp}] ⏸ Graph paused - outline ready for review (interrupt checkpoint saved)"
            )
            await asyncio.to_thread(update_session, session_id, {
                "status": "waiting_approval",
                "state": session["state"],
            })
            logger.info(f"Session {session_id} paused at interrupt checkpoint - waiting for outline approval")
        elif is_complete or next_agent == "END":
            session["status"] = "complete"
            await asyncio.to_thread(update_session, session_id, {"status": "complete"})
            logger.info(f"Session {session_id} completed successfully")
        elif has_error:
            session["status"] = "error"
            session["state"]["error"] = has_error
            await asyncio.to_thread(update_session, session_id, {
                "status": "error",
                "state": session["state"],
            })
            logger.error(f"Session {session_id} error: {has_error}")
        else:
            session["status"] = "complete"
            await asyncio.to_thread(update_session, session_id, {"status": "complete"})

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Graph execution error for session {session_id}: {error_msg}")
        import traceback
        logger.error(traceback.format_exc())

        existing = await asyncio.to_thread(get_session, session_id)
        if existing:
            existing["state"]["error"] = error_msg
            await asyncio.to_thread(update_session, session_id, {
                "status": "error",
                "state": existing["state"],
            })


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
                "project": "Multi-Agent-Research"
            }
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
            "sections_needing_rewrite": updated_state.get("sections_needing_rewrite", []),
        }
        await asyncio.to_thread(graph.update_state, config, state_updates)
        logger.info(f"Checkpoint state updated for session {session_id} - outline_approved=True")

        # Step 2: Resume from interrupt by passing None as input
        # interrupt_before=["synthesis"] fires for EVERY synthesis call (one per section).
        # We loop invoke(None, config) until the graph reaches END or an error occurs.
        # Each iteration: synthesis writes one section → supervisor routes to next → interrupt fires.
        # Derive limit from outline length: one iteration per section plus
        # a fixed buffer for the citations pass and any edge cases.
        # Minimum of 30 so short outlines still have headroom.
        approved_outline_len = len(updated_state.get("approved_outline", []))
        max_iterations = max(30, approved_outline_len * 3)
        for iteration in range(max_iterations):
            result = await asyncio.to_thread(graph.invoke, None, config)

            # Update session state after each section so streaming UI sees progress
            session["state"] = result
            await asyncio.to_thread(update_session, session_id, {
                "state": result,
                "status": session["status"],
            })

            is_complete = result.get("is_complete", False)
            has_error = result.get("error")
            next_agent = result.get("next_agent", "END")

            logger.info(
                f"Resume iteration {iteration + 1} for session {session_id}: "
                f"next_agent={next_agent}, is_complete={is_complete}, "
                f"sections_written={len(result.get('written_sections', []))}"
            )

            if is_complete or next_agent == "END":
                session["status"] = "complete"
                await asyncio.to_thread(update_session, session_id, {
                    "status": session["status"],
                    "state": session["state"],
                })
                logger.info(f"Session {session_id} completed after outline approval")
                break
            elif has_error:
                session["status"] = "error"
                await asyncio.to_thread(update_session, session_id, {
                    "status": session["status"],
                    "state": session["state"],
                })
                logger.error(f"Session {session_id} error after resume: {has_error}")
                break
            # Otherwise interrupt fired again (next synthesis call) - keep resuming
        else:
            # max_iterations reached without completion
            logger.warning(f"Session {session_id} hit max resume iterations ({max_iterations})")
            session["status"] = "error"
            session["state"]["error"] = "Graph did not complete within expected iterations"
            await asyncio.to_thread(update_session, session_id, {
                "status": session["status"],
                "state": session["state"],
            })

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Graph resume error for session {session_id}: {error_msg}")
        import traceback
        logger.error(traceback.format_exc())

        existing = await asyncio.to_thread(get_session, session_id)
        if existing:
            existing["state"]["error"] = error_msg
            await asyncio.to_thread(update_session, session_id, {
                "status": "error",
                "state": existing["state"],
            })


@api_router.get("/session/{session_id}/status")
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
        "error": state.get("error"),
        "is_complete": state.get("is_complete", False),
        "has_outline": len(state.get("outline", [])) > 0,
        "outline": state.get("outline", []) if session.get("status") == "waiting_approval" else [],
        "trace_url": session.get("trace_url"),
        "tracing_enabled": is_tracing_enabled(),
        "updated_at": session.get("updated_at", session.get("created_at", "")),
        "versioning_report": session.get("versioning_report", None),
        "changed_section_ids": state.get("changed_section_ids", []),
        "unchanged_section_ids": [
            s.get("section_id") for s in state.get("approved_outline", [])
            if s.get("section_id") not in state.get("changed_section_ids", [])
        ],
    }


@api_router.get("/session/{session_id}")
async def get_session_endpoint(session_id: str):
    """Get current session state"""
    session = await asyncio.to_thread(get_session, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return session


@api_router.get("/session/{session_id}/stream")
async def stream_session(session_id: str):
    """Stream session updates via SSE"""
    session = await asyncio.to_thread(get_session, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    async def generate():
        last_update_count = 0
        while True:
            session = await asyncio.to_thread(get_session, session_id)
            if not session:
                break
            
            state = session.get("state", {})
            updates = state.get("stream_updates", [])
            
            # Send new updates
            if len(updates) > last_update_count:
                for update in updates[last_update_count:]:
                    data = json.dumps({
                        "type": "update",
                        "message": update,
                        "status": session.get("status"),
                        "current_agent": state.get("current_agent", ""),
                        "completed_agents": state.get("completed_agents", [])
                    })
                    yield f"data: {data}\n\n"
                last_update_count = len(updates)
            
            # Send state snapshot every few updates
            if session.get("status") in ["waiting_approval", "complete", "error"]:
                data = json.dumps({
                    "type": "state",
                    "session": session
                })
                yield f"data: {data}\n\n"
                break
            
            await asyncio.sleep(0.5)
    
    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


@api_router.post("/approve-outline")
async def approve_outline(request: ApproveOutlineRequest, background_tasks: BackgroundTasks):
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
            detail=f"Session is not waiting for approval. Current status: {session.get('status')}"
        )

    state = session["state"]
    timestamp = datetime.now().strftime("%H:%M:%S")

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
        written_sections=state.get("written_sections", [])
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

    logger.info(f"Versioning diff for session {request.session_id}: {versioning_report['summary']}")

    await asyncio.to_thread(update_session, request.session_id, {
        "status": "running",
        "state": state,
        "versioning_report": versioning_report,
    })

    # Resume graph from interrupt checkpoint using the SAME thread_id
    # This is the key difference from the old approach - we pass the UPDATED state
    # and the same config so MemorySaver knows which checkpoint to resume
    background_tasks.add_task(resume_graph_after_approval, request.session_id, state)

    return {
        "status": "approved",
        "message": "Outline approved - resuming report generation from checkpoint",
        "session_id": request.session_id
    }


@api_router.post("/upload-pdf")
@limiter.limit("30/minute")
async def upload_pdf(
    request: Request,
    session_id: Optional[str] = Form(None),
    file: UploadFile = File(...)
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
            detail=f"File exceeds maximum upload size of {MAX_UPLOAD_BYTES // (1024*1024)} MB"
        )

    # --- Magic byte check ---
    # PDF files must begin with the %PDF- signature (hex 25 50 44 46 2D).
    # Extension-only checks are trivially bypassed by renaming any file.
    PDF_MAGIC = b"%PDF-"
    if not content[:5] == PDF_MAGIC:
        raise HTTPException(
            status_code=400,
            detail="File does not appear to be a valid PDF (invalid file signature)"
        )

    # --- Filename sanitization ---
    # Strip to the basename only (no directory components), remove null bytes,
    # remove all characters except alphanumeric, dots, hyphens, underscores,
    # and truncate to 100 characters to prevent filesystem issues.
    original_name = Path(file.filename).name  # basename only, drops any path
    original_name = original_name.replace("\x00", "")  # strip null bytes
    safe_name = "".join(
        c for c in original_name if c.isalnum() or c in (".", "-", "_")
    )
    if not safe_name or safe_name.startswith("."):
        safe_name = "upload.pdf"
    safe_name = safe_name[:100]
    safe_filename = f"{session_id}_{safe_name}"
    file_path = UPLOAD_DIR / safe_filename

    # --- Write to disk ---
    try:
        with open(file_path, "wb") as buffer:
            buffer.write(content)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail="Failed to save uploaded file"
        )

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
        await asyncio.to_thread(create_session, session_id, {
            "id": session_id,
            "topic": f"upload-only/{file.filename}",
            "depth": "quick",
            "status": "pending",
            "state": {
                "uploaded_pdfs": [str(file_path)],
                "has_documents": True,
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

    logger.info(f"Uploaded PDF: {file.filename} -> {file_path}")
    return {
        "status": "uploaded",
        "filename": file.filename,
        "path": str(file_path),
        "session_id": session_id
    }


@api_router.post("/export-pdf")
async def export_pdf_endpoint(session_id: str):
    """Export completed report as a styled PDF and return it as a file download"""
    session = await asyncio.to_thread(get_session, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.get("status") != "complete":
        raise HTTPException(
            status_code=400,
            detail=f"Report is not complete yet. Current status: {session.get('status')}"
        )

    state = session.get("state", {})

    if not state.get("written_sections"):
        raise HTTPException(status_code=400, detail="No written sections found in report")

    try:
        from export.pdf_exporter import export_report_to_pdf

        # Create output directory
        pdf_dir = Path("/tmp/researchforge_pdfs")
        pdf_dir.mkdir(exist_ok=True)

        # Safe filename from topic
        safe_topic = state.get("topic", "report")[:40]
        safe_topic = "".join(c if c.isalnum() or c in " -_" else "" for c in safe_topic)
        safe_topic = safe_topic.strip().replace(" ", "-").lower()
        filename = f"researchforge-{safe_topic}-{session_id[:8]}.pdf"
        output_path = str(pdf_dir / filename)

        # Generate PDF in thread to avoid blocking event loop
        pdf_path = await asyncio.to_thread(export_report_to_pdf, state, output_path)

        logger.info(f"PDF exported for session {session_id}: {pdf_path}")

        return FileResponse(
            path=pdf_path,
            media_type="application/pdf",
            filename=filename,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"PDF export error for session {session_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {str(e)}")


# --- Include router and middleware ---
app.include_router(api_router)

cors_origins_env = os.environ.get('CORS_ORIGINS', '').strip()

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
        "CORS_ORIGINS=* — all origins allowed. "
        "Acceptable for development only."
    )
else:
    cors_origins = [o.strip() for o in cors_origins_env.split(',') if o.strip()]
    logger.info(f"CORS restricted to {len(cors_origins)} origin(s): {cors_origins}")

app.add_middleware(
    CORSMiddleware,
    allow_credentials=cors_origins != ["*"] and bool(cors_origins),
    allow_origins=cors_origins if cors_origins else [],
    allow_methods=["*"],
    allow_headers=["*"],
)

SESSION_TTL_SECONDS = 7200


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
        pdf_dir = Path("/tmp/researchforge_pdfs")
        for pdf_file in pdf_dir.glob(f"researchforge-*-{session_id[:8]}.pdf"):
            try:
                pdf_file.unlink(missing_ok=True)
                logger.info(f"Deleted report PDF: {pdf_file}")
            except Exception as e:
                logger.warning(f"Could not delete report PDF {pdf_file}: {e}")

    logger.info(f"Session cleanup complete: removed {len(deleted)} expired sessions")


@app.on_event("startup")
async def start_cleanup_task():
    """Schedule periodic session cleanup and verify tracing"""
    validate_env_vars()
    await asyncio.to_thread(setup_db)
    await asyncio.to_thread(get_checkpointer)
    setup_tracing()

    async def run_cleanup_loop():
        while True:
            await asyncio.sleep(1800)
            await asyncio.to_thread(_cleanup_sessions_and_files)

    cleanup_task = asyncio.create_task(run_cleanup_loop())
    # Store reference to prevent garbage collection
    app.state.cleanup_task = cleanup_task
    logger.info("ResearchForge API started \u2014 session cleanup scheduled every 30 minutes")

