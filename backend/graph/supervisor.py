"""
ResearchForge Supervisor Agent
Routes control flow between specialized agents using structured output.
"""
import os
import json
import logging
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from langsmith import traceable

load_dotenv()
logger = logging.getLogger(__name__)

MODEL = "gpt-4o"


class SupervisorDecision(BaseModel):
    """Structured output for supervisor routing decisions"""
    next_agent: Literal["research", "document", "factcheck", "outline", "synthesis", "citations", "END", "WAIT_FOR_HUMAN"]
    reasoning: str = Field(description="Brief explanation for routing decision")


def get_supervisor_decision(state: dict) -> SupervisorDecision:
    """
    Determine which agent should run next based on current state.
    Uses deterministic rules first, falls back to LLM for edge cases.
    """
    # Extract state
    research_results = state.get("research_results", [])
    has_documents = state.get("has_documents", False)
    document_chunks = state.get("document_chunks", [])
    fact_check_results = state.get("fact_check_results", [])
    outline = state.get("outline", [])
    outline_approved = state.get("outline_approved", False)
    approved_outline = state.get("approved_outline", [])
    written_sections = state.get("written_sections", [])
    sources = state.get("sources", [])
    
    # Routing rules
    # 1. If research_results is empty → route to "research"
    if not research_results:
        retry_count = state.get("retry_count", 0)
        if retry_count >= 2:
            # Research failed after 2 attempts — surface error instead of looping forever
            return SupervisorDecision(
                next_agent="END",
                reasoning=f"Research failed after {retry_count} attempts. Check Tavily API key and network connectivity."
            )
        return SupervisorDecision(
            next_agent="research",
            reasoning=f"No research results yet. Starting web research phase (attempt {retry_count + 1})."
        )
    
    # 2. If research done AND (has_documents is True AND document_chunks is empty) → route to "document"
    if research_results and has_documents and not document_chunks:
        return SupervisorDecision(
            next_agent="document",
            reasoning="Research complete. User provided documents - starting document ingestion."
        )
    
    # 3. If research done AND documents handled (or has_documents is False) AND fact_check_results empty → route to "factcheck"
    documents_handled = not has_documents or (has_documents and document_chunks)
    if research_results and documents_handled and not fact_check_results:
        return SupervisorDecision(
            next_agent="factcheck",
            reasoning="Research and documents processed. Starting fact-check phase."
        )
    
    # 4. If fact_check done but outline empty → route to "outline"
    if fact_check_results and not outline:
        return SupervisorDecision(
            next_agent="outline",
            reasoning="Fact-checking complete. Generating report outline."
        )
    
    # 5. If outline exists but outline_approved is False → route to "synthesis"
    # The graph's interrupt_before=["synthesis"] will pause execution here,
    # giving the user a chance to review/edit the outline before synthesis runs.
    # This replaces the old WAIT_FOR_HUMAN → END hack with a proper interrupt checkpoint.
    if outline and not outline_approved:
        return SupervisorDecision(
            next_agent="synthesis",
            reasoning="Outline generated. Routing to synthesis — interrupt will pause for human approval."
        )
    
    # 6. If outline_approved and not all sections written → route to "synthesis"
    if outline_approved and approved_outline:
        if len(written_sections) < len(approved_outline):
            return SupervisorDecision(
                next_agent="synthesis",
                reasoning=f"Writing section {len(written_sections) + 1} of {len(approved_outline)}."
            )
    
    # 7. If all sections written and citations not yet run → route to "citations"
    citations_done = "citations" in state.get("completed_agents", [])
    if approved_outline and len(written_sections) >= len(approved_outline) and not citations_done:
        return SupervisorDecision(
            next_agent="citations",
            reasoning="All sections written. Generating citation list."
        )
    
    # 8. If citations done → route to "END"
    if citations_done:
        return SupervisorDecision(
            next_agent="END",
            reasoning="Report complete with all citations."
        )
    
    # Fallback - should not reach here
    return SupervisorDecision(
        next_agent="END",
        reasoning="All tasks complete or unknown state."
    )


@traceable(name="supervisor", run_type="chain")
def supervisor_node(state: dict) -> dict:
    """
    LangGraph node for the Supervisor agent.
    Reads current state and decides which agent to call next.
    """
    timestamp = datetime.now().strftime("%H:%M:%S")
    
    try:
        decision = get_supervisor_decision(state)
        
        # Update state
        state["current_agent"] = decision.next_agent
        state["next_agent"] = decision.next_agent
        
        # Add to stream updates
        if decision.next_agent == "WAIT_FOR_HUMAN":
            update_msg = f"[{timestamp}] ⏸ WAITING FOR HUMAN APPROVAL - Review and approve the outline"
        elif decision.next_agent == "END":
            update_msg = f"[{timestamp}] ✓ Supervisor → Report generation complete"
            state["is_complete"] = True
        else:
            update_msg = f"[{timestamp}] Supervisor → routing to {decision.next_agent} agent ({decision.reasoning})"
        
        state["stream_updates"].append(update_msg)
        logger.info(update_msg)
        
        return state
        
    except Exception as e:
        error_msg = f"[{timestamp}] ✗ Supervisor error: {str(e)}"
        state["stream_updates"].append(error_msg)
        state["error"] = str(e)
        state["next_agent"] = "END"
        logger.error(error_msg)
        return state
