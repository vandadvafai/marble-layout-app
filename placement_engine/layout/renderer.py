"""Clean geometric preview for a `LayoutResult`.

CAD-style plan view: thin boundary, muted-grey holes, full tiles in a
light fill, edge pieces in a slightly darker fill so the difference is
visible at a glance. Optional small corner numerals. No photos, no
debug overlays, no slab references.
"""

from __future__ import annotations

from pathlib import Path

from placement_engine.layout.schema import LayoutResult

# Style constants
BG_COLOR = "white"
BOUNDARY_COLOR = "#202020"
BOUNDARY_LINEWIDTH = 1.0
HOLE_FACE = "#e0e0e0"
HOLE_EDGE = "#909090"
HOLE_LINEWIDTH = 0.75
FULL_TILE_FACE = "#fafafa"
FULL_TILE_EDGE = "#808080"
FULL_TILE_LINEWIDTH = 0.6
EDGE_PIECE_FACE = "#eef1f6"  # slightly cooler grey-blue
EDGE_PIECE_EDGE = "#5a7090"
EDGE_PIECE_LINEWIDTH = 0.6
SLIVER_FACE = "#ffe9d8"
SLIVER_EDGE = "#cc7733"
LABEL_COLOR = "#606060"
LABEL_FONTSIZE = 6

DEFAULT_DPI = 150
DEFAULT_FIG_HEIGHT_IN = 8.0


def render_layout_geometric(
    result: LayoutResult,
    out_path: str | Path,
    *,
    show_labels: bool = False,
    dpi: int = DEFAULT_DPI,
) -> Path:
    """Render a clean geometric preview of a layout to a PNG.

    ``show_labels`` enables small corner numerals (1, 2, 3...) per
    piece. Off by default to keep the preview uncluttered; turn on for
    designer review of specific cuts.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.patches as mpatches
    import matplotlib.pyplot as plt

    target = result.target
    bx0, by0, bx1, by1 = target.bbox
    w, h = bx1 - bx0, by1 - by0
    fig_w, fig_h = _figure_size(w, h)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))

    ax.set_facecolor(BG_COLOR)
    ax.set_axis_off()

    # 1. Holes — drawn first so anything above sits on top.
    for hole in target.holes:
        ax.add_patch(mpatches.Polygon(
            hole, closed=True,
            facecolor=HOLE_FACE, edgecolor=HOLE_EDGE,
            linewidth=HOLE_LINEWIDTH, zorder=2,
        ))

    # 2. Pieces — full tiles first, edge pieces on top (they're rarer
    #    and need to be visible). Slivers get their own treatment so
    #    designers spot them.
    for piece in result.pieces:
        if piece.is_full_tile:
            face, edge = FULL_TILE_FACE, FULL_TILE_EDGE
            z = 3
        elif "sliver" in piece.notes:
            face, edge = SLIVER_FACE, SLIVER_EDGE
            z = 5
        else:
            face, edge = EDGE_PIECE_FACE, EDGE_PIECE_EDGE
            z = 4
        ax.add_patch(mpatches.Polygon(
            piece.actual_cut_polygon, closed=True,
            facecolor=face, edgecolor=edge,
            linewidth=FULL_TILE_LINEWIDTH if piece.is_full_tile else EDGE_PIECE_LINEWIDTH,
            zorder=z,
        ))

    # 3. Boundary on top.
    ax.add_patch(mpatches.Polygon(
        target.boundary, closed=True, fill=False,
        edgecolor=BOUNDARY_COLOR, linewidth=BOUNDARY_LINEWIDTH, zorder=6,
    ))

    # 4. Optional small piece numerals (1-based) in the piece centroid.
    if show_labels:
        for i, piece in enumerate(result.pieces, start=1):
            cx, cy = _polygon_centroid(piece.actual_cut_polygon)
            ax.text(
                cx, cy, str(i),
                fontsize=LABEL_FONTSIZE, color=LABEL_COLOR,
                ha="center", va="center", zorder=7,
            )

    margin = max(w, h) * 0.03
    ax.set_xlim(bx0 - margin, bx1 + margin)
    ax.set_ylim(by0 - margin, by1 + margin)
    ax.set_aspect("equal")

    # Surface the chosen anchor mode in the figure title — designers
    # need to see which direction the auto-selector picked, and why
    # the slivers (if any) landed where they did.
    title = _build_title(result)
    if title:
        fig.suptitle(title, fontsize=9, y=0.995)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(pad=0.4, rect=(0.0, 0.0, 1.0, 0.97 if title else 1.0))
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight", facecolor=BG_COLOR)
    plt.close(fig)
    return out_path


def _build_title(result: LayoutResult) -> str:
    """One-line plot title: tile size, anchor mode, sliver count.

    Empty string when the layout was built without anchor selection
    (lower-level ``generate_tile_layout`` entry point) — keeps the
    figure clean for callers that don't go through the selector.
    """
    pieces = []
    pieces.append(
        f"{result.target.name or result.target.target_id}"
    )
    pieces.append(
        f"tile {int(result.tile_width_mm)}×{int(result.tile_height_mm)} mm"
    )
    if result.anchor_mode:
        pieces.append(f"anchor {result.anchor_mode}")
    slivers = result.sliver_count
    if slivers:
        pieces.append(f"{slivers} sliver(s)")
    return "   ·   ".join(pieces)


def _figure_size(width_mm: float, height_mm: float) -> tuple[float, float]:
    aspect = width_mm / height_mm if height_mm > 0 else 1.0
    fig_h = DEFAULT_FIG_HEIGHT_IN
    fig_w = max(6.0, min(16.0, fig_h * aspect))
    return fig_w, fig_h


def _polygon_centroid(coords: list[tuple[float, float]]) -> tuple[float, float]:
    """Cheap centroid for a small label. Uses the bbox center; that
    placement is good enough for the small piece numerals and avoids a
    Shapely dependency in the renderer module.
    """
    if not coords:
        return 0.0, 0.0
    xs = [x for x, _ in coords]
    ys = [y for _, y in coords]
    return (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
