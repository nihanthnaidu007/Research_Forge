"""
FactCheckAgent - LLM-as-judge for verifying claims against sources
"""
import os
import json
import logging
from datetime import datetime
from typing import List
from utils.clients import get_openai_client
from dotenv import load_dotenv
from langsmith import traceable

load_dotenv()
logger = logging.getLogger(__name__)

# Initialize OpenAI client
MODEL = "gpt-4o"

# Verdict scoring
VERDICT_SCORES = {
    "SUPPORTED": 1.0,
    "PARTIALLY_SUPPORTED": 0.6,
    "UNSUPPORTED": 0.2
}


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
        response = get_openai_client().chat.completions.create(
            model=MODEL,
            temperature=0.2,
            max_completion_tokens=800,
            messages=[
                {
                    "role": "system",
                    "content": """You are a claim extraction specialist. Extract 5-8 key factual claims from the provided source snippets that can be verified. 
                    
Focus on:
- Statistical claims and numbers
- Statements about trends or changes
- Claims about relationships between factors
- Factual assertions that could be true or false

Return ONLY a JSON array of claim strings, no markdown, no explanation:
["claim 1", "claim 2", "claim 3", ...]"""
                },
                {
                    "role": "user",
                    "content": f"Extract key factual claims from these sources:\n\n{combined_snippets}"
                }
            ]
        )
        
        content = response.choices[0].message.content.strip()
        # Clean up potential markdown
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        
        claims = json.loads(content)
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


@traceable(name="judge-claim", run_type="llm")
def judge_single_claim(claim: str, sources: List[dict]) -> dict:
    """
    LLM judges whether a claim is supported by the retrieved sources.
    Returns a FactCheckResult dict.
    """
    # Build sources text
    sources_text = "\n\n".join(
        f"[{s.get('source_domain', 'unknown')}] ({s.get('url', '')}): {s.get('snippet', '')[:400]}"
        for s in sources[:8]
    )
    
    prompt = f"""You are a fact-checking AI. Given a claim and source excerpts, assess whether the sources support the claim.

Claim: {claim}

Source excerpts:
{sources_text}

Respond ONLY with JSON, no markdown, no backticks:
{{
    "verdict": "SUPPORTED" or "PARTIALLY_SUPPORTED" or "UNSUPPORTED",
    "confidence": 0.0 to 1.0,
    "reasoning": "one sentence explanation",
    "supporting_urls": ["url1", "url2"]
}}"""

    try:
        response = get_openai_client().chat.completions.create(
            model=MODEL,
            temperature=0.1,
            max_completion_tokens=300,
            messages=[
                {"role": "system", "content": "You are a precise fact-checker. Return only valid JSON."},
                {"role": "user", "content": prompt}
            ]
        )
        
        content = response.choices[0].message.content.strip()
        # Clean up potential markdown
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
        
        result = json.loads(content)
        
        return {
            "claim": claim,
            "verdict": result.get("verdict", "PARTIALLY_SUPPORTED"),
            "confidence": float(result.get("confidence", 0.5)),
            "reasoning": result.get("reasoning", "Unable to determine"),
            "supporting_urls": result.get("supporting_urls", [])
        }
        
    except Exception as e:
        logger.error(f"Fact-check error for claim '{claim[:50]}': {str(e)}")
        # Fallback: return low-confidence unsupported result to avoid silently inflating scores
        return {
            "claim": claim,
            "verdict": "UNSUPPORTED",
            "confidence": 0.2,
            "reasoning": f"Could not verify — API error: {str(e)[:100]}",
            "supporting_urls": []
        }


# NOTE: This sequential factcheck_node is kept as a fallback.
# The production path now uses factcheck_fanout → factcheck_single (parallel via Send() API)
# → factcheck_merge. The supervisor routes to "factcheck_fanout" not "factcheck".
# This function is no longer called in the main graph flow.
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
