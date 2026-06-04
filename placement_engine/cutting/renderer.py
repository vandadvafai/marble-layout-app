"""Cutting plan preview — one tiled PNG showing every slab and what's
been cut from it.

Each slab is drawn as a rectangle with placed pieces (filled by
classification colour) and offcuts (light-grey hatched). Unassigned
pieces are listed at the bottom of the figure so a fabricator can see
both what's planned and what couldn't be planned.

Coordinates inside each slab are local — origin at the slab's bottom
left. The figure is laid out in a simple grid; slab sizes are not to
the same global scale, but the within-slab geometry is.
"""

from __future__ import annotations

import math
from pathlib import Path

from placement_engine.cutting.schema import (
    CutPlacement,
    CuttingPlan,
    CuttingSlab,
    Offcut,
)

# Match the cut-list renderer's palette so designers see consistent
# colours across reports.
CLASSIFICATION_FACE: dict[str, str] = {
    "full":   "#b9d8b9",
    "edge":   "#f4dca0",
    "hole":   "#c7c0e8",
    "sliver": "#f0b8b8",
}
CLASSIFICATION_EDGE: dict[str, str] = {
    "full":   "#3d8a3d",
    "edge":   "#a07a1a",
    "hole":   "#534598",
    "sliver": "#a03030",
}
DEFAULT_PIECE_FACE = "#cccccc"
DEFAULT_PIECE_EDGE = "#444444"

SLAB_FACE = "#fafafa"
SLAB_EDGE = "#202020"
SLAB_LINEWIDTH = 1.2

OFFCUT_FACE = "#e8e8e8"
OFFCUT_EDGE = "#808080"
OFFCUT_HATCH = "////"

UNASSIGNED_TEXT_COLOR = "#a03030"

DEFAULT_DPI = 150
PER_SLAB_INCH = 4.5
LABEL_FONTSIZE = 7
TITLE_FONTSIZE = 9


