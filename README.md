# ResearchForge

![Python](https://img.shields.io/badge/Python-3.11+-blue?style=flat-square&logo=python)
![LangGraph](https://img.shields.io/badge/LangGraph-0.2+-green?style=flat-square)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-teal?style=flat-square&logo=fastapi)
![React](https://img.shields.io/badge/React-18+-61DAFB?style=flat-square&logo=react)
![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)

**Hierarchical multi-agent research and report generation system built with LangGraph.**

Give it a topic. Get a structured, cited, confidence-scored research report - with human-in-the-loop outline approval before writing begins.

ResearchForge orchestrates 6 specialized AI agents through a LangGraph Supervisor pattern. Each agent handles one responsibility: web research, document ingestion, fact verification, outline generation, section synthesis, and citation formatting. The Supervisor routes control flow deterministically using 8 rules against current state - no LLM routing, no hallucinated decisions.

---

## What It Does

- 🔍 **Live Web Research** - Searches the web in real time using Tavily across 3 targeted queries per topic
- ✓ **LLM-as-Judge Fact Checking** - Verifies every claim against retrieved sources before writing begins. Returns SUPPORTED / PARTIALLY_SUPPORTED / UNSUPPORTED with confidence scores
- 📋 **Human-in-the-Loop Outline Approval** - Graph pauses using `interrupt()` after outline generation. You review and edit the outline before synthesis begins
- ♻️ **Report Versioning** - Diffs your edits against the original outline. Only changed sections are re-written - unchanged sections reuse existing content (~80% token savings)
- ✍️ **Section-by-Section Synthesis** - Each section is written individually, grounded in fact-checked sources with inline citations
- 📊 **Confidence Scoring** - Every section receives a confidence score (HIGH/MEDIUM/LOW) based on fact-check verdict weighted means
- 📄 **PDF Export** - Styled multi-page PDF with cover page, table of contents, confidence bars per section, and numbered citations
- 📡 **Live SSE Streaming** - Frontend receives real-time agent updates via Server-Sent Events
- 🔭 **LangSmith Tracing** - Full trace tree for every run - routing decisions, LLM calls, tool invocations, latency per node

---

## Demo

**Full pipeline flow:**

```
Topic entered → Research Agent fires (Tavily × 3)
             → FactCheck Agent (parallel via Send() API)
             → Outline Agent generates structure
             → ⏸ INTERRUPT - user reviews and edits outline
             → Versioning diff runs on approval
             → Synthesis Agent writes sections (skips unchanged)
             → Citation Agent formats references
             → PDF ready for download
```

---

## Architecture

```
                    ┌──────────────────────────────────────────┐
                    │              SUPERVISOR NODE              │
                    │  Reads ReportState. Routes to next agent  │
                    │  using 8 deterministic rules.             │
                    │  No LLM calls - pure state inspection.    │
                    └───────────────┬──────────────────────────┘
                                    │
         ┌──────────────────────────┼────────────────────────────┐
         │                          │                            │
         ▼                          ▼                            ▼
┌─────────────────┐      ┌──────────────────┐      ┌────────────────────────┐
│  WebResearch    │      │  DocumentAgent   │      │     FactCheck Agent    │
│  Agent          │      │                  │      │                        │
│  • Tavily ×3    │      │  • pdfplumber    │      │  • Extracts 5-8 claims │
│  • URL dedup    │      │  • httpx + BS4   │      │  • LLM-as-judge        │
│  • 15 sources   │      │  • ~500w chunks  │      │  • Send() API parallel │
│    max          │      │  • GPT-4o summary│      │  • SUPPORTED /         │
└────────┬────────┘      └────────┬─────────┘      │    PARTIAL /           │
         │                        │                 │    UNSUPPORTED         │
         └────────────────────────┴─────────────────┘
                                    │
                                    ▼
                    ┌──────────────────────────────────────────┐
                    │              OUTLINE AGENT                │
                    │  • Structured outline from research data  │
                    │  • quick=3 sections, deep=6 sections      │
                    │  • Snapshots to original_outline          │
                    └───────────────┬──────────────────────────┘
                                    │
                                    ▼
                    ┌──────────────────────────────────────────┐
                    │         interrupt() CHECKPOINT            │◄── MemorySaver
                    │                                          │
                    │  Graph pauses before synthesis.          │
                    │  State serialized to checkpointer.       │
                    │  User reviews + edits outline in UI.     │
                    │  Versioning diff runs on approval.       │
                    │  Graph resumes with same thread_id.      │
                    └───────────────┬──────────────────────────┘
                                    │
                                    ▼
                    ┌──────────────────────────────────────────┐
                    │           SYNTHESIS AGENT                 │
                    │  • One section per invocation             │
                    │  • Skips unchanged sections (versioning)  │
                    │  • Inline [source: url] citations         │
                    │  • 180-250 words per section              │
                    │  • Confidence scored per section          │
                    └───────────────┬──────────────────────────┘
                                    │
                                    ▼
                    ┌──────────────────────────────────────────┐
                    │           CITATION AGENT                  │
                    │  • Deduplicates all source URLs           │
                    │  • Preserves first-appearance order       │
                    │  • Assigns [1], [2], [n] numbers          │
                    │  • Replaces inline markers in content     │
                    │  • Computes final overall_confidence      │
                    └──────────────────────────────────────────┘
```

---

## What Makes This Senior-Level

| Pattern | Implementation |
|---|---|
| **LangGraph Supervisor** | Deterministic routing across 6 agents using 8 explicit state-inspection rules - not LLM routing |
| **`interrupt()` + MemorySaver** | True graph pause/resume - state checkpointed to MemorySaver, graph resumes from exact node via same `thread_id` |
| **Send() API parallelism** | FactCheck fans out N claim-judging invocations simultaneously via `Send()`, merges results - reduces latency from N×2s serial to ~2s regardless of claim count |
| **LLM-as-judge fact checking** | GPT-4o assesses each claim against retrieved sources - returns structured verdict + confidence + reasoning + supporting URLs |
| **Section-level confidence scoring** | Each section scored by weighted mean of `verdict_score × confidence` for fact-checks with keyword overlap. `SUPPORTED=1.0`, `PARTIALLY=0.6`, `UNSUPPORTED=0.2` |
| **Report versioning** | Diffs original vs edited outline on approval - skips LLM synthesis calls for unchanged sections. 80% token savings when 1 of 5 sections edited |
| **Pydantic state schema** | Full `ReportState` TypedDict with 29 fields across 6 Pydantic sub-models - makes LangSmith traces readable and graph behavior testable |
| **SSE streaming** | FastAPI `StreamingResponse` pushes agent updates to React frontend in real time - `EventSource` with polling fallback |
| **LangSmith tracing** | `@traceable` on all 15 LLM functions - supervisor routing, parallel factcheck spans, per-section synthesis, full latency tree per run |
| **PDF export** | reportlab-generated multi-page PDF with cover page, TOC, confidence bars, citations page - downloadable from UI |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Agent Framework | LangGraph 0.2+ - StateGraph, MemorySaver, Send() API, interrupt() |
| LLM | GPT-4o via OpenAI API |
| Research | Tavily API - live web search |
| Document Ingestion | pdfplumber (PDF parsing), httpx + BeautifulSoup (URL fetching) |
| Backend API | FastAPI with async background tasks, SSE streaming |
| Frontend | React 18 with Zustand state management |
| Observability | LangSmith - full trace tree, @traceable decorators |
| PDF Generation | reportlab - styled multi-page documents |
| State Schema | Pydantic v2 + TypedDict (29 fields) |
| Environment | Python 3.11+, Node.js 18+ |

---

## Prerequisites

Before you begin, make sure you have:

- **Python 3.11+** - check with `python3 --version` 
- **Node.js 18+** - check with `node --version` 
- **OpenAI API key** - get one at [platform.openai.com](https://platform.openai.com)
- **Tavily API key** - free tier at [app.tavily.com](https://app.tavily.com)
- **LangSmith API key** - free tier at [smith.langchain.com](https://smith.langchain.com) (optional but recommended)

---

## Installation & Setup

### Step 1 - Clone the repository

```bash
git clone https://github.com/nihanthnaidu007/Research_Forge.git
cd Research_Forge
```

### Step 2 - Set up the backend

```bash
cd backend
pip install -r requirements.txt
```

### Step 3 - Create your environment file

Create `backend/.env` with your API keys:

```bash
cp .env.example backend/.env
```

Then open `backend/.env` and fill in your keys:

```
# Required
OPENAI_API_KEY=your_openai_api_key_here
TAVILY_API_KEY=your_tavily_api_key_here

# Server
CORS_ORIGINS=http://localhost:3000

# LangSmith Tracing (optional but recommended)
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=your_langsmith_api_key_here
LANGCHAIN_PROJECT=Multi-Agent-Research
```

### Step 4 - Start the backend

```bash
cd backend
uvicorn server:app --port 8000 --reload
```

You should see:
```
LangSmith tracing ENABLED - project: Multi-Agent-Research
ResearchForge API started - session cleanup scheduled every 30 minutes
INFO:     Uvicorn running on http://0.0.0.0:8000
```

### Step 5 - Set up and start the frontend

Open a second terminal:

```bash
cd frontend
npm install
npm start
```

Open [http://localhost:3000](http://localhost:3000) in your browser.

---

## How to Use

### Running a report

1. Enter a research topic in the left sidebar - e.g. `"AI agents in healthcare diagnostics"` 
2. Select depth: **Quick** (3 sections) or **Deep** (6 sections)
3. Optionally upload PDFs or paste URLs for document ingestion
4. Click **Generate Report**

### What you will see

**Stage 1 - Research phase**

The agent pipeline sidebar shows agents firing in real time:
- 🔍 Web Research → green checkmark when done
- ✓ Fact Check → runs in parallel, shows claim count
- 📋 Outline → generates section structure

**Stage 2 - Outline approval**

The graph pauses. The center panel shows your outline with editable section titles and descriptions.

- Edit any section you want changed - the badge updates to `✏️ edited - will re-synthesize` 
- Leave sections you are happy with - they show `✓ unchanged - will reuse` 
- A summary banner shows how many sections will be re-written vs reused
- Click **Approve & Start Writing**

**Stage 3 - Synthesis**

The graph resumes from the checkpoint. Sections are written one at a time. The trace log shows which sections are being written vs reused.

**Stage 4 - Complete report**

The center panel shows the full report:
- Each section has a confidence badge (HIGH/MEDIUM/LOW) with a percentage
- Inline citations are formatted as `[1]`, `[2]`, etc.
- References section lists all sources with clickable URLs
- ♻️ reused badge appears on sections that were not re-synthesized

**Download PDF**

Click **Download PDF** to generate and download a styled multi-page PDF report.

---

## Report Depth Options

| Option | Sections | Use case |
|---|---|---|
| **Quick** | 3 sections | Fast overview, ~2-3 minutes |
| **Deep** | 6 sections | Comprehensive analysis, ~5-7 minutes |

---

## Document Ingestion

ResearchForge can incorporate your own documents into the research context:

**Upload PDFs:**
- Click the PDF upload area in the left sidebar
- Upload one or more PDF files
- The DocumentAgent extracts text page by page using pdfplumber
- Pages with fewer than 50 characters (blank/image-only pages) are skipped

**Paste URLs:**
- Paste one URL per line in the URL input field
- The DocumentAgent fetches each URL, extracts text from paragraph elements, and splits into ~500 word chunks
- Failed URLs are skipped with a warning in the trace log

All document content is summarized by GPT-4o and injected into the synthesis context alongside web research results.

---

## Report Versioning

When you edit the outline during approval, ResearchForge only re-writes sections that actually changed:

```
Original outline:
  sec_1: "Background and Context"        → you leave this unchanged
  sec_2: "Current Applications"          → you change the title
  sec_3: "Future Implications"           → you leave this unchanged

After approval:
  Versioning diff result:
    changed:   ['sec_2']
    unchanged: ['sec_1', 'sec_3']

  Synthesis behavior:
    sec_1 → ♻️ reused (0 LLM calls)
    sec_2 → ✍️ re-written (1 LLM call)
    sec_3 → ♻️ reused (0 LLM calls)

  Token savings: ~80% (1 call instead of 3)
```

The versioning diff is case-insensitive and strips whitespace. A section is considered changed only if its title or description actually differs.

---

## Confidence Scoring

Every section receives a confidence score based on the FactCheck phase:

```
For each fact-checked claim:
  verdict_score:
    SUPPORTED           → 1.0
    PARTIALLY_SUPPORTED → 0.6
    UNSUPPORTED         → 0.2

  weighted_score = verdict_score × claim_confidence

For each section:
  relevant_claims = claims with ≥2 keyword overlap with section title + description
  section_confidence = mean(weighted_scores for relevant_claims)
  fallback = 0.5 (neutral) if no relevant claims found

Overall confidence = mean(all section_confidence scores)
```

Confidence badge thresholds:
- **HIGH** (green) - ≥ 85%
- **MEDIUM** (amber) - ≥ 65%
- **LOW** (red) - < 65%

---

## LangSmith Tracing

Every report run produces a full trace visible at [smith.langchain.com/projects/Multi-Agent-Research](https://smith.langchain.com/projects/Multi-Agent-Research).

The trace tree shows:
- `supervisor` - each routing decision with reasoning
- `research-agent` → `tavily-web-search` (×3 tool calls)
- `factcheck-fanout` → `judge-claim-parallel` (×N simultaneous spans)
- `factcheck-merge` - collects parallel results
- `outline-agent` → `generate-outline` LLM call
- `synthesis-agent` → `write-section` (×N, one per section)
- `citations-agent` - no LLM, pure logic

The parallel factcheck spans appear as sibling nodes at the same level, confirming Send() API parallelism is working correctly.

---

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/health` | Health check - returns status and agent list |
| `POST` | `/api/run` | Start a new report generation session |
| `GET` | `/api/session/{id}/status` | Lightweight status poll - current agent, completed agents, stream updates |
| `GET` | `/api/session/{id}/stream` | SSE stream - real-time agent updates during running phase |
| `GET` | `/api/session/{id}` | Full session state including written sections and sources |
| `POST` | `/api/approve-outline` | Resume graph after human-in-the-loop outline approval |
| `POST` | `/api/export-pdf` | Generate and return styled PDF as file download |
| `POST` | `/api/upload-pdf` | Upload a PDF file for document ingestion |

### Example: Start a report

```bash
curl -X POST "http://localhost:8000/api/run" \
  -H "Content-Type: application/json" \
  -d '{"topic": "AI agents in healthcare", "depth": "quick", "input_urls": []}'
```

Response:
```json
{
  "session_id": "88418737-3b57-4cf9-a85f-6e7c4cc4ca94",
  "status": "running",
  "message": "Started research report generation for: AI agents in healthcare"
}
```

### Example: Poll status

```bash
curl "http://localhost:8000/api/session/88418737-3b57-4cf9-a85f-6e7c4cc4ca94/status"
```

Response when waiting for outline approval:
```json
{
  "status": "waiting_approval",
  "current_agent": "outline",
  "completed_agents": ["research", "factcheck", "outline"],
  "has_outline": true,
  "outline": [...],
  "sources_found": 14,
  "claims_checked": 8
}
```

### Example: Approve outline

```bash
curl -X POST "http://localhost:8000/api/approve-outline" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "88418737-3b57-4cf9-a85f-6e7c4cc4ca94",
    "outline": [
      {"section_id": "sec_1", "title": "Background", "description": "...", "order": 1},
      {"section_id": "sec_2", "title": "Current State", "description": "...", "order": 2},
      {"section_id": "sec_3", "title": "Future Trends", "description": "...", "order": 3}
    ],
    "edits": null
  }'
```

### Example: Export PDF

```bash
curl -X POST "http://localhost:8000/api/export-pdf?session_id=SESSION_ID" \
  --output report.pdf
```

---

## Project Structure

```
MULTI-AGENT-RESEARCH-REPORT-SYSTEM/
├── README.md                          ← You are here
├── .env.example                       ← Copy to backend/.env
├── backend/
│   ├── server.py                      ← FastAPI app - SSE, session mgmt, interrupt resume
│   ├── requirements.txt
│   ├── .env                           ← Your API keys (not committed)
│   ├── graph/
│   │   ├── state.py                   ← ReportState TypedDict (29 fields, 6 Pydantic models)
│   │   ├── supervisor.py              ← Deterministic routing - 8 rules, no LLM
│   │   ├── graph.py                   ← StateGraph + MemorySaver + interrupt_before=['synthesis']
│   │   └── agents/
│   │       ├── research.py            ← Tavily × 3 queries, URL dedup, relevance scoring
│   │       ├── document.py            ← pdfplumber + httpx/BS4, GPT-4o summary
│   │       ├── factcheck.py           ← Sequential fallback (not used in main flow)
│   │       ├── factcheck_parallel.py  ← SingleClaimState + Send() API parallel execution
│   │       ├── outline.py             ← GPT-4o outline + original_outline snapshot
│   │       ├── synthesis.py           ← Per-section loop, versioning skip, confidence scoring
│   │       └── citations.py           ← Source dedup, first-appearance ordering, [n] numbering
│   ├── export/
│   │   └── pdf_exporter.py            ← reportlab - cover, TOC, confidence bars, citations
│   ├── utils/
│   │   └── versioning.py              ← diff_outlines, build_versioning_report, compute_section_diff
│   └── eval/
│       └── langsmith_tracer.py        ← @traceable config, setup_tracing(), get_trace_url()
├── frontend/
│   ├── package.json
│   └── src/
│       ├── store.js                   ← Zustand - SSE EventSource + polling fallback, full state
│       ├── App.js
│       └── components/
│           ├── Sidebar.jsx            ← Input controls, agent pipeline, stats
│           ├── MainPanel.jsx          ← 5-state render (idle/running/approval/complete/error)
│           ├── OutlineApproval.jsx    ← Editable outline, versioning badges, summary banner
│           ├── ReportOutput.jsx       ← Section cards, confidence badges, citations, PDF download
│           ├── AgentStatus.jsx        ← Live agent status with pulse animation
│           └── TraceLog.jsx           ← Real-time SSE log, color-coded, LangSmith link
└── docs/
    └── ARCHITECTURE.md                ← Deep dive - state flow, confidence pipeline, checkpointing
```

---

## State Schema

The entire pipeline runs through a single `ReportState` TypedDict defined in `graph/state.py`. Every agent reads from and writes to this shared state.

| Field | Type | Description |
|---|---|---|
| `topic` | str | The research topic entered by the user |
| `depth` | str | `"quick"` (3 sections) or `"deep"` (6 sections) |
| `uploaded_pdfs` | list[str] | File paths of uploaded PDFs |
| `input_urls` | list[str] | URLs pasted by the user |
| `research_results` | list[dict] | SearchResult objects from Tavily |
| `document_chunks` | list[dict] | DocumentChunk objects from PDF/URL ingestion |
| `has_documents` | bool | True if user provided PDFs or URLs |
| `document_summary` | str | GPT-4o summary of all document chunks |
| `fact_check_results` | list[dict] | FactCheckResult per claim |
| `outline` | list[dict] | Generated OutlineSection list |
| `outline_approved` | bool | Set to True after human approval |
| `approved_outline` | list[dict] | Outline after user edits |
| `original_outline` | list[dict] | Snapshot before user edits (for versioning diff) |
| `changed_section_ids` | list[str] | Section IDs flagged as changed by diff |
| `sections_needing_rewrite` | list[str] | Subset of changed IDs with existing content |
| `written_sections` | list[dict] | WrittenSection objects from synthesis |
| `current_section_index` | int | Tracks which section synthesis is writing |
| `sources` | list[dict] | Deduplicated, numbered Source objects |
| `confidence_scores` | dict | section_id → float (0.0–1.0) |
| `overall_confidence` | float | Mean of all section confidence scores |
| `current_agent` | str | Name of currently executing agent |
| `completed_agents` | list[str] | Agents that have finished |
| `next_agent` | str | Supervisor's routing decision |
| `is_complete` | bool | True when citations agent completes |
| `stream_updates` | list[str] | Timestamped log messages for UI trace |
| `error` | Optional[str] | Error message if any agent fails |
| `retry_count` | int | Retry attempts counter |
| `messages` | Annotated[list, add_messages] | LangChain message history |

---

## Supervisor Routing Rules

The Supervisor reads the current state and applies these 8 rules in order:

```
1. research_results is empty
   → route to "research"

2. has_documents=True AND document_chunks is empty
   → route to "document"

3. research done, documents handled (or none), fact_check_results empty
   → route to "factcheck_fanout" (triggers Send() parallelism)

4. fact_check_results populated, outline empty
   → route to "outline"

5. outline exists, outline_approved=False
   → route to "synthesis" → interrupt() fires before node executes

6. outline_approved=True AND len(written_sections) < len(approved_outline)
   → route to "synthesis"

7. all sections written, "citations" not in completed_agents
   → route to "citations"

8. "citations" in completed_agents
   → route to "END"
```

---

## Troubleshooting

**Backend won't start - `KeyError: OPENAI_API_KEY`**

Make sure `backend/.env` exists and contains your API keys. Copy from `.env.example`:
```bash
cp .env.example backend/.env
```
Then edit `backend/.env` with your actual keys.

**`ModuleNotFoundError: No module named 'graph'`**

You are running Python from the wrong directory. Always run from inside `backend/`:
```bash
cd backend && uvicorn server:app --port 8000
```

**Frontend shows infinite loading spinner after submitting topic**

Check that the backend is running on port 8000. Open browser DevTools → Network tab and look for failed requests to `localhost:8000`. Confirm `frontend/package.json` has `"proxy": "http://localhost:8000"` or that `vite.config.js` has the proxy configured.

**`gpt-4o` API errors - model not found**

Confirm your OpenAI API key has access to `gpt-4o`. Check at [platform.openai.com/usage](https://platform.openai.com/usage).

**Tavily search returns zero results**

Check your `TAVILY_API_KEY` is set correctly in `backend/.env`. Test it:
```bash
cd backend && python -c "from tavily import TavilyClient; import os; from dotenv import load_dotenv; load_dotenv(); c = TavilyClient(api_key=os.getenv('TAVILY_API_KEY')); print(c.search('test query'))"
```

**LangSmith traces not appearing**

Verify these three env vars are all set in `backend/.env`:
```
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=your_key_here
LANGCHAIN_PROJECT=Multi-Agent-Research
```
Then check the backend startup logs - you should see `LangSmith tracing ENABLED`.

**PDF download returns error**

PDF export only works on completed sessions. Make sure the report has finished (status = `complete`) before clicking Download PDF.

**Graph keeps re-running after outline approval**

This means the `interrupt()` checkpoint is not working correctly. Check that `graph.py` compiles with `MemorySaver` and `interrupt_before=['synthesis']`:
```bash
cd backend && python -c "from graph.graph import build_graph; g = build_graph(); print('Checkpointer:', type(g.checkpointer).__name__)"
```
Expected: `Checkpointer: InMemorySaver` 

---

## Running the Test Suite

Test results are stored in the `test_reports/` directory.

Expected output:
```
🚀 Starting ResearchForge Backend API Tests
==================================================
✅ PASS | Health Check
✅ PASS | Start Report
✅ PASS | Get Session
✅ PASS | Session Stream
✅ PASS | Invalid Session Handling
✅ PASS | Malformed Request Handling
==================================================
📊 Test Results: 6/6 passed (100.0%)
🎉 All tests passed!
```

---

## Performance

Typical run times on a standard internet connection:

| Phase | Time | Notes |
|---|---|---|
| Web Research | 8–12s | 3 Tavily queries with retry logic |
| Fact Check (parallel) | 3–5s | All claims judged simultaneously via Send() API |
| Outline Generation | 3–5s | Single GPT-4o call |
| Synthesis (per section) | 4–6s | One GPT-4o call per section |
| Citations | <1s | Pure logic, no LLM |
| **Total (quick, no edits)** | **~25–35s** | 3 sections |
| **Total (deep, no edits)** | **~50–70s** | 6 sections |
| **With versioning (1 of 3 edited)** | **~15–20s** | 2 sections reused |

---

## Environment Variables Reference

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | ✅ Yes | OpenAI API key for GPT-4o |
| `TAVILY_API_KEY` | ✅ Yes | Tavily API key for web search |
| `CORS_ORIGINS` | No | Allowed CORS origins (default: `http://localhost:3000`) |
| `LANGCHAIN_TRACING_V2` | No | Set to `true` to enable LangSmith tracing |
| `LANGCHAIN_API_KEY` | No | LangSmith API key |
| `LANGCHAIN_PROJECT` | No | LangSmith project name (default: `Multi-Agent-Research`) |
| `LANGCHAIN_ENDPOINT` | No | LangSmith endpoint (default: `https://api.smith.langchain.com`) |

---

## Key Design Decisions

**Why deterministic Supervisor routing instead of LLM routing?**

LLM routing is non-deterministic - the same state can produce different routing decisions. For a production system where you need predictable behavior, testability, and no hallucinated routing decisions, deterministic rule-based routing is strictly better.

**Why `interrupt()` instead of re-invoking the graph?**

Re-invoking the graph from the start after outline approval wastes tokens on research and factcheck that already ran. `interrupt()` with MemorySaver checkpointing pauses the graph at a specific node, persists the full state, and resumes from exactly that position. Research and factcheck never re-run.

**Why Send() API for fact-checking instead of sequential?**

Sequential claim judging at 2s per claim means 8 claims = 16 seconds. The Send() API fans all 8 claims out simultaneously, reducing this to ~3 seconds regardless of claim count. This is the correct pattern for embarrassingly parallel workloads in LangGraph.

**Why section-by-section synthesis instead of full-report synthesis?**

Section-by-section synthesis enables streaming (user sees sections appear one by one), enables per-section confidence scoring, enables versioning (only re-write changed sections), and produces better quality output (smaller, focused prompts outperform large document prompts).

---

## License

MIT License - Nihanth Naidu Kalisetti, 2026
