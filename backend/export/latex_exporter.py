r"""
ResearchForge LaTeX Exporter

Generates a self-contained, compilable .tex document from completed report
state: the full report body plus a thebibliography block whose keys match
the \cite commands emitted for inline [n] markers.

Design contracts (mirroring the BibTeX exporter):
- Deterministic given the same report state (no timestamps), so golden-file
  tests can assert the exact text.
- Nothing is invented: fields the retrieval source did not return are
  simply omitted.
- Single-file deliverable: the bibliography is embedded via thebibliography
  (no companion .bib), so the download compiles with plain pdflatex.

Escaping policy:
- Prose and metadata: every LaTeX special character (\ & % $ # _ { } ~ ^)
  is escaped before interpolation — model-written prose must never open
  math mode or break the document.
- URLs: interpolated raw inside \url{}, which renders them verbatim.
- Inline citation markers: [n] with a resolvable source number becomes
  \cite{key}; everything else (including [source: url] markers that the
  citations agent passes through verbatim) stays literal, escaped text.
- Unicode: UTF-8 pass-through with a small typographic map (dashes, curly
  quotes, ellipsis); the document declares inputenc utf8 so pdflatex
  compiles it, and xelatex/lualatex work equally.
"""

import re

from export.bibtex_exporter import citation_key
from export.markdown_exporter import extract_report_parts

# LaTeX special characters that must be escaped outside \url{} — the same
# ten-character set the BibTeX exporter maintains for its field values.
_LATEX_SPECIALS = str.maketrans(
    {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
)

# Common typographic characters model prose actually contains, mapped to
# their LaTeX forms AFTER the specials table runs — the replacements are
# LaTeX the specials table must not re-escape, and their input characters
# are not specials the table would have escaped.
_TYPOGRAPHIC = str.maketrans(
    {
        "\u2014": "---",  # em dash
        "\u2013": "--",  # en dash
        "\u2018": "`",  # left single quote
        "\u2019": "'",  # right single quote
        "\u201c": "``",  # left double quote
        "\u201d": "''",  # right double quote
        "\u2026": r"\ldots{}",  # ellipsis
    }
)

_CITATION_MARKER = re.compile(r"\[(\d+)\]")


def _escape(value: str) -> str:
    return value.translate(_LATEX_SPECIALS).translate(_TYPOGRAPHIC)


def _citation_keys(parts: dict) -> tuple[list[dict], dict[int, str]]:
    r"""
    Assign a stable thebibliography key per source and map resolvable
    inline citation numbers to those keys.

    Mirrors build_bibtex_report's key assignment: citation_key with letter
    suffixes on collisions. Sources without a citation_number still get a
    bibliography entry (order preserved); they simply cannot be cited by
    number from the prose.
    """
    ordered: list[dict] = []
    number_to_key: dict[int, str] = {}
    used_keys: set[str] = set()

    for position, source in enumerate(parts["sources"]):
        base_key = citation_key(source, position)
        key = base_key
        suffix_letter = "a"
        while key in used_keys:
            key = f"{base_key}{suffix_letter}"
            suffix_letter = chr(ord(suffix_letter) + 1)
        used_keys.add(key)

        entry = dict(source, bibliography_key=key)
        ordered.append(entry)

        number = source.get("citation_number")
        if isinstance(number, int):
            number_to_key.setdefault(number, key)

    return ordered, number_to_key


def _render_prose(content: str, number_to_key: dict[int, str]) -> str:
    r"""
    Escape one prose block and turn resolvable [n] markers into \cite{key}.

    Escaping runs first: brackets and digits are untouched by the escape
    tables, so the marker regex sees the same [n] tokens in the escaped
    text. Unresolvable numbers and non-numeric brackets (e.g. leftover
    [source: url] markers) stay as literal escaped text.
    """

    def _replace(match: re.Match[str]) -> str:
        key = number_to_key.get(int(match.group(1)))
        return rf"\cite{{{key}}}" if key else match.group(0)

    return _CITATION_MARKER.sub(_replace, _escape(content))


def _bibliography_line(entry: dict) -> str:
    r"""Render one \bibitem from what retrieval actually captured."""
    fragments = [entry["title"]]
    if entry.get("venue"):
        fragments.append(str(entry["venue"]))
    if entry.get("year"):
        fragments.append(str(entry["year"]))
    if entry.get("url"):
        fragments.append(rf"\url{{{entry['url']}}}")

    provenance = entry.get("source_api") or entry.get("domain")
    if provenance:
        fragments.append(f"(Source: {provenance})")

    return " ".join(fragments)


def build_latex_report(state: dict) -> str:
    r"""
    Render the completed report as a self-contained LaTeX document.

    Raises ValueError when the state has no written sections — the
    endpoint caller maps that to a 400 response, same as every exporter.
    """
    parts = extract_report_parts(state)
    sources, number_to_key = _citation_keys(parts)

    confidence_pct = round(parts["overall_confidence"] * 100)
    lines: list[str] = [
        r"\documentclass[11pt]{article}",
        r"\usepackage[utf8]{inputenc}",
        r"\usepackage[T1]{fontenc}",
        r"\usepackage{url}",
        r"\usepackage[margin=1in]{geometry}",
        r"\begin{document}",
        "",
        r"\begin{center}",
        rf"{{\LARGE\bfseries {_escape(parts['topic'])}}}\\[0.8em]",
        rf"{{\small depth: {_escape(parts['depth'])}"
        rf" \textperiodcentered\ overall confidence: {confidence_pct}\%}}",
        r"\end{center}",
        "",
    ]

    for section in parts["sections"]:
        lines.append(rf"\section*{{{section['number']}. {_escape(section['title'])}}}")
        lines.append("")
        for paragraph in section["content"].split("\n\n"):
            if paragraph.strip():
                lines.append(_render_prose(paragraph.strip(), number_to_key))
                lines.append("")
        if section["sources_used"]:
            lines.append(
                rf"\noindent\textit{{Sources: {_escape(', '.join(section['sources_used']))}}}"
            )
            lines.append("")

    lines.append(r"\begin{thebibliography}{9}")
    for entry in sources:
        lines.append(rf"\bibitem{{{entry['bibliography_key']}}} {_bibliography_line(entry)}")
    if not sources:
        lines.append("% No cited sources in this report.")
    lines.append(r"\end{thebibliography}")
    lines.append("")
    lines.append(r"\end{document}")

    return "\n".join(lines) + "\n"