def render_cutting_plan_preview(
    plan: CuttingPlan,
    out_path: str | Path,
    *,
    dpi: int = DEFAULT_DPI,
) -> Path:
    """Render the cutting plan to a PNG. Returns the written path."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.patches as mpatches
    import matplotlib.pyplot as plt

    n_slabs = len(plan.slabs)
    has_unassigned = bool(plan.unassigned)
    # Always at least one panel so empty plans still produce a file.
    panels = max(n_slabs, 1)
    cols = min(2, panels)
    rows = math.ceil(panels / cols)
    # Reserve an extra row at the bottom for the unassigned list.
    extra_rows = 1 if has_unassigned else 0

    fig_w = max(7.0, cols * PER_SLAB_INCH)
    fig_h = max(5.0, rows * PER_SLAB_INCH + extra_rows * 1.2)
    fig = plt.figure(figsize=(fig_w, fig_h))

    # Grid: slab axes share the same coordinate-equal aspect; the
    # unassigned strip uses a thinner row so it doesn't dominate.
    if extra_rows:
        gs = fig.add_gridspec(rows + 1, cols, height_ratios=[*([1.0] * rows), 0.35])
    else:
        gs = fig.add_gridspec(rows, cols)

    if n_slabs == 0:
        ax = fig.add_subplot(gs[0, 0])
        ax.set_axis_off()
        ax.text(0.5, 0.5, "no slabs used", ha="center", va="center",
                fontsize=TITLE_FONTSIZE, color=UNASSIGNED_TEXT_COLOR,
                transform=ax.transAxes)

    for i, slab in enumerate(plan.slabs):
        r, c = divmod(i, cols)
        ax = fig.add_subplot(gs[r, c])
        _draw_slab(ax, slab, mpatches)

    # Hide any trailing empty axes in the slab grid for a clean layout.
    for j in range(n_slabs, rows * cols):
        r, c = divmod(j, cols)
        ax = fig.add_subplot(gs[r, c])
        ax.set_axis_off()

    if has_unassigned:
        ax = fig.add_subplot(gs[rows, :])
        _draw_unassigned_strip(ax, plan)

    summary = plan.summary
    fig.suptitle(
        f"Cutting plan — {plan.target_name or plan.target_id}   "
        f"pieces {summary.assigned_cut_pieces}/{summary.total_cut_pieces} assigned   "
        f"slabs {summary.slabs_used} used, {summary.unused_slabs} unused   "
        f"waste {summary.estimated_waste_m2:.2f} m²",
        fontsize=TITLE_FONTSIZE + 1,
        y=0.995,
    )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.97))
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out_path


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _draw_slab(ax, slab: CuttingSlab, mpatches) -> None:
    """Draw one slab: outline, placements, offcuts, labels."""
    ax.set_aspect("equal")
    ax.set_facecolor("white")

    # 1. Slab outline.
    ax.add_patch(mpatches.Rectangle(
        (0, 0), slab.original_width_mm, slab.original_height_mm,
        facecolor=SLAB_FACE, edgecolor=SLAB_EDGE,
        linewidth=SLAB_LINEWIDTH, zorder=1,
    ))

    # 2. Offcuts under placements (so labels stay readable).
    for off in slab.offcuts:
        _draw_offcut(ax, off, mpatches)

    # 3. Placements.
    for placement in slab.placements:
        _draw_placement(ax, placement, mpatches)

    margin = max(slab.original_width_mm, slab.original_height_mm) * 0.04
    ax.set_xlim(-margin, slab.original_width_mm + margin)
    ax.set_ylim(-margin, slab.original_height_mm + margin)
    ax.set_xticks([])
    ax.set_yticks([])
    used_pct = (
        100.0 * slab.used_area_m2 / slab.original_area_m2
        if slab.original_area_m2 > 0 else 0.0
    )
    ax.set_title(
        f"{slab.slab_id}   "
        f"{int(slab.original_width_mm)}×{int(slab.original_height_mm)} mm   "
        f"used {slab.used_area_m2:.2f}/{slab.original_area_m2:.2f} m² "
        f"({used_pct:.0f}%)   "
        f"waste {slab.waste_area_m2:.2f} m²",
        fontsize=TITLE_FONTSIZE,
        pad=4.0,
    )


def _draw_placement(ax, p: CutPlacement, mpatches) -> None:
    face = CLASSIFICATION_FACE.get(p.classification, DEFAULT_PIECE_FACE)
    edge = CLASSIFICATION_EDGE.get(p.classification, DEFAULT_PIECE_EDGE)
    ax.add_patch(mpatches.Rectangle(
        (p.x_mm, p.y_mm), p.width_mm, p.height_mm,
        facecolor=face, edgecolor=edge,
        linewidth=0.9, zorder=3,
    ))
    side = min(p.width_mm, p.height_mm)
    if side < 250:
        # Too small to label legibly.
        return
    fs = LABEL_FONTSIZE if side >= 500 else 5.5
    ax.text(
        p.x_mm + p.width_mm / 2.0,
        p.y_mm + p.height_mm / 2.0,
        f"{p.cut_piece_id}\n{p.source_layout_piece_id}",
        ha="center", va="center", fontsize=fs, color="#202020", zorder=4,
    )


def _draw_offcut(ax, o: Offcut, mpatches) -> None:
    ax.add_patch(mpatches.Rectangle(
        (o.x_mm, o.y_mm), o.width_mm, o.height_mm,
        facecolor=OFFCUT_FACE, edgecolor=OFFCUT_EDGE,
        linewidth=0.6, hatch=OFFCUT_HATCH, zorder=2,
    ))
    side = min(o.width_mm, o.height_mm)
    if side < 250:
        return
    ax.text(
        o.x_mm + o.width_mm / 2.0,
        o.y_mm + o.height_mm / 2.0,
        f"offcut\n{o.area_m2:.2f} m²",
        ha="center", va="center",
        fontsize=LABEL_FONTSIZE - 1, color="#404040",
        zorder=3,
    )


def _draw_unassigned_strip(ax, plan: CuttingPlan) -> None:
    ax.set_axis_off()
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    items = [
        f"{u.cut_piece_id} ({u.classification}, "
        f"{int(u.width_mm)}×{int(u.height_mm)} mm, {u.reason})"
        for u in plan.unassigned
    ]
    text = "UNASSIGNED: " + "  ·  ".join(items) if items else ""
    ax.text(
        0.01, 0.5, text,
        ha="left", va="center",
        fontsize=LABEL_FONTSIZE,
        color=UNASSIGNED_TEXT_COLOR,
        wrap=True,
    )
