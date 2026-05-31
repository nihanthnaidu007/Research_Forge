"""
ResearchForge confidence scoring constants.
Single source of truth for verdict-to-score mapping used across
fact-checking and synthesis agents.
"""

# Maps LLM fact-check verdicts to numeric confidence scores.
# Used by factcheck agents to compute raw confidence values and
# by synthesis to weight section confidence scores.
VERDICT_SCORES: dict[str, float] = {
    "SUPPORTED": 1.0,
    "PARTIALLY_SUPPORTED": 0.6,
    "UNSUPPORTED": 0.2,
}
