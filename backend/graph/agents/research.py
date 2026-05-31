"""
WebResearchAgent - Performs live web research using Tavily API
"""
import os
import logging
from datetime import datetime
from urllib.parse import urlparse
from utils.clients import get_tavily_client
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


@traceable(name="tavily-web-search", run_type="tool")
def perform_tavily_search(query: str, max_results: int = 5, retries: int = 2, stream_updates=None, timestamp: str = None) -> list:
    """Execute a single Tavily search with retry logic"""
    import time

    tavily_client = get_tavily_client()
    if tavily_client is None:
        if stream_updates is not None:
            prefix = f"[{timestamp}] " if timestamp else ""
            stream_updates.append(f"{prefix}\u2717 Tavily client unavailable (missing TAVILY_API_KEY).")
        return []

    for attempt in range(retries + 1):
        try:
            results = get_tavily_client().search(
                query=query,
                search_depth="advanced",
                max_results=max_results,
                include_raw_content=False
            )
            if stream_updates is not None:
                prefix = f"[{timestamp}] " if timestamp else ""
                stream_updates.append(
                    f"{prefix}✓ Tavily returned {len(results.get('results', []))} raw results"
                )

            formatted = []
            for item in results.get("results", []):
                url = item.get("url", "")
                if not url:
                    continue
                formatted.append({
                    "url": url,
                    "title": item.get("title", "Untitled"),
                    "snippet": item.get("content", "")[:500],
                    "source_domain": extract_domain(url),
                    "relevance_score": min(float(item.get("score", 0.8)), 1.0)
                })
            return formatted

        except Exception as e:
            if attempt < retries:
                wait = 2 ** attempt  # 1s, 2s backoff
                logger.warning(f"Tavily search attempt {attempt + 1} failed for '{query[:40]}': {str(e)}. Retrying in {wait}s...")
                if stream_updates is not None:
                    prefix = f"[{timestamp}] " if timestamp else ""
                    stream_updates.append(
                        f"{prefix}\u2717 Tavily search attempt {attempt + 1} failed: {str(e)}"
                    )
                # time.sleep is correct here \u2014 this function runs inside
                # asyncio.to_thread(), not on the event loop. Using asyncio.sleep
                # here would raise RuntimeError (no running event loop in this thread).
                time.sleep(wait)
            else:
                logger.error(f"Tavily search failed after {retries + 1} attempts for '{query[:40]}': {str(e)}")
                if stream_updates is not None:
                    prefix = f"[{timestamp}] " if timestamp else ""
                    stream_updates.append(
                        f"{prefix}\u2717 Tavily search failed after {retries + 1} attempts: {str(e)}"
                    )
                return []
    return []


@traceable(name="research-agent", run_type="chain")
def research_node(state: dict) -> dict:
    """
    LangGraph node for WebResearchAgent.
    Performs multiple Tavily searches and aggregates results.
    """
    timestamp = datetime.now().strftime("%H:%M:%S")
    topic = state.get("topic", "")
    
    state["stream_updates"].append(f"[{timestamp}] Research Agent → Starting web research for: {topic}")

    # Debug signal: confirm whether this backend process has a Tavily API key.
    # (We don't print the key value to avoid leaking secrets.)
    key_present = bool(os.getenv("TAVILY_API_KEY"))
    state["stream_updates"].append(
        f"[{timestamp}] Research Agent → Tavily API key present: {'yes' if key_present else 'no'}"
    )
    
    try:
        # Define search queries
        queries = [
            f"{topic} latest research and developments 2025 2026",
            f"{topic} expert analysis and insights",
            f"{topic} key findings studies and data"
        ]
        
        all_results = []
        seen_urls = set()
        
        for query in queries:
            state["stream_updates"].append(f"[{timestamp}] Research Agent → Searching: {query[:50]}...")
            results = perform_tavily_search(
                query,
                stream_updates=state["stream_updates"],
                timestamp=timestamp
            )
            
            # Deduplicate by URL
            for result in results:
                if result["url"] not in seen_urls:
                    seen_urls.add(result["url"])
                    all_results.append(result)
        
        # Sort by relevance score
        all_results.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
        
        if not all_results:
            error_msg = f"[{timestamp}] Research Agent \u2192 Warning: No results found for topic '{topic}'. Check Tavily API key and internet connection."
            state["stream_updates"].append(error_msg)
            state["error"] = "Research returned no results. The report may be incomplete."
            logger.warning(error_msg)
            # Continue anyway \u2014 let downstream agents handle empty research gracefully

        # Increment retry counter so supervisor can detect repeated failures
        state["retry_count"] = state.get("retry_count", 0) + 1

        # Limit to top 15 results
        state["research_results"] = all_results[:15]
        
        if not all_results:
            state["error"] = (
                "Research returned no results after searching Tavily. "
                "Possible causes: invalid TAVILY_API_KEY, network connectivity issue, or proxy blocking outbound requests. "
                "Check backend/.env and network settings."
            )
            state["stream_updates"].append(
                f"[{timestamp}] \u2717 Research Agent \u2192 No results found. Check TAVILY_API_KEY and network. "
                f"Attempt {state['retry_count']} of 3."
            )
        
        # Update status
        state["completed_agents"].append("research")
        final_msg = f"[{timestamp}] Research Agent → Complete: found {len(all_results)} unique sources across {len(queries)} queries"
        state["stream_updates"].append(final_msg)
        logger.info(final_msg)
        
        return state
        
    except Exception as e:
        error_msg = f"[{timestamp}] Research Agent → Error: {str(e)}"
        state["stream_updates"].append(error_msg)
        state["error"] = str(e)
        logger.error(error_msg)
        return state
