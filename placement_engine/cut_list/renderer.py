"""Fabrication-style cut-list preview.

A read-only view over a `CutList`: each piece is filled by its primary
classification, labelled with ``piece_id  W × H``, and a small legend
spells out the colour-code so designers can scan the preview at a
glance. Interior holes (true internal cuts) are drawn as white "punch
out" patches so they're visible in the rendered piece.
"""

from __future__ import annotations

from pathlib import Path

from placement_engine.cut_list.schema import (
    CLASSIFICATION_EDGE,
    CLASSIFICATION_FULL,
    CLASSIFICATION_HOLE,
    CLASSIFICATION_SLIVER,
    CutList,
)

# Classification palette — designed to read as "ok / careful / cut / watch".
PALETTE: dict[str, tuple[str, str]] = {
    # (face, edge)
    CLASSIFICATION_FULL:   ("#f5f0e8", "#888888"),  # warm cream
    CLASSIFICATION_EDGE:   ("#d9e6ff", "#5070a0"),  # cool blue
    CLASSIFICATION_HOLE:   ("#ffe0d0", "#cc6633"),  # peach
    CLASSIFICATION_SLIVER: ("#ffd060", "#aa7700"),  # warning yellow
}

BG_COLOR = "white"
BOUNDARY_COLOR = "#202020"
BOUNDARY_LINEWIDTH = 1.0
INTERIOR_HOLE_FACE = "white"
INTERIOR_HOLE_EDGE = "#888888"
INTERIOR_HOLE_LINEWIDTH = 0.75
LABEL_FONTSIZE_TARGET = 7

DEFAULT_DPI = 150
DEFAULT_FIG_HEIGHT_IN = 8.0


def render_cut_list_preview(
    cut_list: CutList,
    out_path: str | Path,
    *,
    boundary: list[tuple[float, float]] | None = None,
    holes: list[list[tuple[float, float]]] | None = None,
    show_legend: bool = True,
    dpi: int = DEFAULT_DPI,
) -> Path:
    """Render the cut-list preview to a PNG. Returns the written path.

    ``boundary`` and ``holes`` are optional — when supplied, the floor
    outline + hole shapes are drawn under the pieces so designers see
    them in context. Caller pulls them from the source layout's
    ``target`` block; this module stays free of layout-schema knowledge.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.patches as mpatches
    import matplotlib.pyplot as plt

    bbox = _piece_bbox(cut_list)
    bx0, by0, bx1, by1 = bbox
    w, h = bx1 - bx0, by1 - by0
    fig_w, fig_h = _figure_size(w, h)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    ax.set_facecolor(BG_COLOR)
    ax.set_axis_off()

    # 1. Holes from the target (so designers see them under any piece
    #    that intersects them on its perimeter).
    if holes:
        for hole in holes:
            ax.add_patch(mpatches.Polygon(
                hole, closed=True,
                facecolor="#ececec", edgecolor="#909090",
                linewidth=0.75, zorder=2,
            ))

    # 2. Pieces by classification.
    for piece in cut_list.pieces:
        face, edge = PALETTE.get(
            piece.classification, PALETTE[CLASSIFICATION_FULL],
        )
        ax.add_patch(mpatches.Polygon(
            piece.cut_polygon_exterior, closed=True,
            facecolor=face, edgecolor=edge,
            linewidth=0.7, zorder=3,
        ))
        # Interior holes (true internal cuts) — punched out in white.
        for ring in piece.cut_polygon_interiors:
            ax.add_patch(mpatches.Polygon(
                ring, closed=True,
                facecolor=INTERIOR_HOLE_FACE, edgecolor=INTERIOR_HOLE_EDGE,
                linewidth=INTERIOR_HOLE_LINEWIDTH, zorder=4,
            ))
        # Piece label — sized to fit comfortably in the piece bbox.
        cx, cy = _piece_centroid(piece.cut_polygon_exterior)
        label = (
            f"{piece.piece_id}\n"
            f"{piece.bounding_width_mm:.0f} × {piece.bounding_height_mm:.0f}"
        )
        font_size = _label_font_size(
            piece.bounding_width_mm, piece.bounding_height_mm,
        )
        if font_size > 0:
            ax.text(
                cx, cy, label,
                ha="center", va="center",
                fontsize=font_size, color="#202020",
                zorder=5,
            )

    # 3. Boundary on top (so the outline is crisp over the pieces).
    if boundary:
        ax.add_patch(mpatches.Polygon(
            boundary, closed=True, fill=False,
            edgecolor=BOUNDARY_COLOR, linewidth=BOUNDARY_LINEWIDTH, zorder=6,
        ))

    # 4. Legend in the upper-left of the axes area.
    if show_legend:
        _draw_legend(ax, cut_list, mpatches)

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


def _piece_bbox(cut_list: CutList) -> tuple[float, float, float, float]:
    """Bounding box of all piece exteriors (or a sensible default)."""
    xs: list[float] = []
    ys: list[float] = []
    for piece in cut_list.pieces:
        for x, y in piece.cut_polygon_exterior:
            xs.append(x)
            ys.append(y)
    if not xs:
        return 0.0, 0.0, 1.0, 1.0
    return min(xs), min(ys), max(xs), max(ys)


def _piece_centroid(coords: list[tuple[float, float]]) -> tuple[float, float]:
    """Bbox centre — good enough for the small label and avoids Shapely."""
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
    """Scale labels down for small pieces; drop entirely for tiny ones.

    Slivers are usually too small to host a legible label; we return 0
    in that case and let the colour speak for itself.
    """
    side = min(bbox_w_mm, bbox_h_mm)
    if side < 250:
        return 0.0
    if side < 500:
        return 5.0
    return LABEL_FONTSIZE_TARGET


def _draw_legend(ax, cut_list: CutList, mpatches) -> None:
    """Small colour-key in axes coordinates, top-left."""
    summary = cut_list.summary
    items = [
        (CLASSIFICATION_FULL,
         f"full ({summary.full_pieces})"),
        (CLASSIFICATION_EDGE,
         f"edge ({summary.edge_pieces})"),
        (CLASSIFICATION_HOLE,
         f"hole / internal cut ({summary.hole_pieces})"),
        (CLASSIFICATION_SLIVER,
         f"sliver ({summary.sliver_pieces})"),
    ]
    handles = [
        mpatches.Patch(
            facecolor=PALETTE[label][0], edgecolor=PALETTE[label][1],
            label=text,
        )
        for label, text in items
    ]
    ax.legend(
        handles=handles, loc="upper left",
        fontsize=7, framealpha=0.85,
        title=(
            f"Pieces: {summary.total_pieces}   "
            f"Area: {summary.total_area_m2:.2f} m²"
        ),
        title_fontsize=7,
    )
