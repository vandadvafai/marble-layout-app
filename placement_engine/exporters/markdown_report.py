"""Markdown report for the CAD/Rhino/AutoCAD designer hand-off.

The report is the **verbose companion** to the DXF: the drawing stays
clean while the report carries every metric, every piece, every seam,
every review marker and every risk flag with addresses (`piece_id`,
centroid, bounding box) so the designer can find the right area
quickly in Rhino/AutoCAD.

Output is plain Markdown — easy to render in any viewer, easy to diff,
and a clean precursor to a future PDF if needed.
"""

from __future__ import annotations

from pathlib import Path

from placement_engine.exporters._report_common import (
    fmt_int_mm as _fmt_int_mm,
    piece_bbox as _bbox,
    piece_centroid as _centroid,
    seam_endpoints as _seam_endpoints,
    suggested_marker_action as _suggested_marker_action,
    suggested_risk_action as _suggested_risk_action,
)
from placement_engine.models import (
    EngineOutput,
    LayoutOption,
    ProjectInput,
)


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _section_title(project_input: ProjectInput) -> list[str]:
    return [f"# Marble Layout Report — `{project_input.project_id}`", ""]


def _section_summary(
    output: EngineOutput, option: LayoutOption
) -> list[str]:
    m = option.metrics
    lines = [
        "## Summary",
        "",
        f"- **Project ID:** `{output.project_id}`",
        f"- **Engine version:** `{output.engine_version}`",
        f"- **Generated at:** `{output.generated_at}`",
        f"- **Strategy:** `{option.strategy}`",
        f"- **Layout status:** **{m.layout_status.upper()}**",
        f"- **Inventory status:** **{m.inventory_status.upper()}**",
        f"- **Coverage:** {m.coverage_percentage:.1f} %",
        f"- **Slab waste:** {m.waste_percentage:.1f} %",
        f"- **Slabs used:** {m.slabs_used}",
        f"- **Pieces:** {m.piece_count}",
        f"- **Seams:** {m.seam_count} ({m.total_seam_length:.0f} mm total)",
        "",
        f"_Engine summary:_ {option.explanation.summary}",
        "",
    ]
    return lines


def _section_metrics(option: LayoutOption) -> list[str]:
    m = option.metrics
    rows = [
        ("project_usable_area", f"{_fmt_int_mm(m.project_usable_area)} mm²"),
        ("installed_area", f"{_fmt_int_mm(m.installed_area)} mm²"),
        ("uncovered_area", f"{_fmt_int_mm(m.uncovered_area)} mm²"),
        ("coverage_percentage", f"{m.coverage_percentage} %"),
        ("total_slab_area_used", f"{_fmt_int_mm(m.total_slab_area_used)} mm²"),
        ("waste_area", f"{_fmt_int_mm(m.waste_area)} mm²"),
        ("waste_percentage", f"{m.waste_percentage} %"),
        ("piece_count", f"{m.piece_count}"),
        ("slabs_used", f"{m.slabs_used}"),
        ("seam_count", f"{m.seam_count}"),
        ("total_seam_length", f"{_fmt_int_mm(m.total_seam_length)} mm"),
        ("small_piece_count", f"{m.small_piece_count}"),
        ("layout_status", f"`{m.layout_status}`"),
        ("inventory_status", f"`{m.inventory_status}`"),
    ]
    out = ["## Metrics", "", "| Metric | Value |", "|---|---|"]
    for k, v in rows:
        out.append(f"| `{k}` | {v} |")
    out.append("")
    return out


def _section_pieces(option: LayoutOption) -> list[str]:
    out = [
        "## Pieces",
        "",
        ("| `piece_id` | `slab_id` | `source_slab_id` | role | full slab? | "
         "bbox (mm) | centroid (mm) | risk flags |"),
        "|---|---|---|---|---|---|---|---|",
    ]
    for p in option.placed_pieces:
        bxmin, bymin, bxmax, bymax = _bbox(p)
        cx, cy = _centroid(p)
        bbox = f"x={_fmt_int_mm(bxmin)}–{_fmt_int_mm(bxmax)}, y={_fmt_int_mm(bymin)}–{_fmt_int_mm(bymax)}"
        centroid = f"({_fmt_int_mm(cx)}, {_fmt_int_mm(cy)})"
        out.append(
            f"| `{p.piece_id}` | `{p.slab_id}` | `{p.source_slab_id or p.slab_id}` | "
            f"{p.piece_role} | {'yes' if p.is_full_slab else 'no'} | {bbox} | "
            f"{centroid} | {len(p.risk_flags)} |"
        )
    out.append("")
    return out


