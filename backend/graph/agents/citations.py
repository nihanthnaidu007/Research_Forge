"""
CitationAgent - Deduplicates and formats all references
"""
# No LLM calls in this agent — citation building is deterministic.
# Retry wrapper is not needed here.
import os
import re
import logging
from datetime import datetime
from urllib.parse import urlparse
from typing import List
from dotenv import load_dotenv
from langsmith import traceable

load_dotenv()
logger = logging.getLogger(__name__)


def extract_domain(url: str) -> str:
    """Extract domain from URL"""
    try:
        parsed = urlparse(url)
        return parsed.netloc.replace("www.", "")
    except:
        return "unknown"


def build_citation_list(written_sections: List[dict], research_results: List[dict]) -> List[dict]:
    """
    Collect ALL source URLs referenced across all written_sections,
    deduplicate, and assign citation numbers.
    If no inline citations found, use research results directly.
    """
    # Build URL to title mapping from research results
    url_to_title = {}
    for r in research_results:
        url = r.get("url", "")
        if url:
            url_to_title[url] = r.get("title", "Unknown Source")
    
    # Preserve order of first appearance — do not sort alphabetically
    seen_urls_ordered = []
    seen_urls_set = set()

    # First pass: collect from written sections in order (first appearance wins)
    for section in written_sections:
        content = section.get("content", "")
        url_pattern = r'\[source:\s*(https?://[^\]]+)\]'
        matches = re.findall(url_pattern, content)
        for url in matches:
            if url and url not in seen_urls_set:
                seen_urls_set.add(url)
                seen_urls_ordered.append(url)
        
        for url in section.get("sources_used", []):
            if url and url not in seen_urls_set:
                seen_urls_set.add(url)
                seen_urls_ordered.append(url)

    # Second pass: add any research result URLs not yet captured
    if not seen_urls_ordered and research_results:
        for r in research_results[:8]:
            url = r.get("url", "")
            if url and url not in seen_urls_set:
                seen_urls_set.add(url)
                seen_urls_ordered.append(url)

    # Build citation list in first-appearance order
    citations = []
    citation_number = 1
    for url in seen_urls_ordered:
        citations.append({
            "url": url,
            "title": url_to_title.get(url, "External Source"),
            "domain": extract_domain(url),
            "citation_number": citation_number
        })
        citation_number += 1
    
    return citations


def replace_inline_citations(written_sections: List[dict], sources: List[dict]) -> List[dict]:
    """
    Replace [source: url] with [n] citation numbers in section content.
    """
    # Build URL to citation number mapping
    url_to_number = {s.get("url", ""): s.get("citation_number", 0) for s in sources}
    
    updated_sections = []
    for section in written_sections:
        content = section.get("content", "")
        
        # Replace [source: url] with [n]
        def replace_citation(match):
            url = match.group(1).strip()
            number = url_to_number.get(url, 0)
            if number:
                return f"[{number}]"
            return match.group(0)  # Keep original if not found
        
        url_pattern = r'\[source:\s*(https?://[^\]]+)\]'
        updated_content = re.sub(url_pattern, replace_citation, content)
        
        updated_section = section.copy()
        updated_section["content"] = updated_content
        updated_sections.append(updated_section)
    
    return updated_sections


@traceable(name="citations-agent", run_type="chain")
def citations_node(state: dict) -> dict:
    """
    LangGraph node for CitationAgent.
    Builds final citation list and updates inline references.
    """
    timestamp = datetime.now().strftime("%H:%M:%S")
    written_sections = state.get("written_sections", [])
    research_results = state.get("research_results", [])
    
    state["stream_updates"].append(f"[{timestamp}] Citations Agent → Building citation list...")
    
    try:
        # Build citation list
        sources = build_citation_list(written_sections, research_results)
        state["sources"] = sources
        
        # Replace inline citations
        updated_sections = replace_inline_citations(written_sections, sources)
        state["written_sections"] = updated_sections
        
        # Compute definitive overall confidence from all section scores
        confidence_scores = state.get("confidence_scores", {})
        if confidence_scores:
            state["overall_confidence"] = round(
                sum(confidence_scores.values()) / len(confidence_scores), 4
            )
        else:
            state["overall_confidence"] = 0.0
        
        # Mark as complete
        state["completed_agents"].append("citations")
        state["is_complete"] = True
        
        final_msg = f"[{timestamp}] Citations Agent → Complete: {len(sources)} unique sources cited"
        state["stream_updates"].append(final_msg)
        logger.info(final_msg)
        
        return state
        
    except Exception as e:
        error_msg = f"[{timestamp}] Citations Agent → Error: {str(e)}"
        state["stream_updates"].append(error_msg)
        state["error"] = str(e)
        logger.error(error_msg)
        return state
