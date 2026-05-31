"""
ResearchForge Report Versioning
Diffs original vs edited outline to identify which sections need re-synthesis.
Only changed sections are rewritten — unchanged sections reuse existing content.
"""

import logging

logger = logging.getLogger(__name__)


def normalize(text: str) -> str:
    """Normalize text for comparison — strip whitespace, lowercase"""
    return text.strip().lower()


def diff_outlines(original: list[dict], approved: list[dict]) -> list[str]:
    """
    Compare original outline (before user edits) vs approved outline (after edits).

    A section is considered CHANGED if:
    - Its title differs (case-insensitive, stripped)
    - Its description differs (case-insensitive, stripped)
    - It is a new section (section_id not in original)

    A section is considered UNCHANGED if both title and description are identical.

    Returns:
        List of section_ids that changed and need re-synthesis
    """
    if not original:
        # No original snapshot — treat everything as changed
        logger.warning(
            "No original outline snapshot found — marking all sections as changed"
        )
        return [s.get("section_id", f"sec_{i}") for i, s in enumerate(approved)]

    # Build lookup map from original
    original_map: dict[str, dict] = {s.get("section_id", ""): s for s in original}

    changed_ids = []

    for section in approved:
        section_id = section.get("section_id", "")
        orig = original_map.get(section_id)

        if orig is None:
            # New section added by user
            logger.info(f"Section {section_id} is NEW — marking for synthesis")
            changed_ids.append(section_id)
            continue

        title_changed = normalize(orig.get("title", "")) != normalize(
            section.get("title", "")
        )
        desc_changed = normalize(orig.get("description", "")) != normalize(
            section.get("description", "")
        )

        if title_changed or desc_changed:
            reasons = []
            if title_changed:
                reasons.append("title changed")
            if desc_changed:
                reasons.append("description changed")
            logger.info(
                f"Section {section_id} CHANGED ({', '.join(reasons)}) — marking for re-synthesis"
            )
            changed_ids.append(section_id)
        else:
            logger.info(f"Section {section_id} UNCHANGED — will reuse existing content")

    return changed_ids


def get_sections_needing_rewrite(
    changed_ids: list[str], written_sections: list[dict]
) -> list[str]:
    """
    From the list of changed section_ids, determine which ones actually need
    to be re-written (i.e., they have existing content that must be replaced).

    Sections that changed but have NO existing written content are just new sections
    that need to be written for the first time — handled normally by synthesis.

    Returns:
        List of section_ids that have existing content AND need to be replaced
    """
    written_ids = {s.get("section_id", "") for s in written_sections}
    needs_rewrite = [sid for sid in changed_ids if sid in written_ids]

    if needs_rewrite:
        logger.info(f"Sections needing content replacement: {needs_rewrite}")
    return needs_rewrite


def compute_section_diff(original_section: dict, approved_section: dict) -> dict:
    """
    Detailed diff between two versions of the same section.
    Returns a dict describing what changed for UI display.
    """
    result = {
        "section_id": approved_section.get("section_id", ""),
        "changed": False,
        "title_changed": False,
        "description_changed": False,
        "original_title": original_section.get("title", ""),
        "new_title": approved_section.get("title", ""),
        "original_description": original_section.get("description", ""),
        "new_description": approved_section.get("description", ""),
    }

    if normalize(original_section.get("title", "")) != normalize(
        approved_section.get("title", "")
    ):
        result["title_changed"] = True
        result["changed"] = True

    if normalize(original_section.get("description", "")) != normalize(
        approved_section.get("description", "")
    ):
        result["description_changed"] = True
        result["changed"] = True

    return result


def build_versioning_report(
    original: list[dict], approved: list[dict], written_sections: list[dict]
) -> dict:
    """
    Build a complete versioning report for logging and UI display.

    Returns:
        dict with changed_ids, unchanged_ids, rewrite_ids, diff_details, summary
    """
    changed_ids = diff_outlines(original, approved)
    unchanged_ids = [
        s.get("section_id", "")
        for s in approved
        if s.get("section_id", "") not in changed_ids
    ]
    rewrite_ids = get_sections_needing_rewrite(changed_ids, written_sections)

    original_map = {s.get("section_id", ""): s for s in original}
    diff_details = [
        compute_section_diff(original_map.get(s.get("section_id", ""), {}), s)
        for s in approved
    ]

    total = len(approved)
    changed = len(changed_ids)
    unchanged = len(unchanged_ids)

    summary = (
        f"{unchanged} of {total} sections unchanged (reusing existing content) — "
        f"{changed} section(s) will be re-synthesized"
        if unchanged > 0
        else f"All {total} sections will be synthesized"
    )

    return {
        "changed_ids": changed_ids,
        "unchanged_ids": unchanged_ids,
        "rewrite_ids": rewrite_ids,
        "diff_details": diff_details,
        "summary": summary,
        "tokens_saved": unchanged > 0,
    }
