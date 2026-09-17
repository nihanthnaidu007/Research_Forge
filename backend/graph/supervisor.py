"""
ResearchForge Supervisor Agent
Routes control flow between specialized agents using structured output.
"""

import logging
import os
from datetime import datetime
from typing import Literal

from dotenv import load_dotenv
from langsmith import traceable
from pydantic import BaseModel, Field

load_dotenv()
logger = logging.getLogger(__name__)

MODEL = "gpt-4o"

# --- Deep-research loop thresholds (W4) ---
# Read at call time so tests and deployments can tune them without a rebuild.

# overall_confidence (synthesis.py mean of per-section scores) below this
# signals weak coverage and trips a re-research round.
DEFAULT_GAP_CONFIDENCE_THRESHOLD = 0.6
DEFAULT_GAP_UNSUPPORTED_RATE = 0.3  # share of UNSUPPORTED verdicts that trips a round
DEFAULT_MAX_RESEARCH_ROUNDS = (
    2  # hard cap: makes research↔synthesis ping-pong impossible
)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning(f"Invalid {name}={raw!r} — using default {default}")
        return default


def _env_positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning(f"Invalid {name}={raw!r} — using default {default}")
        return default
    return value if value >= 1 else default


def max_research_rounds() -> int:
    """Hard cap on gap-driven re-research rounds (env-tunable)."""
    return _env_positive_int("MAX_RESEARCH_ROUNDS", DEFAULT_MAX_RESEARCH_ROUNDS)


def _keyword_overlap(left: str, right: str) -> int:
    """Shared-word count between two texts — same >=2 rule synthesis uses
    to match fact-check results to sections."""
    return len(set(left.lower().split()) & set(right.lower().split()))


# The supervisor uses deterministic rule-based routing, not LLM routing.
# SupervisorDecision is a structured return type for get_supervisor_decision().
# The @traceable decorator labels this as "chain" in LangSmith for visibility
# but no LLM call is made here.
class SupervisorDecision(BaseModel):
    """Structured output for supervisor routing decisions"""

    next_agent: Literal[
        "research", "document", "factcheck", "outline", "synthesis", "citations", "END"
    ]
    reasoning: str = Field(description="Brief explanation for routing decision")


