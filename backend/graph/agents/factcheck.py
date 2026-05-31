"""
FactCheckAgent - LLM-as-judge for verifying claims against sources
"""
import os
import json
import logging
from datetime import datetime
from typing import List
from utils.clients import get_openai_client
from utils.llm_utils import call_with_retry
from utils.scoring import VERDICT_SCORES
from graph.agents.factcheck_parallel import judge_single_claim_parallel
from dotenv import load_dotenv
from langsmith import traceable

load_dotenv()
logger = logging.getLogger(__name__)

# Initialize OpenAI client
MODEL = "gpt-4o"


@traceable(name="extract-claims", run_type="llm")
def extract_claims_from_research(research_results: List[dict]) -> List[str]:
    """Extract key factual claims from research snippets for fact-checking."""
    if not research_results:
        return []
    
    # Combine snippets
    combined_snippets = "\n\n".join(
        f"Source ({r.get('source_domain', 'unknown')}): {r.get('snippet', '')}"
        for r in research_results[:10]
    )[:4000]
    
    try:
        response = call_with_retry(
            lambda: get_openai_client().chat.completions.create(
                model=MODEL,
                temperature=0.2,
                max_completion_tokens=800,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": """You are a claim extraction specialist. Extract 5-8 key factual claims from the provided source snippets that can be verified.

Focus on:
- Statistical claims and numbers
- Statements about trends or changes
- Claims about relationships between factors
- Factual assertions that could be true or false

Return ONLY a JSON object with a single key "claims" containing an array of claim strings, no markdown, no explanation:
{"claims": ["claim 1", "claim 2", ...]}"""
                    },
                    {
                        "role": "user",
                        "content": f"Extract key factual claims from these sources:\n\n{combined_snippets}"
                    }
                ]
            ),
            label="factcheck extract_claims",
        )

        content = response.choices[0].message.content.strip()
        data = json.loads(content)
        claims = data.get("claims", [])
        if not isinstance(claims, list):
            claims = []
        return claims[:8]  # Limit to 8 claims
        
    except Exception as e:
        logger.error(f"Claim extraction error: {str(e)}")
        # Fallback: extract simple claims from snippets
        fallback_claims = []
        for r in research_results[:5]:
            snippet = r.get("snippet", "")
            if len(snippet) > 50:
                fallback_claims.append(snippet[:200] + "...")
        return fallback_claims[:5]


def judge_single_claim(claim: str, sources: List[dict]) -> dict:
    """
    Delegates to judge_single_claim_parallel — the canonical implementation.
    This function is kept for import compatibility only. The dead sequential
    factcheck_node was the only caller. Do not add new callers here.
    """
    return judge_single_claim_parallel(claim, sources)


# DEAD CODE: This sequential factcheck_node is NOT wired into the graph.
# The production path is: factcheck_fanout → factcheck_single (parallel
# via Send() API) → factcheck_merge.
# This function is kept only to avoid breaking any external imports.
# Do not wire this node into the graph — use factcheck_fanout instead.
@traceable(name="factcheck-agent", run_type="chain")
def factcheck_node(state: dict) -> dict:
    """
    LangGraph node for FactCheckAgent (SEQUENTIAL FALLBACK).
    Extracts claims and verifies them against sources.
    """
    timestamp = datetime.now().strftime("%H:%M:%S")
    research_results = state.get("research_results", [])
    
    state["stream_updates"].append(f"[{timestamp}] FactCheck Agent → Extracting claims from research...")
    
    try:
        # Extract claims
        claims = extract_claims_from_research(research_results)
        state["stream_updates"].append(f"[{timestamp}] FactCheck Agent → Found {len(claims)} claims to verify")
        
        # Check each claim (sequential for now - CURSOR_TODO: parallelize with Send())
        results = []
        for i, claim in enumerate(claims):
            state["stream_updates"].append(f"[{timestamp}] FactCheck Agent → Verifying claim {i+1}/{len(claims)}...")
            result = judge_single_claim(claim, research_results)
            results.append(result)
        
        state["fact_check_results"] = results
        
        # Compute summary statistics
        verdicts = {"SUPPORTED": 0, "PARTIALLY_SUPPORTED": 0, "UNSUPPORTED": 0}
        for r in results:
            verdict = r.get("verdict", "PARTIALLY_SUPPORTED")
            verdicts[verdict] = verdicts.get(verdict, 0) + 1
        
        # Add verdict summary to trace
        supported = sum(1 for r in results if r.get("verdict") == "SUPPORTED")
        partial = sum(1 for r in results if r.get("verdict") == "PARTIALLY_SUPPORTED")
        unsupported = sum(1 for r in results if r.get("verdict") == "UNSUPPORTED")
        avg_confidence = round(sum(r.get("confidence", 0) for r in results) / max(len(results), 1), 2)

        state["stream_updates"].append(
            f"[{timestamp}] FactCheck Agent → Results: {supported} SUPPORTED / "
            f"{partial} PARTIAL / {unsupported} UNSUPPORTED | Avg confidence: {avg_confidence}"
        )
        
        # Mark as complete
        state["completed_agents"].append("factcheck")
        
        final_msg = (
            f"[{timestamp}] FactCheck Agent → Complete: {len(results)} claims assessed - "
            f"{verdicts['SUPPORTED']} SUPPORTED, {verdicts['PARTIALLY_SUPPORTED']} PARTIAL, "
            f"{verdicts['UNSUPPORTED']} UNSUPPORTED"
        )
        state["stream_updates"].append(final_msg)
        logger.info(final_msg)
        
        return state
        
    except Exception as e:
        error_msg = f"[{timestamp}] FactCheck Agent → Error: {str(e)}"
        state["stream_updates"].append(error_msg)
        state["error"] = str(e)
        logger.error(error_msg)
        return state
