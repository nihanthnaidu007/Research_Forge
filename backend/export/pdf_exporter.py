"""
ResearchForge PDF Exporter
Generates structured, styled PDF reports from completed report state.
"""
import os
import logging
from datetime import datetime
from typing import List, Optional

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak, KeepTogether
)

logger = logging.getLogger(__name__)


# ─── Color Palette ────────────────────────────────────────────────────────────

DARK_BG        = colors.HexColor("#0c0e17")
ACCENT         = colors.HexColor("#6366f1")
ACCENT_LIGHT   = colors.HexColor("#a5b4fc")
TEXT_PRIMARY   = colors.HexColor("#1a1a2e")
TEXT_SECONDARY = colors.HexColor("#4a5568")
TEXT_MUTED     = colors.HexColor("#718096")
GREEN          = colors.HexColor("#22c55e")
AMBER          = colors.HexColor("#f59e0b")
RED            = colors.HexColor("#ef4444")
BORDER         = colors.HexColor("#e2e8f0")
BG_LIGHT       = colors.HexColor("#f8fafc")
BG_SECTION     = colors.HexColor("#f1f5f9")
WHITE          = colors.white


# ─── Style Registry ───────────────────────────────────────────────────────────

def build_styles():
    styles = getSampleStyleSheet()

    styles.add(ParagraphStyle(
        name="CoverTitle",
        fontName="Helvetica-Bold",
        fontSize=28,
        leading=36,
        textColor=TEXT_PRIMARY,
        alignment=TA_LEFT,
        spaceAfter=8,
    ))

    styles.add(ParagraphStyle(
        name="CoverSubtitle",
        fontName="Helvetica",
        fontSize=13,
        leading=18,
        textColor=TEXT_SECONDARY,
        alignment=TA_LEFT,
        spaceAfter=4,
    ))

    styles.add(ParagraphStyle(
        name="CoverMeta",
        fontName="Helvetica",
        fontSize=10,
        leading=14,
        textColor=TEXT_MUTED,
        alignment=TA_LEFT,
    ))

    styles.add(ParagraphStyle(
        name="SectionHeading",
        fontName="Helvetica-Bold",
        fontSize=15,
        leading=20,
        textColor=TEXT_PRIMARY,
        spaceBefore=6,
        spaceAfter=8,
    ))

    styles.add(ParagraphStyle(
        name="SectionLabel",
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=TEXT_MUTED,
        spaceAfter=4,
    ))

    styles.add(ParagraphStyle(
        name="BodyJustified",
        fontName="Helvetica",
        fontSize=10.5,
        leading=16,
        textColor=TEXT_PRIMARY,
        alignment=TA_JUSTIFY,
        spaceAfter=8,
    ))

    styles.add(ParagraphStyle(
        name="CitationItem",
        fontName="Helvetica",
        fontSize=9,
        leading=14,
        textColor=TEXT_SECONDARY,
        leftIndent=20,
        spaceAfter=5,
    ))

    styles.add(ParagraphStyle(
        name="CitationNumber",
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=14,
        textColor=ACCENT,
    ))

    styles.add(ParagraphStyle(
        name="FooterText",
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        textColor=TEXT_MUTED,
        alignment=TA_CENTER,
    ))

    styles.add(ParagraphStyle(
        name="TOCItem",
        fontName="Helvetica",
        fontSize=10,
        leading=16,
        textColor=TEXT_SECONDARY,
        leftIndent=0,
        spaceAfter=3,
    ))

    styles.add(ParagraphStyle(
        name="StatLabel",
        fontName="Helvetica",
        fontSize=8,
        leading=11,
        textColor=TEXT_MUTED,
        alignment=TA_CENTER,
    ))

    styles.add(ParagraphStyle(
        name="StatValue",
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        textColor=TEXT_PRIMARY,
        alignment=TA_CENTER,
    ))

    return styles


# ─── Confidence Helpers ────────────────────────────────────────────────────────