def evaluate_coverage_gaps(state: dict) -> tuple[bool, list[str], list[str]]:
    """
    Deterministic gap signal for the deep-research loop (W4).

    Trips when the fully written report shows weak coverage by either:
    - overall_confidence below RESEARCH_GAP_CONFIDENCE_THRESHOLD, or
    - the share of UNSUPPORTED fact-check verdicts above
      RESEARCH_GAP_UNSUPPORTED_RATE.

    Both signals are computed from tallies/confidences that already exist
    (synthesis.py per-section scores; graph.py verdict tallies). No LLM call.

    Returns (tripped, gap_descriptions, affected_section_ids).
    affected_section_ids is never empty when tripped — a round with nothing
    to rewrite would be pure waste, so the lowest-confidence section is the
    deterministic fallback.
    """
    confidence_threshold = _env_float(
        "RESEARCH_GAP_CONFIDENCE_THRESHOLD", DEFAULT_GAP_CONFIDENCE_THRESHOLD
    )
    unsupported_rate_cap = _env_float(
        "RESEARCH_GAP_UNSUPPORTED_RATE", DEFAULT_GAP_UNSUPPORTED_RATE
    )

    approved_outline = state.get("approved_outline", [])
    confidence_scores = state.get("confidence_scores", {})
    fact_check_results = state.get("fact_check_results", [])

    outline_by_id = {
        s.get("section_id", f"sec_{i + 1}"): s for i, s in enumerate(approved_outline)
    }
    title_by_id = {sid: (s.get("title", "") or sid) for sid, s in outline_by_id.items()}

    gaps: list[str] = []
    affected: list[str] = []

    overall = state.get("overall_confidence", 0.0)
    if overall < confidence_threshold:
        low = sorted(
            (sid, score)
            for sid, score in confidence_scores.items()
            if score < confidence_threshold
        )
        for sid, score in low[:3]:  # cap gap fan-out per round
            affected.append(sid)
            gaps.append(
                f"Low confidence ({score:.2f}) in section "
                f"'{title_by_id.get(sid, sid)}' — needs stronger sourcing"
            )
        if not affected and outline_by_id:
            # Mean dipped below threshold without any single section doing so —
            # target the weakest section deterministically.
            weakest = min(
                confidence_scores.items(), key=lambda item: item[1], default=None
            )
            if weakest is not None:
                affected.append(weakest[0])
            else:
                affected.append(next(iter(outline_by_id)))
            gaps.append(
                f"Overall confidence {overall:.2f} below threshold "
                f"{confidence_threshold:.2f} — re-research the weakest section"
            )

    if fact_check_results:
        unsupported = [
            fc for fc in fact_check_results if fc.get("verdict") == "UNSUPPORTED"
        ]
        rate = len(unsupported) / len(fact_check_results)
        if rate > unsupported_rate_cap:
            claim_text = "; ".join(
                (fc.get("claim", "") or "unspecified claim")[:80]
                for fc in unsupported[:3]
            )
            gaps.append(
                f"{len(unsupported)} of {len(fact_check_results)} claims UNSUPPORTED "
                f"({rate:.0%} > {unsupported_rate_cap:.0%}): {claim_text}"
            )
            written_ids = {
                s.get("section_id") for s in state.get("written_sections", [])
            }
            for fc in unsupported:
                claim = fc.get("claim", "")
                match = next(
                    (
                        sid
                        for sid, section in outline_by_id.items()
                        if _keyword_overlap(
                            claim,
                            section.get("title", "")
                            + " "
                            + section.get("description", ""),
                        )
                        >= 2
                    ),
                    None,
                )
                if match is not None and match in written_ids and match not in affected:
                    affected.append(match)
            if not affected and confidence_scores:
                # Unsupported claims matched no section by keywords — fall back
                # to the weakest section so the round still has a target.
                affected.append(
                    min(confidence_scores.items(), key=lambda item: item[1])[0]
                )

    tripped = bool(gaps) and bool(affected)
    return tripped, gaps, affected


