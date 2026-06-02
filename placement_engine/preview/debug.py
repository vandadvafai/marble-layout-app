"""Debug renderer — engineer-facing matplotlib view.

This is the old "everything-on" preview promoted to opt-in: axes,
gridlines, multi-line title with metrics, bbox dashed guide,
full slab IDs in black pills, rejection ghosts with reason badges,
red-hatched holes. Don't use this for designer-facing output.
"""

from __future__ import annotations

import logging
from pathlib import Path

from placement_engine.preview.schema import PlacementView

logger = logging.getLogger(__name__)


def render_debug(
    view: PlacementView,
    out_path: str | Path,
    *,
    dpi: int = 110,
) -> Path:
    """Render the debug preview to a PNG."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.image as mpimg
    import matplotlib.patches as mpatches
    import matplotlib.pyplot as plt

    target = view.target
    bx0, by0, bx1, by1 = target.bbox
    w, h = bx1 - bx0, by1 - by0

    aspect = w / h if h > 0 else 1.0
    fig_h = 9.0
    fig_w = max(8.0, min(16.0, fig_h * aspect))
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))

    # Bbox dashed guide.
    ax.add_patch(mpatches.Rectangle(
        (bx0, by0), w, h, fill=False,
        edgecolor="#888888", linestyle="--", linewidth=1.0, zorder=2,
    ))

    # Slab photos (or placeholder hatches).
    for p in view.placements:
        image_drawn = False
        if p.image_source != "placeholder" and p.image_path:
            try:
                img = mpimg.imread(p.image_path)
                ax.imshow(
                    img,
                    extent=(p.x_mm, p.x_mm + p.width_mm, p.y_mm, p.y_mm + p.height_mm),
                    aspect="auto", origin="upper", zorder=3,
                )
                image_drawn = True
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not load image %s: %s", p.image_path, exc)
        if not image_drawn:
            ax.add_patch(mpatches.Rectangle(
                (p.x_mm, p.y_mm), p.width_mm, p.height_mm,
                facecolor="#dcdcdc", edgecolor="#888888",
                hatch="//", linewidth=0.8, zorder=3,
            ))
            ax.text(
                p.x_mm + p.width_mm * 0.5, p.y_mm + p.height_mm * 0.85,
                "[no image]", ha="center", va="center",
                color="#444444", fontsize=8, fontstyle="italic", zorder=5,
            )
        # White outline + black-pill slab_id label.
        ax.add_patch(mpatches.Rectangle(
            (p.x_mm, p.y_mm), p.width_mm, p.height_mm, fill=False,
            edgecolor="white" if image_drawn else "#666666",
            linewidth=1.6, zorder=4,
        ))
        ax.text(
            p.x_mm + p.width_mm * 0.5, p.y_mm + p.height_mm * 0.5,
            p.slab_id, ha="center", va="center",
            fontsize=8, color="white",
            bbox=dict(boxstyle="round,pad=0.25", facecolor="black", alpha=0.55),
            zorder=5,
        )

    # Seams in debug-red, full opacity.
    for s in view.seams:
        ax.plot(
            [s.x0_mm, s.x1_mm], [s.y0_mm, s.y1_mm],
            color="#cc0000", linewidth=1.2, zorder=5,
        )

    # Holes — loud red-hatched.
    for hole in target.holes:
        ax.add_patch(mpatches.Polygon(
            hole, closed=True, facecolor="#ffe6e6",
            edgecolor="#cc3333", hatch="xxx",
            linewidth=1.5, alpha=0.85, zorder=6,
        ))

    # Rejection ghosts.
    for r in view.rejected:
        if r.attempted_x_mm is None or r.attempted_y_mm is None:
            continue
        ax.add_patch(mpatches.Rectangle(
            (r.attempted_x_mm, r.attempted_y_mm), r.width_mm, r.height_mm,
            fill=False, edgecolor="#ff8800",
            linestyle=":", linewidth=1.2, zorder=4,
        ))
        ax.text(
            r.attempted_x_mm + r.width_mm * 0.5,
            r.attempted_y_mm + r.height_mm * 0.5,
            f"{r.slab_id}\n[{r.reason}]",
            ha="center", va="center", color="#cc4400",
            fontsize=7, fontstyle="italic",
            bbox=dict(
                boxstyle="round,pad=0.2",
                facecolor="#fff4e6", edgecolor="#ff8800", alpha=0.85,
            ),
            zorder=5,
        )

    # Thick boundary on top.
    ax.add_patch(mpatches.Polygon(
        target.boundary, closed=True, fill=False,
        edgecolor="black", linewidth=2.5, zorder=7,
    ))

    margin = max(w, h) * 0.05
    ax.set_xlim(bx0 - margin, bx1 + margin)
    ax.set_ylim(by0 - margin, by1 + margin)
    ax.set_aspect("equal")
    ax.set_xlabel("x (mm)")
    ax.set_ylabel("y (mm)")
    ax.grid(True, linestyle=":", color="#cccccc", linewidth=0.5)

    packer = view.metadata.get("packer", "(unknown)")
    ax.set_title(
        f"DEBUG — {target.name}  ({packer})\n"
        f"{view.placed_count} placed, {view.rejected_count} rejected, "
        f"{view.coverage_percentage:.1f}% coverage "
        f"({view.placed_area_m2:.2f} / {target.usable_area_m2:.2f} m² usable)"
    )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out_path