def _section_seams(option: LayoutOption) -> list[str]:
    if not option.seams:
        return ["## Seams", "", "_No seams detected._", ""]
    out = [
        "## Seams",
        "",
        "| `seam_id` | between | length (mm) | from | to | visibility |",
        "|---|---|---|---|---|---|",
    ]
    for s in option.seams:
        a, b = _seam_endpoints(s)
        out.append(
            f"| `{s.seam_id}` | `{s.piece_ids[0]}` ↔ `{s.piece_ids[1]}` | "
            f"{_fmt_int_mm(s.length)} | "
            f"({_fmt_int_mm(a[0])}, {_fmt_int_mm(a[1])}) | "
            f"({_fmt_int_mm(b[0])}, {_fmt_int_mm(b[1])}) | {s.visibility} |"
        )
    out.append("")
    return out


def _section_review_notes(option: LayoutOption) -> list[str]:
    out = ["## Designer Review Notes", ""]
    if not option.review_markers:
        out += ["_No review markers raised by the engine._", ""]
        return out
    for m in option.review_markers:
        loc = (
            f"({_fmt_int_mm(m.location[0])}, {_fmt_int_mm(m.location[1])})"
            if m.location is not None
            else "_layout-level — no specific point_"
        )
        related = ", ".join(f"`{pid}`" for pid in m.related_piece_ids) or "_(none)_"
        out += [
            f"### {m.review_id} — {m.type.replace('_', ' ').title()}",
            "",
            f"- **Severity:** {m.severity}",
            f"- **Location:** {loc}",
            f"- **Related pieces:** {related}",
            "",
            f"**Message.** {m.message}",
            "",
            f"**Suggested action.** {_suggested_marker_action(m)}",
            "",
        ]
    return out


def _section_risk_flags(option: LayoutOption) -> list[str]:
    flagged = [p for p in option.placed_pieces if p.risk_flags]
    out = ["## Risk Flags", ""]
    if not flagged:
        out += ["_No piece-level risk flags raised._", ""]
        return out
    for piece in flagged:
        bxmin, bymin, bxmax, bymax = _bbox(piece)
        cx, cy = _centroid(piece)
        out += [
            f"### `{piece.piece_id}` (slab `{piece.slab_id}`, role {piece.piece_role})",
            "",
            f"- **Centroid:** ({_fmt_int_mm(cx)}, {_fmt_int_mm(cy)})",
            (
                f"- **Bounding box:** x={_fmt_int_mm(bxmin)}–{_fmt_int_mm(bxmax)}, "
                f"y={_fmt_int_mm(bymin)}–{_fmt_int_mm(bymax)}"
            ),
            "",
        ]
        for flag in piece.risk_flags:
            out += [
                f"- **{flag.type}** (severity {flag.severity}) — {flag.message}",
                f"  - _Suggested action:_ {_suggested_risk_action(flag)}",
            ]
        out.append("")
    return out


def _section_notes() -> list[str]:
    return [
        "## Notes & Limitations",
        "",
        "- This is an **AI-generated first-draft layout**. A designer must "
        "review and approve the geometry before it is shared with a "
        "customer or sent to production.",
        "- Visual matching and vein-direction scoring are not yet "
        "implemented; the strategy ranks layouts by geometry, not "
        "aesthetics.",
        "- Production / factory cut counting and complexity scoring are "
        "MVP placeholders. Treat `cut_count_estimate`, "
        "`cutting_complexity_score`, and `estimated_production_difficulty` "
        "as advisory only until the production team defines the formula.",
        "- The DXF is intended as an **editable review draft** for "
        "Rhino/AutoCAD, not a final factory cutting file.",
        "- DWG export is not provided directly; use Rhino/AutoCAD to "
        "save-as DWG if a customer needs that format.",
        "",
    ]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def write_report(
    project_input: ProjectInput,
    output: EngineOutput,
    option: LayoutOption,
    target: str | Path,
) -> Path:
    """Write a Markdown report for one layout option and return the path."""
    target_path = Path(target)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    lines += _section_title(project_input)
    lines += _section_summary(output, option)
    lines += _section_metrics(option)
    lines += _section_pieces(option)
    lines += _section_seams(option)
    lines += _section_review_notes(option)
    lines += _section_risk_flags(option)
    lines += _section_notes()

    target_path.write_text("\n".join(lines))
    return target_path
