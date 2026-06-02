"""Side-by-side contact-sheet renderers for multi-packer comparison.

Each rendered packer view becomes one panel in a 1×N grid. Per the
V1 contract there is exactly one contact sheet per mode (geometric and
textured); debug stays per-packer because it's already noisy enough.
"""

from __future__ import annotations

from pathlib import Path

from placement_engine.preview.geometric import (
    BG_COLOR as GEOMETRIC_BG,
    render_geometric_to_ax,
)
from placement_engine.preview.schema import PlacementView
from placement_engine.preview.textured import (
    BG_COLOR as TEXTURED_BG,
    render_textured_to_ax,
)

DEFAULT_DPI = 150
# Fixed square panel size per packer. The inner ``ax.set_aspect("equal")``
# preserves correct slab proportions inside the panel; the surrounding
# panel does NOT stretch to the target aspect, so a 12 × 8 m apartment
# and a 6 × 4 m rectangle render at comparable visual sizes in the
# contact sheet. Wide targets (e.g. 8 × 4 m L-shape) get vertical
# whitespace padding within their panel instead of stretching the
# whole sheet to ~5:1.
PANEL_SIDE_IN = 6.0


def render_geometric_comparison(
    views: dict[str, PlacementView],
    out_path: str | Path,
    *,
    show_labels: bool = True,
    show_dimensions: bool = False,
    dpi: int = DEFAULT_DPI,
) -> Path:
    """One PNG, one geometric panel per packer (side-by-side)."""
    return _render_contact_sheet(
        views, out_path,
        render_to_ax=lambda ax, view, title: render_geometric_to_ax(
            ax, view,
            show_labels=show_labels,
            show_dimensions=show_dimensions,
            title=title,
        ),
        bg=GEOMETRIC_BG,
        dpi=dpi,
    )


def render_textured_comparison(
    views: dict[str, PlacementView],
    out_path: str | Path,
    *,
    show_labels: bool = False,
    dpi: int = DEFAULT_DPI,
) -> Path:
    """One PNG, one textured panel per packer (side-by-side)."""
    return _render_contact_sheet(
        views, out_path,
        render_to_ax=lambda ax, view, title: render_textured_to_ax(
            ax, view, show_labels=show_labels, title=title,
        ),
        bg=TEXTURED_BG,
        dpi=dpi,
    )


def _render_contact_sheet(
    views: dict[str, PlacementView],
    out_path: str | Path,
    *,
    render_to_ax,
    bg: str,
    dpi: int,
) -> Path:
    """Shared implementation: lay out N panels horizontally."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if not views:
        raise ValueError("render contact sheet requires at least one view")

    n = len(views)
    fig_w = PANEL_SIDE_IN * n
    fig_h = PANEL_SIDE_IN
    fig, axes = plt.subplots(1, n, figsize=(fig_w, fig_h))
    if n == 1:
        axes = [axes]

    for ax, (name, view) in zip(axes, views.items()):
        sub = (
            f"{name}\n"
            f"{view.placed_count} placed, {view.rejected_count} rejected, "
            f"{view.coverage_percentage:.1f}%"
        )
        render_to_ax(ax, view, sub)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(pad=0.5)
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight", facecolor=bg)
    plt.close(fig)
    return out_path
