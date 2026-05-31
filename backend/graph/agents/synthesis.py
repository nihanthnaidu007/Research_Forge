"""
SynthesisAgent - Writes report sections grounded in research and sources
"""
import os
import json
import logging
from datetime import datetime
from typing import List
from utils.clients import get_openai_client
from utils.llm_utils import call_with_retry
from utils.scoring import VERDICT_SCORES
from graph.state import ReportState
from dotenv import load_dotenv
from langsmith import traceable

load_dotenv()
logger = logging.getLogger(__name__)

# Initialize OpenAI client
MODEL = "gpt-4o"


SYNTHESIS_SYSTEM_PROMPT = """You are a senior research analyst writing one section of a structured research report. Your output will be published directly - write as if a human expert wrote it.

QUALITY REQUIREMENTS:
- Write in clear, authoritative, evidence-based prose
- No bullet points, no numbered lists, no markdown formatting of any kind
- No headers or subheadings - this is flowing prose only
- Cite sources inline using exactly this format: [source: URL] with no extra spaces
- Every factual claim must be supported by at least one cited source
- Write exactly 180 to 250 words - count carefully
- Start with a strong, specific topic sentence that states the section's core insight
- End with a sentence that transitions naturally to the next topic
- Write in third person - no "we", no "I", no "you"

STRICTLY PROHIBITED:
- Do not use em dashes anywhere in your output
- Do not use: "It is worth noting", "In conclusion", "Furthermore", "Moreover"
- Do not use: "It is important to", "Notably", "Interestingly", "Significantly"
- Do not use passive voice where active voice is clearer
- Do not repeat the section title in your opening sentence
- Do not use markdown bold (**text**) or italic (*text*)
- Do not pad with generic statements about the topic being important or relevant
- Do not use the word "delve" or "landscape" or "crucial" or "key" anywhere

OUTPUT FORMAT:
Write only the section prose. No heading. No word count note. No meta-commentary. No preamble."""


def compute_section_confidence(section: dict, fact_check_results: List[dict]) -> float:
    """
    Match fact-check results relevant to this section's topic.
    Return weighted mean of (verdict_score * confidence) for matched claims.
    """
    if not fact_check_results:
        return 0.5  # Neutral default
    
    section_title = section.get("title", "").lower()
    section_desc = section.get("description", "").lower()
    section_keywords = set((section_title + " " + section_desc).split())
    
    matched_scores = []
    for fc in fact_check_results:
        claim = fc.get("claim", "").lower()
        claim_words = set(claim.split())
        
        # Simple keyword overlap matching
        overlap = len(section_keywords & claim_words)
        if overlap >= 2:  # At least 2 word overlap
            verdict = fc.get("verdict", "PARTIALLY_SUPPORTED")
            confidence = fc.get("confidence", 0.5)
            verdict_score = VERDICT_SCORES.get(verdict, 0.5)
            matched_scores.append(verdict_score * confidence)
    
    if matched_scores:
        return sum(matched_scores) / len(matched_scores)
    return 0.5  # Neutral if no matches