def confidence_label(score: float) -> str:
    if score >= 0.85:
        return "HIGH"
    elif score >= 0.65:
        return "MEDIUM"
    return "LOW"


def confidence_color(score: float) -> colors.Color:
    if score >= 0.85:
        return GREEN
    elif score >= 0.65:
        return AMBER
    return RED


def _color_hex(c: colors.Color) -> str:
    return f"#{int(c.red*255):02x}{int(c.green*255):02x}{int(c.blue*255):02x}"


# ─── Page Template ─────────────────────────────────────────────────────────────

def on_page(canvas, doc):
    """Draw header and footer on every page except the cover."""
    canvas.saveState()
    page_num = doc.page

    if page_num > 1:
        # Header line
        canvas.setStrokeColor(BORDER)
        canvas.setLineWidth(0.5)
        canvas.line(0.75 * inch, 10.3 * inch, 7.75 * inch, 10.3 * inch)

        # Header text
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(TEXT_MUTED)
        canvas.drawString(0.75 * inch, 10.38 * inch, "ResearchForge - Multi-Agent Research Report")
        canvas.drawRightString(7.75 * inch, 10.38 * inch, datetime.now().strftime("%B %d, %Y"))

        # Footer line
        canvas.line(0.75 * inch, 0.65 * inch, 7.75 * inch, 0.65 * inch)

        # Footer text
        canvas.drawCentredString(4.25 * inch, 0.45 * inch, f"Page {page_num}")

    canvas.restoreState()


# ─── Cover Page ────────────────────────────────────────────────────────────────

def build_cover_page(topic: str, depth: str, overall_confidence: float,
                     sources_count: int, sections_count: int, claims_count: int,
                     styles) -> List:
    story = []

    # Top accent bar
    accent_bar = Table([[""]], colWidths=[6.5 * inch], rowHeights=[6])
    accent_bar.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), ACCENT),
        ("LINEBELOW", (0, 0), (-1, -1), 0, WHITE),
    ]))
    story.append(accent_bar)
    story.append(Spacer(1, 0.5 * inch))

    # Report label
    story.append(Paragraph("RESEARCH REPORT", styles["SectionLabel"]))
    story.append(Spacer(1, 0.15 * inch))

    # Title
    story.append(Paragraph(topic, styles["CoverTitle"]))
    story.append(Spacer(1, 0.1 * inch))

    # Subtitle
    depth_label = "Quick Report (3 sections)" if depth == "quick" else "Deep Report (6 sections)"
    story.append(Paragraph(f"Generated by ResearchForge · {depth_label}", styles["CoverSubtitle"]))
    story.append(Spacer(1, 0.1 * inch))

    # Date
    story.append(Paragraph(
        f"Generated on {datetime.now().strftime('%B %d, %Y at %I:%M %p')}",
        styles["CoverMeta"]
    ))
    story.append(Spacer(1, 0.5 * inch))

    # Divider
    story.append(HRFlowable(width="100%", thickness=0.5, color=BORDER, spaceAfter=0.4 * inch))

    # Stats table
    conf_pct = f"{round(overall_confidence * 100)}%"
    conf_lbl = confidence_label(overall_confidence)
    conf_col = confidence_color(overall_confidence)

    stats_data = [
        [
            Paragraph("OVERALL CONFIDENCE", styles["StatLabel"]),
            Paragraph("SECTIONS", styles["StatLabel"]),
            Paragraph("SOURCES CITED", styles["StatLabel"]),
            Paragraph("CLAIMS VERIFIED", styles["StatLabel"]),
        ],
        [
            Paragraph(f'<font color="{_color_hex(conf_col)}">{conf_pct} {conf_lbl}</font>', styles["StatValue"]),
            Paragraph(str(sections_count), styles["StatValue"]),
            Paragraph(str(sources_count), styles["StatValue"]),
            Paragraph(str(claims_count), styles["StatValue"]),
        ]
    ]

    stats_table = Table(stats_data, colWidths=[1.625 * inch] * 4)
    stats_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BG_LIGHT),
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))

    story.append(stats_table)
    story.append(Spacer(1, 0.5 * inch))

    # System info
    story.append(Paragraph(
        "Generated using ResearchForge - a hierarchical multi-agent system built with LangGraph. "
        "Pipeline: Web Research → Fact Verification → Outline → Synthesis → Citations. "
        "Confidence scores reflect LLM-as-judge fact verification across retrieved sources.",
        styles["CoverMeta"]
    ))

    story.append(PageBreak())
    return story


