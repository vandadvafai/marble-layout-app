"""Matplotlib debug plot for a generated layout.

Produces a PNG that shows:
  - the project boundary (thick black line)
  - holes (hatched grey)
  - placed pieces, each filled with a colour keyed off its slab_id and
    labelled with `piece_id ← slab_id`
  - the project bounding box for reference (thin grey)

This is a debug tool, not a designer-facing render. The aim is to confirm
the geometry is valid before Blender integration exists.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless: no display required
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
from matplotlib.patches import PathPatch
from matplotlib.path import Path as MplPath

from placement_engine.models import EngineOutput, ProjectInput


# A small, readable categorical palette. Cycles if there are more slabs than
# colours — fine for MVP debug visuals.
_PALETTE = [
    "#4C72B0", "#DD8452", "#55A467", "#C44E52", "#8172B3",
    "#937860", "#DA8BC3", "#8C8C8C", "#CCB974", "#64B5CD",
]


def _slab_colour(slab_id: str, slab_order: list[str]) -> str:
    idx = slab_order.index(slab_id) if slab_id in slab_order else 0
    return _PALETTE[idx % len(_PALETTE)]


def _polygon_patch(coords: list[tuple[float, float]], **kwargs) -> PathPatch:
    """Build a closed-polygon PathPatch from JSON-style coordinates."""
    verts = list(coords) + [coords[0]]
    codes = [MplPath.MOVETO] + [MplPath.LINETO] * (len(coords) - 1) + [MplPath.CLOSEPOLY]
    return PathPatch(MplPath(verts, codes), **kwargs)


def render_layout(
    project_input: ProjectInput,
    output: EngineOutput,
    target: str | Path,
    option_index: int = 0,
) -> Path:
    """Render one layout option to PNG and return the written path."""
    target_path = Path(target)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    option = output.layout_options[option_index]
    slab_order = [s.slab_id for s in project_input.slabs]

    fig, ax = plt.subplots(figsize=(12, 8))

    # Pieces first so the boundary outline overdraws their edges cleanly.
    from shapely.geometry import Polygon  # already a dependency

    for piece in option.placed_pieces:
        colour = _slab_colour(piece.slab_id, slab_order)
        # Pieces with any risk flag get a red outline so they stand out.
        is_risky = bool(piece.risk_flags)
        edge_colour = "#d62728" if is_risky else "white"
        edge_width = 2.2 if is_risky else 1.2
        ax.add_patch(
            _polygon_patch(
                piece.project_polygon,
                facecolor=colour,
                edgecolor=edge_colour,
                linewidth=edge_width,
                alpha=0.85,
            )
        )
        # Label at the polygon centroid.
        c = Polygon(piece.project_polygon).centroid
        ax.text(
            c.x, c.y,
            f"{piece.piece_id}\n{piece.slab_id}",
            ha="center", va="center", fontsize=8, color="white",
            weight="bold",
        )

    # Review markers: a small ring at each marker location so the designer
    # sees where the engine wants attention, even when the piece itself is
    # invisible (e.g. an empty-placement skip outside the project area).
    for marker in option.review_markers:
        mx, my = marker.location
        ax.plot(
            mx, my, marker="o", markersize=10,
            markerfacecolor="none", markeredgecolor="#d62728",
            markeredgewidth=1.8, zorder=5,
        )

    # Holes drawn as hatched grey on top of pieces (in case any clipping bug
    # leaves coverage where it shouldn't).
    for hole in project_input.layout.holes:
        ax.add_patch(
            _polygon_patch(
                hole,
                facecolor="#dddddd",
                edgecolor="#666666",
                hatch="///",
                linewidth=1.0,
            )
        )

    # Project boundary on top, no fill.
    ax.add_patch(
        _polygon_patch(
            project_input.layout.boundary,
            facecolor="none",
            edgecolor="black",
            linewidth=2.5,
        )
    )

    # Per-slab legend.
    used_slabs = sorted({p.slab_id for p in option.placed_pieces}, key=slab_order.index)
    handles = [
        mpatches.Patch(color=_slab_colour(sid, slab_order), label=sid)
        for sid in used_slabs
    ]
    if handles:
        ax.legend(
            handles=handles, title="Slabs", loc="center left",
            bbox_to_anchor=(1.02, 0.5), frameon=False, fontsize=9,
        )

    # Title carries the headline metrics so the PNG is useful on its own.
    m = option.metrics
    ax.set_title(
        f"{project_input.project_id}  |  strategy={option.strategy}  |  "
        f"pieces={m.piece_count}  slabs={m.slabs_used}  "
        f"waste={m.waste_percentage:.1f}%",
        fontsize=11,
    )
    ax.set_xlabel("x (mm)")
    ax.set_ylabel("y (mm)")
    ax.set_aspect("equal", adjustable="box")
    ax.grid(True, linestyle=":", linewidth=0.5, alpha=0.5)
    ax.autoscale_view()
    fig.tight_layout()
    fig.savefig(target_path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    return target_path
