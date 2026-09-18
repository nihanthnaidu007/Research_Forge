r"""
Report templates — named presets for the outline prompt seam.

A template parameterizes ONLY the outline system prompt: named rule
blocks appended to the base OUTLINE_SYSTEM_PROMPT. Two couplings are
deliberately preserved (spec: two couplings must not drift per preset):

1. num_sections keys on depth alone (outline.generate_outline) — no
   preset changes the section count or the truncation cap.
2. synthesis is fixed (SYNTHESIS_SYSTEM_PROMPT's 180-250 words/section)
   — no preset promises longer or shorter sections. Templates shape
   outline STRUCTURE, not section length; the picker copy says so.

Wording discipline for "systematic-lite": the preset describes what it
does (structured, evidence-linked sections) and never borrows
systematic-review vocabulary — no screening, no inclusion/exclusion
criteria, no PRISMA claims. Guarded by test.
"""

from typing import TypedDict


class TemplatePreset(TypedDict):
    label: str
    description: str
    rules: str


_STANDARD_LABEL = "Standard"
_STANDARD_DESCRIPTION = "The default report shape: background, current state, analysis, implications, conclusion."

EXEC_BRIEF: TemplatePreset = {
    "label": "Executive Brief",
    "description": "Decision-first brief: findings, implications, recommended actions. Same section count and length as standard.",
    "rules": (
        "Template: executive brief\n"
        "- Order sections so decision-relevant findings come before context\n"
        "- Each section description must state the implication for a decision maker\n"
        "- The final section must present recommended actions, not a generic conclusion"
    ),
}

JOURNAL_CLUB: TemplatePreset = {
    "label": "Journal Club",
    "description": "Seminar-style discussion: approach, evidence quality, relation to prior work. Same section count and length as standard.",
    "rules": (
        "Template: journal club\n"
        "- Include a section examining the methodology of the researched work\n"
        "- Each section description must name the kind of evidence it will discuss\n"
        "- Include a section on how the findings relate to or contradict prior work\n"
        "- Frame one section as open questions worth discussing"
    ),
}

DEEP_DIVE: TemplatePreset = {
    "label": "Deep Dive",
    "description": "Mechanism-oriented: how it works, why it works, failure modes, open questions. Same section count and length as standard.",
    "rules": (
        "Template: deep dive\n"
        "- Prefer narrower, mechanism-focused sections over broad surveys\n"
        "- At least one section must examine failure modes or edge cases\n"
        "- Each section description must point at the concrete mechanism it explains\n"
        "- Reserve the final section for open questions, not summary"
    ),
}

SYSTEMATIC_LITE: TemplatePreset = {
    "label": "Systematic Lite",
    "description": "Structured, evidence-linked organization: one theme per section with explicit limitations. Not a formal systematic review.",
    "rules": (
        "Template: systematic lite\n"
        "- One theme per section; no section may straddle two themes\n"
        "- Every section description must name the evidence it will present\n"
        "- Include a final section stating the limitations of the evidence gathered\n"
        "- This is a structured summary, not a formal systematic review"
    ),
}

PRESETS: dict[str, TemplatePreset] = {
    "standard": {
        "label": _STANDARD_LABEL,
        "description": _STANDARD_DESCRIPTION,
        "rules": "",
    },
    "exec-brief": EXEC_BRIEF,
    "journal-club": JOURNAL_CLUB,
    "deep-dive": DEEP_DIVE,
    "systematic-lite": SYSTEMATIC_LITE,
}

DEFAULT_TEMPLATE = "standard"

# The server validates the run-request field against exactly these keys
# (pattern built from the registry — single source of truth).
TEMPLATE_PATTERN = rf"^({'|'.join(sorted(PRESETS))})$"


def template_rules(template: str) -> str:
    """The appended rules block for a template; unknown/None falls back to standard."""
    return PRESETS.get(template, PRESETS[DEFAULT_TEMPLATE])["rules"]


def build_outline_messages(
    topic: str,
    num_sections: int,
    template: str,
    research_results: list[dict],
    fact_check_results: list[dict],
    document_summary: str = "",
) -> list[dict]:
    """
    Assemble the outline LLM messages for a topic under a template preset.

    Pure function — the testable seam for prompt-shape assertions per
    preset. The base system prompt (OUTLINE_SYSTEM_PROMPT) is imported and
    composed with the preset's rules; the user prompt mirrors the original
    inline assembly exactly (research findings, fact-check results,
    document summary). num_sections is passed in by the caller so the
    depth coupling lives in one place (generate_outline).
    """
    from graph.agents.outline import OUTLINE_SYSTEM_PROMPT

    preset_rules = template_rules(template)
    system_prompt = (
        f"{OUTLINE_SYSTEM_PROMPT}\n\n{preset_rules}" if preset_rules else OUTLINE_SYSTEM_PROMPT
    )

    research_context = "\n".join(
        f"- {r.get('title', 'Unknown')}: {r.get('snippet', '')[:200]}"
        for r in research_results[:8]
    )

    factcheck_context = "\n".join(
        f"- [{r.get('verdict', 'UNKNOWN')}] {r.get('claim', '')[:150]}"
        for r in fact_check_results[:6]
    )

    doc_context = (
        f"\nDocument insights: {document_summary[:500]}" if document_summary else ""
    )

    user_prompt = f"""Topic: {topic}

Number of sections to generate: {num_sections}

Research findings:
{research_context}

Fact-check results:
{factcheck_context}
{doc_context}

Generate a {num_sections}-section report outline that covers this topic comprehensively. Return only JSON array."""

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
