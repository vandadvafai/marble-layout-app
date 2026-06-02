"""Textured (photo-composite) renderer.

White background, thin dark-grey boundary, real cropped slab photos
clipped to the boundary polygon, hairline grey seams, holes "punched
out" white. No labels by default; opt-in adds small corner numerals.
No axes, no gridlines, no debug overlays.
"""

from __future__ import annotations

import logging
from pathlib import Path

from placement_engine.preview.schema import PlacementView

logger = logging.getLogger(__name__)

# Style constants
BG_COLOR = "white"
BOUNDARY_COLOR = "#303030"
BOUNDARY_LINEWIDTH = 0.9
HOLE_FACE = "white"
HOLE_EDGE = "#888888"
HOLE_LINEWIDTH = 0.6
SEAM_COLOR = "#888888"
SEAM_LINEWIDTH = 0.4
SEAM_ALPHA = 0.6
PLACEHOLDER_FACE = "#ececec"
LABEL_BG = "#404040"
LABEL_FG = "white"
LABEL_FONTSIZE = 7

DEFAULT_DPI = 150
DEFAULT_FIG_HEIGHT_IN = 8.0


def render_textured(
    view: PlacementView,
    out_path: str | Path,
    *,
    show_labels: bool = False,
    dpi: int = DEFAULT_DPI,
) -> Path:
    """Render the textured preview to a PNG. Returns the written path."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    bx0, by0, bx1, by1 = view.target.bbox
    w, h = bx1 - bx0, by1 - by0
    fig_w, fig_h = _figure_size(w, h)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    render_textured_to_ax(ax, view, show_labels=show_labels)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(pad=0.4)
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight", facecolor=BG_COLOR)
    plt.close(fig)
    return out_path


def render_textured_to_ax(
    ax,
    view: PlacementView,
    *,
    show_labels: bool = False,
    title: str | None = None,
) -> None:
    """Render the textured view onto a pre-existing matplotlib Axes."""
    import matplotlib.image as mpimg
    import matplotlib.patches as mpatches
    from matplotlib.path import Path as MplPath
    from matplotlib.patches import PathPatch

    target = view.target
    bx0, by0, bx1, by1 = target.bbox
    w, h = bx1 - bx0, by1 - by0

    ax.set_facecolor(BG_COLOR)
    ax.set_axis_off()

    # Build a clip path matching the boundary polygon. Anything we draw
    # (photos, placeholder fills) gets clipped to this so it doesn't
    # leak outside the irregular floor outline. The clip patch itself
    # is invisible.
    boundary_verts = list(target.boundary) + [target.boundary[0]]
    boundary_codes = (
        [MplPath.MOVETO]
        + [MplPath.LINETO] * (len(target.boundary) - 1)
        + [MplPath.CLOSEPOLY]
    )
    clip_path = MplPath(boundary_verts, boundary_codes)
    clip_patch = PathPatch(
        clip_path, transform=ax.transData,
        facecolor="none", edgecolor="none",
    )
    ax.add_patch(clip_patch)

    # 1. Slab photos (or placeholder fills). Each is clipped to the
    #    boundary so bbox-mode placements that hang off the irregular
    #    boundary get visually trimmed.
    for p in view.placements:
        artist = None
        if p.image_source != "placeholder" and p.image_path:
            try:
                img = mpimg.imread(p.image_path)
                artist = ax.imshow(
                    img,
                    extent=(p.x_mm, p.x_mm + p.width_mm, p.y_mm, p.y_mm + p.height_mm),
                    aspect="auto", origin="upper", zorder=2,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not load image %s: %s", p.image_path, exc)
                artist = None
        if artist is None:
            # Subtle placeholder fill — no hatch, no border in textured mode.
            artist = mpatches.Rectangle(
                (p.x_mm, p.y_mm), p.width_mm, p.height_mm,
                facecolor=PLACEHOLDER_FACE, edgecolor="none", zorder=2,
            )
            ax.add_patch(artist)
        artist.set_clip_path(clip_patch)

    # 2. Hairline seams.
    for s in view.seams:
        ax.plot(
            [s.x0_mm, s.x1_mm], [s.y0_mm, s.y1_mm],
            color=SEAM_COLOR, linewidth=SEAM_LINEWIDTH, alpha=SEAM_ALPHA,
            solid_capstyle="butt", zorder=3,
        )

    # 3. Holes — "punched out" white fills with a thin grey outline.
    #    Drawn on top of photos so any portion of a photo overlapping a
    #    hole is covered.
    for hole in target.holes:
        ax.add_patch(mpatches.Polygon(
            hole, closed=True,
            facecolor=HOLE_FACE, edgecolor=HOLE_EDGE,
            linewidth=HOLE_LINEWIDTH, zorder=4,
        ))

    # 4. Boundary on top.
    ax.add_patch(mpatches.Polygon(
        target.boundary, closed=True, fill=False,
        edgecolor=BOUNDARY_COLOR, linewidth=BOUNDARY_LINEWIDTH, zorder=5,
    ))

    # 5. Optional small corner numerals.
    if show_labels:
        offset = max(w, h) * 0.012
        for p in view.placements:
            ax.text(
                p.x_mm + offset, p.y_mm + offset, str(p.display_index),
                fontsize=LABEL_FONTSIZE, color=LABEL_FG,
                ha="left", va="bottom", zorder=6,
                bbox=dict(
                    boxstyle="round,pad=0.15",
                    facecolor=LABEL_BG, alpha=0.65, edgecolor="none",
                ),
            )

    # Frame
    margin = max(w, h) * 0.03
    ax.set_xlim(bx0 - margin, bx1 + margin)
    ax.set_ylim(by0 - margin, by1 + margin)
    ax.set_aspect("equal")

    if title:
        ax.set_title(title, fontsize=10, color="#404040", pad=8)


def _figure_size(width_mm: float, height_mm: float) -> tuple[float, float]:
    aspect = width_mm / height_mm if height_mm > 0 else 1.0
    fig_h = DEFAULT_FIG_HEIGHT_IN
    fig_w = max(6.0, min(16.0, fig_h * aspect))
    return fig_w, fig_h
