"""
OutlineAgent - Generates structured report outline based on research
"""
import os
import json
import logging
from datetime import datetime
from typing import List
from utils.clients import get_openai_client
from utils.llm_utils import call_with_retry
from graph.state import ReportState
from dotenv import load_dotenv
from langsmith import traceable

load_dotenv()
logger = logging.getLogger(__name__)

# Initialize OpenAI client
MODEL = "gpt-4o"


OUTLINE_SYSTEM_PROMPT = """You are a research report architect. Given a topic and fact-checked research findings, generate a structured report outline.

Rules:
- Each section must have a clear, specific title (not generic like "Introduction")
- Each description must guide the writer on exactly what to cover in that section
- Sections must flow logically: background, current state, analysis, implications, conclusion
- Ground section topics in the actual research findings provided
- Do not use em dashes in any section title or description
- Section titles must be specific and descriptive - maximum 8 words
- Section descriptions must be 1-2 sentences explaining exactly what to cover
- Return ONLY a JSON object with a single key "sections" containing the array, no markdown, no backticks, no explanation:

{
  "sections": [
    {
      "section_id": "sec_1",
      "title": "Section Title Here",
      "description": "2-3 sentences describing what this section should cover",
      "order": 1
    },
    {
      "section_id": "sec_2",
      "title": "Another Section Title",
      "description": "2-3 sentences describing what this section should cover",
      "order": 2
    }
  ]
}"""


@traceable(name="generate-outline", run_type="llm")
def generate_outline(topic: str, depth: str, fact_check_results: List[dict], 
                     research_results: List[dict], document_summary: str = "") -> List[dict]:
    """
    Generate a report outline using LLM based on research findings.
    depth="quick" → 3 sections, depth="deep" → 6 sections
    """
    num_sections = 6 if depth == "deep" else 3
    
    # Build context from research and fact-checks
    research_context = "\n".join(
        f"- {r.get('title', 'Unknown')}: {r.get('snippet', '')[:200]}"
        for r in research_results[:8]
    )
    
    factcheck_context = "\n".join(
        f"- [{r.get('verdict', 'UNKNOWN')}] {r.get('claim', '')[:150]}"
        for r in fact_check_results[:6]
    )
    
    doc_context = f"\nDocument insights: {document_summary[:500]}" if document_summary else ""
    
    user_prompt = f"""Topic: {topic}

Number of sections to generate: {num_sections}

Research findings:
{research_context}

Fact-check results:
{factcheck_context}
{doc_context}

Generate a {num_sections}-section report outline that covers this topic comprehensively. Return only JSON array."""

    try:
        response = call_with_retry(
            lambda: get_openai_client().chat.completions.create(
                model=MODEL,
                temperature=0.4,
                max_completion_tokens=1000,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": OUTLINE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ]
            ),
            label="outline generate_outline",
        )

        content = response.choices[0].message.content.strip()
        data = json.loads(content)
        raw_outline = data.get("sections", [])
        if not isinstance(raw_outline, list):
            raw_outline = []

        # Validate and ensure structure
        validated_outline = []
        for i, section in enumerate(raw_outline[:num_sections]):
            validated_outline.append({
                "section_id": section.get("section_id", f"sec_{i+1}"),
                "title": section.get("title", f"Section {i+1}"),
                "description": section.get("description", "Cover key findings and insights"),
                "order": section.get("order", i + 1)
            })
        
        return validated_outline
        
    except Exception as e:
        logger.error(f"Outline generation error: {str(e)}")
        # Return fallback outline
        sections = ["Background & Context", "Current State Analysis", "Key Findings"]
        if depth == "deep":
            sections.extend(["Comparative Analysis", "Implications", "Conclusions"])
        
        return [
            {
                "section_id": f"sec_{i+1}",
                "title": title,
                "description": f"Cover {title.lower()} related to {topic}",
                "order": i + 1
            }
            for i, title in enumerate(sections)
        ]


@traceable(name="outline-agent", run_type="chain")
def outline_node(state: ReportState) -> ReportState:
    """
    LangGraph node for OutlineAgent.
    Generates a structured report outline.
    """
    timestamp = datetime.now().strftime("%H:%M:%S")
    topic = state.get("topic", "")
    depth = state.get("depth", "quick")
    fact_check_results = state.get("fact_check_results", [])
    research_results = state.get("research_results", [])
    document_summary = state.get("document_summary", "")
    
    state["stream_updates"].append(f"[{timestamp}] Outline Agent → Generating {depth} report structure...")
    
    try:
        outline = generate_outline(
            topic, depth, fact_check_results, research_results, document_summary
        )
        
        state["outline"] = outline
        state["original_outline"] = outline.copy()  # Snapshot for versioning
        state["outline_approved"] = False  # Always reset - do not rely on initial state default
        
        # Mark as complete
        state["completed_agents"].append("outline")
        
        final_msg = f"[{timestamp}] Outline Agent → Complete: generated {len(outline)}-section outline"
        state["stream_updates"].append(final_msg)
        logger.info(final_msg)
        
        return state
        
    except Exception as e:
        error_msg = f"[{timestamp}] Outline Agent → Error: {str(e)}"
        state["stream_updates"].append(error_msg)
        state["error"] = str(e)
        logger.error(error_msg)
        return state
