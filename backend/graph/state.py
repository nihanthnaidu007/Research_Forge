"""
ResearchForge State Schema
Single source of truth for the entire multi-agent graph.
"""

import operator
from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

# --- Sub-models ---


class OutlineSection(BaseModel):
    """A single section in the report outline"""

    section_id: str  # e.g. "sec_1"
    title: str  # e.g. "Background & Context"
    description: str  # 1-2 sentence guide for synthesis agent
    order: int  # display order


class SearchResult(BaseModel):
    """A single search result from Tavily"""

    url: str
    title: str
    snippet: str
    source_domain: str
    relevance_score: float = Field(default=0.8, ge=0.0, le=1.0)


class FactCheckResult(BaseModel):
    """Result of fact-checking a single claim"""

    claim: str
    verdict: str  # "SUPPORTED" | "PARTIALLY_SUPPORTED" | "UNSUPPORTED"
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str  # one sentence explanation
    supporting_urls: list[str] = Field(default_factory=list)


class WrittenSection(BaseModel):
    """A fully written section of the report"""

    section_id: str
    title: str
    content: str  # full written prose
    word_count: int
    sources_used: list[str] = Field(default_factory=list)  # list of URLs used


class Source(BaseModel):
    """A formatted citation source"""

    url: str
    title: str
    domain: str
    citation_number: int  # e.g. [1], [2] in the report


class DocumentChunk(BaseModel):
    """A chunk of content extracted from PDF or URL"""

    source_label: str  # filename or URL
    source_type: str  # "pdf" | "url"
    chunk_index: int  # page number or paragraph index
    content: str  # extracted text
    word_count: int


# --- Main Graph State ---


class ReportState(TypedDict):
    """Main state object passed through the entire graph"""

    # Input
    topic: str
    depth: str  # "quick" (3 sections) | "deep" (6 sections)
    uploaded_pdfs: list[str]  # file paths of user-uploaded PDFs (empty list if none)
    input_urls: list[str]  # user-pasted URLs for ingestion (empty list if none)

    # Research phase
    research_results: list[dict]  # List of SearchResult as dicts

    # Document ingestion phase
    document_chunks: list[dict]  # extracted content from PDFs + URLs
    has_documents: bool  # True if user provided any PDFs or URLs
    document_summary: str  # LLM-generated summary of all ingested docs

    # Fact-check phase
    fact_check_results: list[dict]  # List of FactCheckResult as dicts
    parallel_fact_check_results: Annotated[
        list[dict], operator.add
    ]  # Send() API accumulator

    # Outline phase
    outline: list[dict]  # List of OutlineSection as dicts
    outline_approved: bool  # set to True after human-in-the-loop
    approved_outline: list[dict]  # List of OutlineSection after approval
    user_outline_edits: str | None  # raw text edits from user

    # Report versioning — tracks which sections changed after outline edit
    original_outline: list[dict]  # snapshot before user edits
    changed_section_ids: list[str]
    sections_needing_rewrite: list[str]  # section_ids flagged for re-synthesis

    # Synthesis phase
    written_sections: list[dict]  # List of WrittenSection as dicts
    current_section_index: int  # tracks which section is being written

    # Citation phase
    sources: list[dict]  # List of Source as dicts

    # Confidence scoring
    confidence_scores: dict  # section_id -> float (0.0-1.0)
    overall_confidence: float

    # Orchestration
    current_agent: str  # name of currently running agent
    completed_agents: list[str]  # agents that have finished
    next_agent: str  # supervisor's routing decision
    is_complete: bool

    # Streaming / UI
    stream_updates: list[str]  # real-time status messages for UI

    # Error handling
    error: str | None
    retry_count: int

    # LangChain messages (required by LangGraph)
    messages: Annotated[list[BaseMessage], add_messages]


def create_initial_state(
    topic: str,
    depth: str = "quick",
    uploaded_pdfs: list[str] | None = None,
    input_urls: list[str] | None = None,
) -> ReportState:
    """Create an initial state for the graph"""
    uploaded_pdfs = uploaded_pdfs or []
    input_urls = input_urls or []

    return {
        "topic": topic,
        "depth": depth,
        "uploaded_pdfs": uploaded_pdfs,
        "input_urls": input_urls,
        "research_results": [],
        "document_chunks": [],
        "has_documents": bool(uploaded_pdfs or input_urls),
        "document_summary": "",
        "fact_check_results": [],
        "parallel_fact_check_results": [],
        "outline": [],
        "outline_approved": False,
        "approved_outline": [],
        "user_outline_edits": None,
        "original_outline": [],
        "changed_section_ids": [],
        "sections_needing_rewrite": [],
        "written_sections": [],
        "current_section_index": 0,
        "sources": [],
        "confidence_scores": {},
        "overall_confidence": 0.0,
        "current_agent": "",
        "completed_agents": [],
        "next_agent": "",
        "is_complete": False,
        "stream_updates": [],
        "error": None,
        "retry_count": 0,
        "messages": [],
    }
