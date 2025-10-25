"""
ResearchForge LangGraph Graph Compilation
Orchestrates the multi-agent workflow with interrupt() support for human-in-the-loop.
Uses Send() API for parallel fact-checking.
"""
import logging
from datetime import datetime
from langgraph.graph import StateGraph, END
from langgraph.types import Send
from langgraph.checkpoint.memory import MemorySaver
from graph.state import ReportState
from graph.supervisor import supervisor_node
from graph.agents.research import research_node
from graph.agents.document import document_node
from graph.agents.factcheck import factcheck_node, extract_claims_from_research
from graph.agents.factcheck_parallel import factcheck_single_node
from graph.agents.outline import outline_node
from graph.agents.synthesis import synthesis_node
from graph.agents.citations import citations_node

logger = logging.getLogger(__name__)


def route_from_supervisor(state: dict) -> str:
    """Route based on supervisor's decision"""
    return state.get("next_agent", "END")


def fan_out_claims(state: dict):
    """
    Fan-out function for Send() API.
    Called by the conditional edge from factcheck_fanout node.
    Returns one Send() object per claim — all run in parallel.
    If no claims, routes directly to factcheck_merge.
    """
    research_results = state.get("research_results", [])
    claims = extract_claims_from_research(research_results)

    if not claims:
        logger.warning("No claims extracted — skipping parallel factcheck")
        return "factcheck_merge"

    logger.info(f"Fanning out {len(claims)} claims for parallel fact-checking")

    return [
        Send("factcheck_single", {
            "claim": claim,
            "sources": research_results,
        })
        for claim in claims
    ]


def factcheck_merge_node(state: dict) -> dict:
    """
    Merge node — collects all parallel fact-check results and updates state.
    Runs after all parallel factcheck_single nodes complete.
    The operator.add reducer on parallel_fact_check_results has already
    accumulated results from all Send() invocations.
    """
    timestamp = datetime.now().strftime("%H:%M:%S")

    parallel_results = state.get("parallel_fact_check_results", [])

    if not parallel_results:
        logger.warning("No parallel results to merge — factcheck may have been skipped")
        state["completed_agents"] = state.get("completed_agents", []) + ["factcheck"]
        state["stream_updates"] = state.get("stream_updates", []) + [
            f"[{timestamp}] FactCheck Agent → No claims to verify"
        ]
        return state

    # Copy parallel results into the canonical fact_check_results field
    state["fact_check_results"] = parallel_results

    # Compute verdict summary
    supported = sum(1 for r in parallel_results if r.get("verdict") == "SUPPORTED")
    partial = sum(1 for r in parallel_results if r.get("verdict") == "PARTIALLY_SUPPORTED")
    unsupported = sum(1 for r in parallel_results if r.get("verdict") == "UNSUPPORTED")
    avg_confidence = round(
        sum(r.get("confidence", 0) for r in parallel_results) / max(len(parallel_results), 1), 2
    )

    state["completed_agents"] = state.get("completed_agents", []) + ["factcheck"]
    state["stream_updates"] = state.get("stream_updates", []) + [
        f"[{timestamp}] ✓ FactCheck Agent → {len(parallel_results)} claims verified in parallel: "
        f"{supported} SUPPORTED / {partial} PARTIAL / {unsupported} UNSUPPORTED | "
        f"Avg confidence: {avg_confidence}"
    ]

    logger.info(f"Factcheck merge complete: {len(parallel_results)} results merged")
    return state


def build_graph():
    """
    Build and compile the LangGraph workflow.
    Uses MemorySaver checkpointer for interrupt() support.
    Uses Send() API for parallel fact-checking.
    The graph pauses BEFORE the synthesis node when outline approval is needed.
    """
    workflow = StateGraph(ReportState)

    # Add all agent nodes
    workflow.add_node("supervisor", supervisor_node)
    workflow.add_node("research", research_node)
    workflow.add_node("document", document_node)
    workflow.add_node("factcheck", factcheck_node)  # Sequential fallback (not used in main flow)
    workflow.add_node("outline", outline_node)
    workflow.add_node("synthesis", synthesis_node)
    workflow.add_node("citations", citations_node)

    # Parallel factcheck nodes — Send() API pattern
    workflow.add_node("factcheck_fanout", lambda state: state)  # Pass-through to trigger fan-out
    workflow.add_node("factcheck_single", factcheck_single_node)
    workflow.add_node("factcheck_merge", factcheck_merge_node)

    # Entry point
    workflow.set_entry_point("supervisor")

    # Supervisor routes to agents — factcheck now routes to parallel fanout
    workflow.add_conditional_edges(
        "supervisor",
        route_from_supervisor,
        {
            "research": "research",
            "document": "document",
            "factcheck": "factcheck_fanout",  # Route to parallel fan-out instead of sequential
            "outline": "outline",
            "synthesis": "synthesis",
            "citations": "citations",
            "END": END,
            "WAIT_FOR_HUMAN": END,
        }
    )

    # Standard agents route back to supervisor
    workflow.add_edge("research", "supervisor")
    workflow.add_edge("document", "supervisor")
    workflow.add_edge("factcheck", "supervisor")  # Fallback sequential path
    workflow.add_edge("outline", "supervisor")
    workflow.add_edge("synthesis", "supervisor")
    workflow.add_edge("citations", "supervisor")

    # Parallel factcheck fan-out edges
    workflow.add_conditional_edges(
        "factcheck_fanout",
        fan_out_claims,
        ["factcheck_single", "factcheck_merge"]
    )
    workflow.add_edge("factcheck_single", "factcheck_merge")
    workflow.add_edge("factcheck_merge", "supervisor")

    # MemorySaver checkpointer enables interrupt() and state persistence across invocations
    memory = MemorySaver()

    # interrupt_before=["synthesis"] means the graph pauses BEFORE synthesis runs
    # This gives the user a chance to review and edit the outline before writing begins
    return workflow.compile(
        checkpointer=memory,
        interrupt_before=["synthesis"]
    )


# Module-level compiled graph instance (singleton)
_compiled_graph = None
_memory = None


def get_graph():
    """Get or create the compiled graph with MemorySaver"""
    global _compiled_graph, _memory
    if _compiled_graph is None:
        _compiled_graph = build_graph()
        logger.info("Graph compiled with MemorySaver + Send() parallel factcheck + interrupt_before=['synthesis']")
    return _compiled_graph


def get_memory():
    """Get the MemorySaver instance for direct state access"""
    get_graph()  # Ensure graph is compiled
    return _memory