# ─── Table of Contents ─────────────────────────────────────────────────────────

def build_toc(sections: List[dict], styles) -> List:
    story = []
    story.append(Paragraph("TABLE OF CONTENTS", styles["SectionLabel"]))
    story.append(Spacer(1, 0.2 * inch))
    story.append(HRFlowable(width="100%", thickness=0.5, color=BORDER, spaceAfter=0.2 * inch))

    for i, section in enumerate(sections):
        title = section.get("title", f"Section {i + 1}")
        toc_data = [[
            Paragraph(f"{i + 1}. {title}", styles["TOCItem"]),
            Paragraph(str(i + 3), styles["TOCItem"]),
        ]]
        toc_row = Table(toc_data, colWidths=[5.5 * inch, 0.5 * inch])
        toc_row.setStyle(TableStyle([
            ("ALIGN", (1, 0), (1, 0), "RIGHT"),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        story.append(toc_row)

    story.append(Spacer(1, 0.3 * inch))
    story.append(PageBreak())
    return story


# ─── Section Pages ─────────────────────────────────────────────────────────────

def build_section(section: dict, section_num: int, total_sections: int,
                  confidence_score: float, styles) -> List:
    story = []

    title = section.get("title", f"Section {section_num}")
    content = section.get("content", "")
    word_count = section.get("word_count", 0)
    sources_used = len(section.get("sources_used", []))

    conf_pct = round(confidence_score * 100)
    conf_lbl = confidence_label(confidence_score)
    conf_col = confidence_color(confidence_score)

    # Section header block
    header_data = [[
        Paragraph(
            f'<font size="9" color="#718096">SECTION {section_num} OF {total_sections}</font><br/>'
            f'<b>{title}</b>',
            styles["SectionHeading"]
        ),
        Paragraph(
            f'<font color="#718096" size="8">{conf_lbl}</font><br/>'
            f'<font size="14"><b>{conf_pct}%</b></font>',
            ParagraphStyle(
                name=f"ConfBadge_{section_num}",
                fontName="Helvetica",
                fontSize=10,
                textColor=conf_col,
                alignment=TA_RIGHT,
            )
        ),
    ]]

    header_table = Table(header_data, colWidths=[4.5 * inch, 1.5 * inch])
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))

    # Confidence bar
    bar_filled = max(0.1, confidence_score) * 6.0 * inch
    bar_data = [["", ""]]
    bar_table = Table(bar_data, colWidths=[bar_filled, 6.0 * inch - bar_filled], rowHeights=[4])
    bar_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), conf_col),
        ("BACKGROUND", (1, 0), (1, 0), BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))

    elements = [
        header_table,
        Spacer(1, 6),
        bar_table,
        Spacer(1, 0.2 * inch),
    ]

    # Section body - split into paragraphs
    paragraphs = [p.strip() for p in content.split("\n") if p.strip()]
    for para in paragraphs:
        elements.append(Paragraph(para, styles["BodyJustified"]))

    # Section footer meta
    meta_text = f"{word_count} words · {sources_used} sources cited"
    elements.append(Spacer(1, 0.1 * inch))
    elements.append(Paragraph(meta_text, styles["CoverMeta"]))
    elements.append(Spacer(1, 0.4 * inch))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=BORDER, spaceAfter=0.3 * inch))

    story.extend(elements)
    return story


