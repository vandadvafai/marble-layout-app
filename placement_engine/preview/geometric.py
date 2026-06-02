"""Geometric (CAD-style) renderer.

Clean line drawing on white: thin boundary, muted-grey holes, slab
rectangles with thin outlines, red seam lines, optional small corner
numerals, optional dimension annotations. No photos, no axes, no
gridlines.
"""

from __future__ import annotations

from pathlib import Path

from placement_engine.preview.schema import PlacementView

# Style constants — exposed so future themes can override.
BG_COLOR = "white"
BOUNDARY_COLOR = "#202020"
BOUNDARY_LINEWIDTH = 1.0
HOLE_FACE = "#e0e0e0"
HOLE_EDGE = "#909090"
HOLE_LINEWIDTH = 0.75
SLAB_FACE = "#fafafa"
SLAB_EDGE = "#808080"
SLAB_LINEWIDTH = 0.75
SEAM_COLOR = "#cc3333"
SEAM_LINEWIDTH = 0.9
LABEL_COLOR = "#606060"
LABEL_FONTSIZE = 7
DIMENSION_COLOR = "#a0a0a0"
DIMENSION_FONTSIZE = 7

DEFAULT_DPI = 150
DEFAULT_FIG_HEIGHT_IN = 8.0


def render_geometric(
    view: PlacementView,
    out_path: str | Path,
    *,
    show_labels: bool = True,
    show_dimensions: bool = False,
    dpi: int = DEFAULT_DPI,
) -> Path:
    """Render the geometric preview to a PNG.

    Returns the written path. Caller is responsible for `view` having
    been built from a packer adapter; this renderer is pure I/O over
    the view.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    bx0, by0, bx1, by1 = view.target.bbox
    w, h = bx1 - bx0, by1 - by0
    fig_w, fig_h = _figure_size(w, h)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    render_geometric_to_ax(
        ax, view,
        show_labels=show_labels,
        show_dimensions=show_dimensions,
    )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(pad=0.4)
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight", facecolor=BG_COLOR)
    plt.close(fig)
    return out_path


def render_geometric_to_ax(
    ax,
    view: PlacementView,
    *,
    show_labels: bool = True,
    show_dimensions: bool = False,
    title: str | None = None,
) -> None:
    """Render the geometric view onto a pre-existing matplotlib Axes.

    Shared by `render_geometric` (single-image output) and
    `render_geometric_comparison` (contact sheet). Doing the actual
    drawing here keeps the look identical across both entry points.
    """
    import matplotlib.patches as mpatches

    target = view.target
    bx0, by0, bx1, by1 = target.bbox
    w, h = bx1 - bx0, by1 - by0

    ax.set_facecolor(BG_COLOR)
    ax.set_axis_off()

    # 1. Holes — drawn before slabs so a slab overlapping a hole (in
    #    debug-mode of the polygon packer's bbox preview, e.g.) wins.
    for hole in target.holes:
        ax.add_patch(mpatches.Polygon(
            hole, closed=True,
            facecolor=HOLE_FACE, edgecolor=HOLE_EDGE,
            linewidth=HOLE_LINEWIDTH, zorder=2,
        ))

    # 2. Slab rectangles
    for p in view.placements:
        ax.add_patch(mpatches.Rectangle(
            (p.x_mm, p.y_mm), p.width_mm, p.height_mm,
            facecolor=SLAB_FACE, edgecolor=SLAB_EDGE,
            linewidth=SLAB_LINEWIDTH, zorder=3,
        ))

    # 3. Seam lines on top of slab outlines so they're legible.
    for s in view.seams:
        ax.plot(
            [s.x0_mm, s.x1_mm], [s.y0_mm, s.y1_mm],
            color=SEAM_COLOR, linewidth=SEAM_LINEWIDTH,
            solid_capstyle="butt", zorder=4,
        )

    # 4. Boundary on top of everything else.
    ax.add_patch(mpatches.Polygon(
        target.boundary, closed=True, fill=False,
        edgecolor=BOUNDARY_COLOR, linewidth=BOUNDARY_LINEWIDTH, zorder=5,
    ))

    # 5. Optional small corner numerals.
    if show_labels:
        offset = max(w, h) * 0.008  # ~0.8% of the longer side
        for p in view.placements:
            ax.text(
                p.x_mm + offset, p.y_mm + offset, str(p.display_index),
                fontsize=LABEL_FONTSIZE, color=LABEL_COLOR,
                ha="left", va="bottom", zorder=6,
            )

    # 6. Optional dimension text (off by default).
    if show_dimensions:
        for p in view.placements:
            ax.text(
                p.x_mm + p.width_mm * 0.5,
                p.y_mm + p.height_mm * 0.5,
                f"{p.width_mm:.0f} × {p.height_mm:.0f}",
                fontsize=DIMENSION_FONTSIZE, color=DIMENSION_COLOR,
                ha="center", va="center", zorder=6,
            )

    # Frame
    margin = max(w, h) * 0.03
    ax.set_xlim(bx0 - margin, bx1 + margin)
    ax.set_ylim(by0 - margin, by1 + margin)
    ax.set_aspect("equal")

    if title:
        ax.set_title(title, fontsize=10, color="#404040", pad=8)


def _figure_size(width_mm: float, height_mm: float) -> tuple[float, float]:
    """Pick a figure size that respects the target aspect ratio.

    Clamps to avoid extreme-long-thin figures (e.g. 18 m corridor).
    """
    aspect = width_mm / height_mm if height_mm > 0 else 1.0
    fig_h = DEFAULT_FIG_HEIGHT_IN
    fig_w = max(6.0, min(16.0, fig_h * aspect))
    return fig_w, fig_h
