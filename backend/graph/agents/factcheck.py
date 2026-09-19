"""
FactCheckAgent - LLM-as-judge claim extraction for the parallel fact-check pipeline.

The verification step itself lives in factcheck_parallel.py: the graph fans
claims out with Send() and judges them concurrently (factcheck_single_node).
"""

import logging

from dotenv import load_dotenv
from langsmith import traceable

from utils.clients import chat_completion_with_usage, llm_model
from utils.llm_utils import call_with_retry, extract_json_object

load_dotenv()
logger = logging.getLogger(__name__)


@traceable(name="extract-claims", run_type="llm")
def extract_claims_from_research(research_results: list[dict]) -> list[str]:
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
            lambda: chat_completion_with_usage(
                model=llm_model(),
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
{"claims": ["claim 1", "claim 2", ...]}""",
                    },
                    {
                        "role": "user",
                        "content": f"Extract key factual claims from these sources:\n\n{combined_snippets}",
                    },
                ],
            ),
            label="factcheck extract_claims",
        )

        content = response.choices[0].message.content.strip()
        data = extract_json_object(content)
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