# ─── Citations Page ─────────────────────────────────────────────────────────────

def build_citations_page(sources: List[dict], styles) -> List:
    story = []
    story.append(PageBreak())
    story.append(Paragraph("REFERENCES", styles["SectionLabel"]))
    story.append(Spacer(1, 0.15 * inch))
    story.append(HRFlowable(width="100%", thickness=0.5, color=BORDER, spaceAfter=0.25 * inch))

    for i, source in enumerate(sources):
        num = source.get("citation_number", i + 1)
        title = source.get("title", "Untitled Source")
        domain = source.get("domain", "")
        url = source.get("url", "")

        row_data = [[
            Paragraph(f"[{num}]", styles["CitationNumber"]),
            Paragraph(
                f'<b>{title}</b><br/>'
                f'<font color="#6366f1">{url}</font><br/>'
                f'<font color="#718096">{domain}</font>',
                styles["CitationItem"]
            ),
        ]]

        row_table = Table(row_data, colWidths=[0.4 * inch, 5.85 * inch])
        row_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ]))

        story.append(row_table)

    story.append(Spacer(1, 0.4 * inch))
    story.append(HRFlowable(width="100%", thickness=0.5, color=BORDER))
    story.append(Spacer(1, 0.1 * inch))
    story.append(Paragraph(
        "This report was generated by ResearchForge - a hierarchical multi-agent research system "
        "using LangGraph Supervisor pattern with WebResearch, FactCheck, Outline, Synthesis, and Citation agents.",
        styles["FooterText"]
    ))

    return story


# ─── Main Export Function ───────────────────────────────────────────────────────

def export_report_to_pdf(state: dict, output_path: str) -> str:
    """
    Generate a structured PDF report from the final ReportState.

    Args:
        state: The complete ReportState dict with written_sections, sources, confidence_scores, etc.
        output_path: Full file path where the PDF should be saved

    Returns:
        str: Path to the generated PDF file

    Raises:
        ValueError: If required state fields are missing
        Exception: If PDF generation fails
    """
    topic = state.get("topic", "Research Report")
    depth = state.get("depth", "quick")
    written_sections = state.get("written_sections", [])
    sources = state.get("sources", [])
    confidence_scores = state.get("confidence_scores", {})
    overall_confidence = state.get("overall_confidence", 0.0)
    fact_check_results = state.get("fact_check_results", [])

    if not written_sections:
        raise ValueError("No written sections in state - report is incomplete")

    logger.info(f"Generating PDF for topic: {topic} ({len(written_sections)} sections, {len(sources)} sources)")

    # Ensure output directory exists
    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)

    styles = build_styles()

    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.9 * inch,
        bottomMargin=0.75 * inch,
        title=topic,
        author="ResearchForge",
        subject="Multi-Agent Research Report",
        creator="ResearchForge - LangGraph Multi-Agent System",
    )

    story = []

    # Cover page
    story.extend(build_cover_page(
        topic=topic,
        depth=depth,
        overall_confidence=overall_confidence,
        sources_count=len(sources),
        sections_count=len(written_sections),
        claims_count=len(fact_check_results),
        styles=styles
    ))

    # Table of contents
    story.extend(build_toc(written_sections, styles))

    # Section pages
    for i, section in enumerate(written_sections):
        section_id = section.get("section_id", f"sec_{i + 1}")
        confidence = confidence_scores.get(section_id, overall_confidence)
        story.extend(build_section(
            section=section,
            section_num=i + 1,
            total_sections=len(written_sections),
            confidence_score=confidence,
            styles=styles
        ))

    # Citations page
    if sources:
        story.extend(build_citations_page(sources, styles))

    # Build PDF
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)

    file_size_kb = round(os.path.getsize(output_path) / 1024, 1)
    logger.info(f"PDF generated successfully: {output_path} ({file_size_kb} KB)")

    return output_path