def apply_research_round(state: dict, gaps: list[str], affected: list[str]) -> None:
    """
    Mutate state for one gap-driven re-research round (W4).

    Flagging happens BEFORE re-synthesis, never after (R6 — silent staleness
    is a defect): affected section ids join changed_section_ids and
    sections_needing_rewrite, and the affected sections are pruned from
    written_sections. The pruning is what makes the supervisor's
    sections-remaining rule route back into synthesis, where the versioning
    unchanged-skip reuses every section not flagged. synthesis is dropped
    from completed_agents — it has work to do again.
    """
    timestamp = datetime.now().strftime("%H:%M:%S")
    state["research_rounds"] = (state.get("research_rounds") or 0) + 1
    state["coverage_gaps"] = list(gaps)

    affected_set = set(affected)
    written = state.get("written_sections", [])
    written_ids = {s.get("section_id") for s in written}

    state["changed_section_ids"] = list(
        dict.fromkeys(state.get("changed_section_ids", []) + affected)
    )
    state["sections_needing_rewrite"] = list(
        dict.fromkeys(
            state.get("sections_needing_rewrite", [])
            + [sid for sid in affected if sid in written_ids]
        )
    )
    state["written_sections"] = [
        s for s in written if s.get("section_id") not in affected_set
    ]
    state["current_section_index"] = 0
    state["completed_agents"] = [
        agent for agent in state.get("completed_agents", []) if agent != "synthesis"
    ]
    state["stream_updates"] = state.get("stream_updates", []) + [
        f"[{timestamp}] 🔁 Deep-research round {state['research_rounds']} tripped: "
        + " | ".join(gaps)
    ]
    logger.info(
        "Re-research round %d for '%s': %d section(s) flagged for rewrite",
        state["research_rounds"],
        state.get("topic", ""),
        len(affected),
    )


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

    # Routing rules
    # 1. If research_results is empty → route to "research"
    if not research_results:
        retry_count = state.get("retry_count", 0)
        if retry_count >= 2:
            # Research failed after 2 attempts — surface error instead of looping forever
            return SupervisorDecision(
                next_agent="END",
                reasoning=f"Research failed after {retry_count} attempts. Check Tavily API key and network connectivity.",
            )
        return SupervisorDecision(
            next_agent="research",
            reasoning=f"No research results yet. Starting web research phase (attempt {retry_count + 1}).",
        )

    # 2. If research done AND (has_documents is True AND document_chunks is empty) → route to "document"
    if research_results and has_documents and not document_chunks:
        return SupervisorDecision(
            next_agent="document",
            reasoning="Research complete. User provided documents - starting document ingestion.",
        )

    # 3. If research done AND documents handled (or has_documents is False) AND fact_check_results empty → route to "factcheck"
    documents_handled = not has_documents or (has_documents and document_chunks)
    if research_results and documents_handled and not fact_check_results:
        return SupervisorDecision(
            next_agent="factcheck",
            reasoning="Research and documents processed. Starting fact-check phase.",
        )

    # 4. If fact_check done but outline empty → route to "outline"
    if fact_check_results and not outline:
        return SupervisorDecision(
            next_agent="outline",
            reasoning="Fact-checking complete. Generating report outline.",
        )

    # 5. If outline exists but outline_approved is False → route to "synthesis"
    # The graph's interrupt_before=["synthesis"] will pause execution here,
    # giving the user a chance to review/edit the outline before synthesis runs.
    # This replaces the old WAIT_FOR_HUMAN → END hack with a proper interrupt checkpoint.
    if outline and not outline_approved:
        return SupervisorDecision(
            next_agent="synthesis",
            reasoning="Outline generated. Routing to synthesis — interrupt will pause for human approval.",
        )

    # 6. If outline_approved and not all sections written → route to "synthesis"
    if outline_approved and approved_outline:
        if len(written_sections) < len(approved_outline):
            return SupervisorDecision(
                next_agent="synthesis",
                reasoning=f"Writing section {len(written_sections) + 1} of {len(approved_outline)}.",
            )

    # 6.5 Deep-research loop (W4): all sections are written, but a
    # deterministic gap signal says coverage is weak — run one more
    # research round scoped to the gaps instead of moving to citations.
    # Post-approval only: written_sections stays empty until the outline is
    # approved (interrupt_before=["synthesis"]), so this rule cannot fire in
    # the initial phase; round work rides the resume-loop shape (R4/R7).
    # The hard round cap makes research↔synthesis ping-pong structurally
    # impossible (R1); the token budget is the economic backstop.
    citations_done = "citations" in state.get("completed_agents", [])
    if (
        outline_approved
        and approved_outline
        and len(written_sections) >= len(approved_outline)
        and not citations_done
        and (state.get("research_rounds") or 0) < max_research_rounds()
    ):
        tripped, gaps, affected = evaluate_coverage_gaps(state)
        if tripped:
            apply_research_round(state, gaps, affected)
            return SupervisorDecision(
                next_agent="research",
                reasoning=(
                    f"Coverage gap signal tripped re-research round "
                    f"{state['research_rounds']} of {max_research_rounds()}: "
                    + " ".join(gaps[:2])
                ),
            )

    # 7. If all sections written and citations not yet run → route to "citations"
    if (
        approved_outline
        and len(written_sections) >= len(approved_outline)
        and not citations_done
    ):
        return SupervisorDecision(
            next_agent="citations",
            reasoning="All sections written. Generating citation list.",
        )

    # 8. If citations done → route to "END"
    if citations_done:
        return SupervisorDecision(
            next_agent="END", reasoning="Report complete with all citations."
        )

    # Fallback - should not reach here
    return SupervisorDecision(
        next_agent="END", reasoning="All tasks complete or unknown state."
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
        if decision.next_agent == "END":
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
