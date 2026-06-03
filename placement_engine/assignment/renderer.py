"""Assignment preview — clean fabrication-readable PNG.

Each piece is filled by its assignment status (green = assigned,
red = unassigned) and labelled with its piece ID and either the
assigned slab ID or ``UNASSIGNED``. No marble photos at this stage.

The renderer accepts the optional ``boundary`` + ``holes`` of the
source target so it can draw the floor outline behind the pieces.
Caller pulls those from the source layout (or wherever else); the
assignment package itself stays free of layout-schema knowledge.
"""

from __future__ import annotations

from pathlib import Path

from placement_engine.assignment.schema import (
    ASSIGNMENT_ASSIGNED,
    Assignment,
)

# Status palette
ASSIGNED_FACE = "#d8f0d8"
ASSIGNED_EDGE = "#3d8a3d"
UNASSIGNED_FACE = "#ffd6d6"
UNASSIGNED_EDGE = "#b03030"

INTERIOR_HOLE_FACE = "white"
INTERIOR_HOLE_EDGE = "#888888"
INTERIOR_HOLE_LINEWIDTH = 0.75

BG_COLOR = "white"
BOUNDARY_COLOR = "#202020"
BOUNDARY_LINEWIDTH = 1.0
TARGET_HOLE_FACE = "#ececec"
TARGET_HOLE_EDGE = "#909090"
TARGET_HOLE_LINEWIDTH = 0.75

# Smaller pieces (slivers) need smaller labels to stay readable.
LABEL_FONTSIZE_TARGET = 7

DEFAULT_DPI = 150
DEFAULT_FIG_HEIGHT_IN = 8.0


def render_assignment_preview(
    assignment: Assignment,
    out_path: str | Path,
    *,
    boundary: list[tuple[float, float]] | None = None,
    holes: list[list[tuple[float, float]]] | None = None,
    show_legend: bool = True,
    show_slab_ids: bool = True,
    dpi: int = DEFAULT_DPI,
) -> Path:
    """Render the assignment preview to a PNG. Returns the written path.

    ``show_slab_ids`` controls whether assigned slab IDs appear on each
    piece (default True). Turn off for an at-a-glance status view that
    only colour-codes assigned vs unassigned.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.patches as mpatches
    import matplotlib.pyplot as plt

    bbox = _piece_bbox(assignment)
    bx0, by0, bx1, by1 = bbox
    w, h = bx1 - bx0, by1 - by0
    fig_w, fig_h = _figure_size(w, h)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    ax.set_facecolor(BG_COLOR)
    ax.set_axis_off()

    # 1. Target holes under the pieces (so designers see them in
    #    context even when a piece overlaps the hole on its perimeter).
    if holes:
        for hole in holes:
            ax.add_patch(mpatches.Polygon(
                hole, closed=True,
                facecolor=TARGET_HOLE_FACE, edgecolor=TARGET_HOLE_EDGE,
                linewidth=TARGET_HOLE_LINEWIDTH, zorder=2,
            ))

    # 2. Pieces by assignment status.
    for piece in assignment.pieces:
        if piece.assignment_status == ASSIGNMENT_ASSIGNED:
            face, edge = ASSIGNED_FACE, ASSIGNED_EDGE
        else:
            face, edge = UNASSIGNED_FACE, UNASSIGNED_EDGE
        ax.add_patch(mpatches.Polygon(
            piece.cut_polygon_exterior, closed=True,
            facecolor=face, edgecolor=edge,
            linewidth=0.7, zorder=3,
        ))
        # Interior cuts — white punched-out patches.
        for ring in piece.cut_polygon_interiors:
            ax.add_patch(mpatches.Polygon(
                ring, closed=True,
                facecolor=INTERIOR_HOLE_FACE, edgecolor=INTERIOR_HOLE_EDGE,
                linewidth=INTERIOR_HOLE_LINEWIDTH, zorder=4,
            ))
        _draw_piece_label(ax, piece, show_slab_ids=show_slab_ids)

    # 3. Boundary on top.
    if boundary:
        ax.add_patch(mpatches.Polygon(
            boundary, closed=True, fill=False,
            edgecolor=BOUNDARY_COLOR, linewidth=BOUNDARY_LINEWIDTH, zorder=6,
        ))

    # 4. Legend.
    if show_legend:
        _draw_legend(ax, assignment, mpatches)

    margin = max(w, h) * 0.04
    ax.set_xlim(bx0 - margin, bx1 + margin)
    ax.set_ylim(by0 - margin, by1 + margin)
    ax.set_aspect("equal")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(pad=0.4)
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight", facecolor=BG_COLOR)
    plt.close(fig)
    return out_path


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _piece_bbox(assignment: Assignment) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for p in assignment.pieces:
        for x, y in p.cut_polygon_exterior:
            xs.append(x)
            ys.append(y)
    if not xs:
        return 0.0, 0.0, 1.0, 1.0
    return min(xs), min(ys), max(xs), max(ys)


def _piece_centroid(coords: list[tuple[float, float]]) -> tuple[float, float]:
    """Bbox centre — fine for label placement and avoids Shapely."""
    if not coords:
        return 0.0, 0.0
    xs = [x for x, _ in coords]
    ys = [y for _, y in coords]
    return (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0


def _figure_size(width_mm: float, height_mm: float) -> tuple[float, float]:
    aspect = width_mm / height_mm if height_mm > 0 else 1.0
    fig_h = DEFAULT_FIG_HEIGHT_IN
    fig_w = max(6.0, min(16.0, fig_h * aspect))
    return fig_w, fig_h


def _label_font_size(bbox_w_mm: float, bbox_h_mm: float) -> float:
    side = min(bbox_w_mm, bbox_h_mm)
    if side < 250:
        return 0.0
    if side < 500:
        return 4.5
    return LABEL_FONTSIZE_TARGET


def _draw_piece_label(ax, piece, *, show_slab_ids: bool) -> None:
    """Two-line label centred in the piece bbox: P-id on top, slab info below."""
    cx, cy = _piece_centroid(piece.cut_polygon_exterior)
    fs = _label_font_size(piece.piece_width_mm, piece.piece_height_mm)
    if fs <= 0:
        return
    top = piece.piece_id
    if piece.assignment_status == ASSIGNMENT_ASSIGNED:
        bottom = piece.assigned_slab_id or ""
        if not show_slab_ids:
            bottom = ""
    else:
        bottom = "UNASSIGNED"
    label = top if not bottom else f"{top}\n{bottom}"
    ax.text(
        cx, cy, label,
        ha="center", va="center",
        fontsize=fs, color="#202020", zorder=5,
    )


def _draw_legend(ax, assignment: Assignment, mpatches) -> None:
    summary = assignment.summary
    handles = [
        mpatches.Patch(
            facecolor=ASSIGNED_FACE, edgecolor=ASSIGNED_EDGE,
            label=f"assigned ({summary.assigned_pieces})",
        ),
        mpatches.Patch(
            facecolor=UNASSIGNED_FACE, edgecolor=UNASSIGNED_EDGE,
            label=f"unassigned ({summary.unassigned_pieces})",
        ),
    ]
    ax.legend(
        handles=handles, loc="upper left",
        fontsize=7, framealpha=0.85,
        title=(
            f"Pieces: {summary.total_pieces}   "
            f"Slabs used: {summary.slabs_used}/{summary.total_slab_count}   "
            f"Waste: {summary.estimated_waste_m2:.2f} m²"
        ),
        title_fontsize=7,
    )
