"""Designer-facing PDF report for one layout option.

Generated from structured layout data (the same `ProjectInput` /
`EngineOutput` the Markdown report consumes) — *not* by parsing
Markdown, so tables, status banners, and warnings stay reliable.

Layout (A4 portrait):
  1. Title block (project, strategy, generated_at)
  2. Status banner (layout_status / inventory_status / coverage / waste)
  3. Coverage-vs-waste explanation
  4. Main metrics table
  5. Preview image (if supplied)
  6. Designer review notes (one block per ReviewMarker)
  7. Risky pieces (per piece with risk_flags)
  8. Piece schedule table
  9. Notes & limitations

Uses ReportLab Platypus so multi-page flow, page numbers, and table
splitting are handled automatically — no manual coordinates.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from placement_engine.exporters._report_common import (
    NOTES_AND_LIMITATIONS,
    fmt_int_mm,
    piece_bbox,
    piece_centroid,
    suggested_marker_action,
    suggested_risk_action,
)
from placement_engine.models import (
    EngineOutput,
    LayoutOption,
    ProjectInput,
)


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title":     ParagraphStyle("title", parent=base["Title"], fontSize=18, spaceAfter=4),
        "subtitle":  ParagraphStyle("subtitle", parent=base["Normal"], fontSize=10,
                                    textColor=colors.HexColor("#555555"), spaceAfter=12),
        "h2":        ParagraphStyle("h2", parent=base["Heading2"], fontSize=13,
                                    spaceBefore=12, spaceAfter=6),
        "h3":        ParagraphStyle("h3", parent=base["Heading3"], fontSize=11,
                                    spaceBefore=8, spaceAfter=4),
        "body":      ParagraphStyle("body", parent=base["Normal"], fontSize=10,
                                    leading=13, spaceAfter=4),
        "caption":   ParagraphStyle("caption", parent=base["Normal"], fontSize=9,
                                    textColor=colors.HexColor("#555555"),
                                    spaceAfter=8),
        "bullet":    ParagraphStyle("bullet", parent=base["Normal"], fontSize=9,
                                    leftIndent=12, bulletIndent=0,
                                    leading=12, spaceAfter=2),
        "banner_ok":   ParagraphStyle("banner_ok", parent=base["Normal"],
                                      backColor=colors.HexColor("#e6f4ea"),
                                      borderColor=colors.HexColor("#1e8e3e"),
                                      borderWidth=0.5, borderPadding=6,
                                      fontSize=11, leading=14, spaceAfter=10),
        "banner_warn": ParagraphStyle("banner_warn", parent=base["Normal"],
                                      backColor=colors.HexColor("#fef7e0"),
                                      borderColor=colors.HexColor("#f9ab00"),
                                      borderWidth=0.5, borderPadding=6,
                                      fontSize=11, leading=14, spaceAfter=10),
        "banner_err":  ParagraphStyle("banner_err", parent=base["Normal"],
                                      backColor=colors.HexColor("#fce8e6"),
                                      borderColor=colors.HexColor("#d93025"),
                                      borderWidth=0.5, borderPadding=6,
                                      fontSize=11, leading=14, spaceAfter=10),
    }


_TABLE_STYLE_BASE = TableStyle([
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eaed")),
    ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#202124")),
    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ("FONTSIZE", (0, 0), (-1, -1), 9),
    ("ALIGN", (0, 0), (-1, -1), "LEFT"),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c7c8cc")),
    ("LEFTPADDING", (0, 0), (-1, -1), 5),
    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ("TOPPADDING", (0, 0), (-1, -1), 3),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
])


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _status_banner_style(styles: dict, option: LayoutOption) -> ParagraphStyle:
    m = option.metrics
    if m.layout_status == "complete" and m.inventory_status == "sufficient":
        return styles["banner_ok"]
    if m.layout_status == "failed":
        return styles["banner_err"]
    return styles["banner_warn"]


def _title_block(styles, project_input, output, option) -> list:
    return [
        Paragraph("Marble Layout Review Report", styles["title"]),
        Paragraph(
            f"Project: <b>{project_input.project_id}</b> &nbsp;|&nbsp; "
            f"Strategy: <b>{option.strategy}</b> &nbsp;|&nbsp; "
            f"Generated: {output.generated_at}",
            styles["subtitle"],
        ),
    ]


def _status_block(styles, option) -> list:
    m = option.metrics
    text = (
        f"<b>Layout status:</b> {m.layout_status.upper()} &nbsp; | &nbsp; "
        f"<b>Inventory:</b> {m.inventory_status.upper()} &nbsp; | &nbsp; "
        f"<b>Coverage:</b> {m.coverage_percentage}% &nbsp; | &nbsp; "
        f"<b>Slab waste:</b> {m.waste_percentage}%"
    )
    return [Paragraph(text, _status_banner_style(styles, option))]


def _explainer(styles) -> list:
    return [
        Paragraph(
            "<b>Reading these numbers.</b> Coverage measures project "
            "completion (installed area ÷ usable project area). Waste "
            "measures unused material from the slabs that were consumed "
            "in this layout — the two are different numbers.",
            styles["caption"],
        ),
    ]


def _metrics_table(styles, option) -> list:
    m = option.metrics
    rows = [
        ["Metric", "Value"],
        ["project_usable_area",   f"{fmt_int_mm(m.project_usable_area)} mm²"],
        ["installed_area",        f"{fmt_int_mm(m.installed_area)} mm²"],
        ["uncovered_area",        f"{fmt_int_mm(m.uncovered_area)} mm²"],
        ["coverage_percentage",   f"{m.coverage_percentage} %"],
        ["total_slab_area_used",  f"{fmt_int_mm(m.total_slab_area_used)} mm²"],
        ["waste_area",            f"{fmt_int_mm(m.waste_area)} mm²"],
        ["waste_percentage",      f"{m.waste_percentage} %"],
        ["slabs_used",            str(m.slabs_used)],
        ["piece_count",           str(m.piece_count)],
        ["seam_count",            str(m.seam_count)],
        ["total_seam_length",     f"{fmt_int_mm(m.total_seam_length)} mm"],
    ]
    table = Table(rows, colWidths=[55 * mm, 60 * mm], repeatRows=1)
    table.setStyle(_TABLE_STYLE_BASE)
    return [Paragraph("Metrics", styles["h2"]), table]


def _preview_block(styles, preview_path: Path | None) -> list:
    if preview_path is None or not Path(preview_path).is_file():
        return []
    # Fit to the printable width; preserve aspect ratio.
    max_w = 170 * mm  # A4 portrait minus margins
    img = Image(str(preview_path))
    iw, ih = img.imageWidth, img.imageHeight
    if iw <= 0 or ih <= 0:  # defensive
        return []
    scale = min(1.0, max_w / iw)
    img.drawWidth = iw * scale
    img.drawHeight = ih * scale
    return [
        Paragraph("Layout preview", styles["h2"]),
        img,
        Paragraph(
            "Preview rendered by the matplotlib debug plot. The DXF file "
            "is the editable source for Rhino/AutoCAD.",
            styles["caption"],
        ),
    ]


def _review_notes_block(styles, option) -> list:
    blocks: list = [Paragraph("Designer review notes", styles["h2"])]
    if not option.review_markers:
        blocks.append(Paragraph("No review markers raised by the engine.",
                                styles["body"]))
        return blocks
    for marker in option.review_markers:
        loc = (
            f"({fmt_int_mm(marker.location[0])}, {fmt_int_mm(marker.location[1])})"
            if marker.location is not None
            else "layout-level (no specific point)"
        )
        related = (
            ", ".join(marker.related_piece_ids) if marker.related_piece_ids
            else "(none)"
        )
        body = (
            f"<b>{marker.review_id} — {marker.type.replace('_', ' ').title()}</b><br/>"
            f"Severity: {marker.severity} &nbsp;|&nbsp; "
            f"Location: {loc} &nbsp;|&nbsp; Related: {related}<br/>"
            f"<b>Message:</b> {marker.message}<br/>"
            f"<b>Suggested action:</b> {suggested_marker_action(marker)}"
        )
        blocks.append(KeepTogether(Paragraph(body, styles["body"])))
        blocks.append(Spacer(1, 4))
    return blocks


def _risky_pieces_block(styles, option) -> list:
    flagged = [p for p in option.placed_pieces if p.risk_flags]
    blocks: list = [Paragraph("Risky pieces", styles["h2"])]
    if not flagged:
        blocks.append(Paragraph("No piece-level risk flags raised.",
                                styles["body"]))
        return blocks
    for piece in flagged:
        bxmin, bymin, bxmax, bymax = piece_bbox(piece)
        cx, cy = piece_centroid(piece)
        header = (
            f"<b>{piece.piece_id}</b> (slab {piece.slab_id}, role {piece.piece_role})"
            f"<br/>Centroid: ({fmt_int_mm(cx)}, {fmt_int_mm(cy)}) &nbsp;|&nbsp; "
            f"Bbox: x={fmt_int_mm(bxmin)}–{fmt_int_mm(bxmax)}, "
            f"y={fmt_int_mm(bymin)}–{fmt_int_mm(bymax)}"
        )
        blocks.append(Paragraph(header, styles["body"]))
        for flag in piece.risk_flags:
            line = (
                f"• <b>{flag.type}</b> (severity {flag.severity}) — {flag.message}<br/>"
                f"&nbsp;&nbsp;&nbsp;&nbsp;<i>Suggested action:</i> "
                f"{suggested_risk_action(flag)}"
            )
            blocks.append(Paragraph(line, styles["bullet"]))
        blocks.append(Spacer(1, 4))
    return blocks


def _piece_schedule_block(styles, option) -> list:
    rows = [["piece_id", "slab_id", "role", "risks", "bbox (mm)"]]
    for p in option.placed_pieces:
        bxmin, bymin, bxmax, bymax = piece_bbox(p)
        risk = f"{len(p.risk_flags)} flag(s)" if p.risk_flags else "—"
        bbox = (
            f"x={fmt_int_mm(bxmin)}–{fmt_int_mm(bxmax)}, "
            f"y={fmt_int_mm(bymin)}–{fmt_int_mm(bymax)}"
        )
        rows.append([p.piece_id, p.slab_id, p.piece_role, risk, bbox])
    table = Table(
        rows,
        colWidths=[26 * mm, 22 * mm, 16 * mm, 22 * mm, 84 * mm],
        repeatRows=1,
    )
    table.setStyle(_TABLE_STYLE_BASE)
    return [Paragraph("Piece schedule", styles["h2"]), table]


def _notes_block(styles) -> list:
    items = [Paragraph(f"• {note}", styles["bullet"])
             for note in NOTES_AND_LIMITATIONS]
    return [Paragraph("Notes &amp; limitations", styles["h2"])] + items


# ---------------------------------------------------------------------------
# Page numbering
# ---------------------------------------------------------------------------


def _draw_page_number(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#777777"))
    canvas.drawRightString(
        A4[0] - 15 * mm, 10 * mm, f"Page {doc.page}"
    )
    canvas.restoreState()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def write_pdf_report(
    project_input: ProjectInput,
    output: EngineOutput,
    option: LayoutOption,
    target: str | Path,
    preview_path: str | Path | None = None,
) -> Path:
    """Write a PDF designer report for one layout option and return the path."""
    target_path = Path(target)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    styles = _styles()
    doc = SimpleDocTemplate(
        str(target_path),
        pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title=f"Marble Layout Report — {project_input.project_id} ({option.strategy})",
        author="Marble Placement Engine",
    )

    story: list = []
    story += _title_block(styles, project_input, output, option)
    story += _status_block(styles, option)
    story += _explainer(styles)
    story += _metrics_table(styles, option)
    story.append(Spacer(1, 6))
    story += _preview_block(styles, Path(preview_path) if preview_path else None)
    story += _review_notes_block(styles, option)
    story += _risky_pieces_block(styles, option)
    story.append(PageBreak())
    story += _piece_schedule_block(styles, option)
    story += _notes_block(styles)

    doc.build(story, onFirstPage=_draw_page_number, onLaterPages=_draw_page_number)
    return target_path