@traceable(name="write-section", run_type="llm")
def write_section(section: dict, research_results: List[dict], 
                  fact_check_results: List[dict], document_summary: str = "",
                  is_last: bool = False) -> dict:
    """
    Write a single report section using LLM, grounded in sources.
    """
    # Build source context
    source_context = "\n".join(
        f"[{r.get('url', '')}] {r.get('title', 'Unknown')}: {r.get('snippet', '')[:300]}"
        for r in research_results[:8]
    )
    
    # Add document summary if available
    doc_context = f"\nAdditional insights from documents: {document_summary[:400]}" if document_summary else ""
    
    # Relevant fact-checks
    relevant_facts = "\n".join(
        f"- {fc.get('claim', '')[:150]} ({fc.get('verdict', 'UNKNOWN')})"
        for fc in fact_check_results[:5]
    )
    
    ending_instruction = "End with a concluding thought." if is_last else "End with a transition to the next topic."
    
    user_prompt = f"""Write the following section:

Section Title: {section.get('title', 'Section')}
Section Focus: {section.get('description', 'Cover key findings')}

Available Sources:
{source_context}
{doc_context}

Verified Facts:
{relevant_facts}

Instructions: Write 180-250 words. Cite sources inline as [source: url]. {ending_instruction}"""

    try:
        response = call_with_retry(
            lambda: get_openai_client().chat.completions.create(
                model=MODEL,
                temperature=0.5,
                max_completion_tokens=600,
                messages=[
                    {"role": "system", "content": SYNTHESIS_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ]
            ),
            label=f"synthesis write_section [{section.get('section_id', '?')}]",
        )
        
        content = response.choices[0].message.content.strip()
        word_count = len(content.split())
        
        # Extract sources used (URLs mentioned in content)
        sources_used = []
        for r in research_results:
            if r.get("url", "") in content:
                sources_used.append(r.get("url", ""))
        
        return {
            "section_id": section.get("section_id", ""),
            "title": section.get("title", ""),
            "content": content,
            "word_count": word_count,
            "sources_used": sources_used
        }
        
    except Exception as e:
        logger.error(f"Section writing error: {str(e)}")
        return {
            "section_id": section.get("section_id", ""),
            "title": section.get("title", ""),
            "content": f"Error generating section content: {str(e)}",
            "word_count": 0,
            "sources_used": []
        }


# CURSOR_TODO: Add streaming token output so frontend can show words appearing in real time
@traceable(name="synthesis-agent", run_type="chain")
def synthesis_node(state: ReportState) -> ReportState:
    """
    LangGraph node for SynthesisAgent.
    Writes one section at a time, incrementing index on each call.
    """
    timestamp = datetime.now().strftime("%H:%M:%S")
    approved_outline = state.get("approved_outline", [])
    current_index = state.get("current_section_index", 0)
    research_results = state.get("research_results", [])
    fact_check_results = state.get("fact_check_results", [])
    document_summary = state.get("document_summary", "")
    
    if current_index >= len(approved_outline):
        state["stream_updates"].append(f"[{timestamp}] Synthesis Agent → All sections complete")
        return state
    
    section = approved_outline[current_index]
    section_title = section.get("title", f"Section {current_index + 1}")
    is_last = current_index == len(approved_outline) - 1

    # --- Versioning: skip unchanged sections ---
    section_id = section.get("section_id", f"sec_{current_index + 1}")
    changed_section_ids = state.get("changed_section_ids", [])
    written_sections = state.get("written_sections", [])

    # Check if this section already has content and does NOT need rewriting
    existing_written = {s.get("section_id"): s for s in written_sections}

    if section_id in existing_written and section_id not in changed_section_ids:
        # Section is UNCHANGED - reuse existing content, skip LLM call
        state["stream_updates"].append(
            f"[{timestamp}] ♻️ Synthesis Agent → Reusing section {current_index + 1}/{len(approved_outline)}: "
            f"'{section_title}' (unchanged - skipping LLM call)"
        )
        # It is already in written_sections - just increment the index and continue
        state["current_section_index"] = current_index + 1

        # Mark synthesis as complete only when all sections are done
        if current_index + 1 >= len(approved_outline):
            state["completed_agents"].append("synthesis")
            state["stream_updates"].append(
                f"[{timestamp}] Synthesis Agent → Complete: all {len(approved_outline)} sections written"
            )

        return state
    # --- End versioning skip ---

    state["stream_updates"].append(
        f"[{timestamp}] ✍️ Synthesis Agent → Writing section {current_index + 1}/{len(approved_outline)}: {section_title}"
    )
    
    try:
        # Write the section
        written_section = write_section(
            section, research_results, fact_check_results, document_summary, is_last
        )
        
        # Compute confidence for this section
        confidence = compute_section_confidence(section, fact_check_results)
        state["confidence_scores"][section.get("section_id", f"sec_{current_index + 1}")] = confidence
        
        # Add to written sections
        written_sections = state.get("written_sections", [])
        written_sections.append(written_section)
        state["written_sections"] = written_sections
        
        # Increment index
        state["current_section_index"] = current_index + 1
        
        # Update overall confidence
        if state["confidence_scores"]:
            state["overall_confidence"] = sum(state["confidence_scores"].values()) / len(state["confidence_scores"])
        
        # Mark synthesis as complete only when all sections are done
        if current_index + 1 >= len(approved_outline):
            state["completed_agents"].append("synthesis")
            final_msg = f"[{timestamp}] Synthesis Agent → Complete: all {len(approved_outline)} sections written"
        else:
            final_msg = f"[{timestamp}] Synthesis Agent → Section {current_index + 1} complete ({written_section['word_count']} words, {confidence:.0%} confidence)"
        
        state["stream_updates"].append(final_msg)
        logger.info(final_msg)
        
        return state
        
    except Exception as e:
        error_msg = (
            f"[{timestamp}] ✗ Synthesis Agent → Section "
            f"{current_index + 1}/{len(approved_outline)} failed: {str(e)}"
        )
        state["stream_updates"].append(error_msg)
        logger.error(error_msg)

        # Append error placeholder so the supervisor sees progress and
        # advances past this section instead of retrying it indefinitely.
        # Do NOT set state["error"] here — that signals terminal failure
        # to the resume loop and would break the session immediately.
        error_section = {
            "section_id": section.get("section_id", f"sec_{current_index + 1}"),
            "title": section.get("title", f"Section {current_index + 1}"),
            "content": (
                f"[This section could not be generated — "
                f"error: {str(e)[:200]}]"
            ),
            "word_count": 0,
            "sources_used": [],
        }
        written_sections = state.get("written_sections", [])
        written_sections.append(error_section)
        state["written_sections"] = written_sections
        state["current_section_index"] = current_index + 1

        if current_index + 1 >= len(approved_outline):
            state["completed_agents"].append("synthesis")
            state["stream_updates"].append(
                f"[{timestamp}] Synthesis Agent → Complete with errors: "
                f"all {len(approved_outline)} sections processed"
            )

        return state
