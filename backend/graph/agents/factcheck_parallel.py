"""
FactCheck Parallel State and Node
Used by the Send() API to process individual claims in parallel.
"""
import os
import json
import logging
from typing import TypedDict, List
from utils.clients import get_openai_client
from utils.llm_utils import call_with_retry
from dotenv import load_dotenv
from langsmith import traceable

load_dotenv()
logger = logging.getLogger(__name__)

MODEL = "gpt-4o"

VERDICT_SCORES = {
    "SUPPORTED": 1.0,
    "PARTIALLY_SUPPORTED": 0.6,
    "UNSUPPORTED": 0.2
}


class SingleClaimState(TypedDict):
    """State for processing a single claim in parallel"""
    claim: str
    sources: List[dict]
    fact_check_result: dict  # populated after judging


@traceable(name="judge-claim-parallel", run_type="llm")
def judge_single_claim_parallel(claim: str, sources: List[dict]) -> dict:
    """
    LLM judges whether a claim is supported by the retrieved sources.
    This is the same logic as the sequential version but designed for parallel execution.
    """
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
        response = call_with_retry(
            lambda: get_openai_client().chat.completions.create(
                model=MODEL,
                temperature=0.1,
                max_completion_tokens=300,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": "You are a precise fact-checker. Return only valid JSON."},
                    {"role": "user", "content": prompt}
                ]
            ),
            label="factcheck_parallel judge_single_claim",
        )

        content = response.choices[0].message.content.strip()
        result = json.loads(content)

        return {
            "claim": claim,
            "verdict": result.get("verdict", "PARTIALLY_SUPPORTED"),
            "confidence": float(result.get("confidence", 0.5)),
            "reasoning": result.get("reasoning", "Unable to determine"),
            "supporting_urls": result.get("supporting_urls", [])
        }

    except Exception as e:
        logger.error(f"Parallel fact-check error for claim '{claim[:50]}': {str(e)}")
        return {
            "claim": claim,
            "verdict": "UNSUPPORTED",
            "confidence": 0.2,
            "reasoning": f"Could not verify — API error: {str(e)[:100]}",
            "supporting_urls": []
        }


@traceable(name="factcheck-single-claim-node", run_type="chain")
def factcheck_single_node(state: dict) -> dict:
    """
    LangGraph node that processes exactly one claim.
    Invoked in parallel by the Send() API fan-out.
    
    Receives: {claim, sources} via Send()
    Returns: partial ReportState update with result in parallel_fact_check_results
    """
    result = judge_single_claim_parallel(state["claim"], state["sources"])
    # Return a partial state update — the operator.add reducer on
    # parallel_fact_check_results accumulates results from all parallel nodes
    return {"parallel_fact_check_results": [result]}
