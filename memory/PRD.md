# ResearchForge - Product Requirements Document

## Original Problem Statement
Build a production-grade Multi-Agent Research & Report Generation System that takes a topic from the user, orchestrates 6 specialized AI agents through a LangGraph Supervisor pattern, and produces a structured, cited, confidence-scored research report with human-in-the-loop approval at the outline stage.

## User Personas
1. **Researchers**: Need comprehensive, cited research reports on complex topics
2. **Analysts**: Require fact-checked insights with confidence scoring
3. **Consultants**: Want structured reports they can edit and customize
4. **Students**: Need well-organized research with proper citations

## Core Requirements (Static)
- Topic input with Quick (3 sections) / Deep (6 sections) modes
- 6-agent pipeline: Research → Document → FactCheck → Outline → Synthesis → Citations
- Human-in-the-loop outline approval
- Confidence scoring per section
- Real-time execution trace
- Citation management

## What's Been Implemented (Jan 2026)
### Backend
- ✅ LangGraph multi-agent system with StateGraph
- ✅ Supervisor routing with deterministic rules
- ✅ WebResearchAgent (Tavily API integration)
- ✅ DocumentAgent (PDF/URL ingestion)
- ✅ FactCheckAgent (LLM-as-judge)
- ✅ OutlineAgent (structured output)
- ✅ SynthesisAgent (per-section writing)
- ✅ CitationAgent (deduplication)
- ✅ FastAPI with SSE streaming
- ✅ GPT 5.4 integration (max_completion_tokens)

### Frontend
- ✅ 3-panel dashboard (Sidebar, Main, Trace Log)
- ✅ Dark operational theme
- ✅ Agent status tracking with animations
- ✅ Outline approval zone with editable sections
- ✅ Report section cards with confidence badges
- ✅ Real-time trace log
- ✅ Zustand state management

## Prioritized Backlog

### P0 (Critical - Stubbed)
- [ ] PDF Export (export/pdf_exporter.py)
- [ ] LangGraph interrupt() for true human-in-the-loop

### P1 (Important)
- [ ] LangSmith tracing integration
- [ ] Token-level SSE streaming for synthesis
- [ ] Report versioning (diff outlines)

### P2 (Nice to Have)
- [ ] Send() API for parallel fact-checking
- [ ] User authentication
- [ ] Report history/saving
- [ ] Export to Word/Markdown

## Next Tasks
1. Implement PDF export with reportlab
2. Add LangSmith callbacks for observability
3. Implement proper interrupt() checkpointing
4. Add report versioning diff logic
5. Token streaming for real-time section writing

## Technical Notes
- GPT 5.4 requires `max_completion_tokens` instead of `max_tokens`
- Tavily API provides advanced search with relevance scoring
- Supervisor uses deterministic routing (not LLM-based)
- Sessions stored in memory (add Redis/MongoDB for persistence)
