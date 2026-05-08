"""Area, waste, and coverage metrics for a layout option.

Two distinct views of "how good is this layout":

  * **Slab-usage view** (`waste_area`, `waste_percentage`): of the slabs
    that were consumed, how much of their material is unused offcut?
  * **Project-coverage view** (`installed_area`, `uncovered_area`,
    `coverage_percentage`): of the floor that should have been clad,
    how much actually is?

These can disagree. A layout that uses two slabs perfectly but only
covers 12 % of the project has `waste_percentage = 0` *and*
`coverage_percentage = 12`. The `layout_status` and `inventory_status`
flags exist so a caller doesn't have to compare percentages itself to
spot that.

Seam metrics, complexity scoring, and risk flagging plug in via their
own modules and are merged by the engine.
"""

from __future__ import annotations

from shapely.geometry import Polygon

from placement_engine.config import AREA_EPSILON_MM2
from placement_engine.models import LayoutMetrics, PlacedPiece, Slab


def _piece_area(piece: PlacedPiece) -> float:
    return Polygon(piece.project_polygon).area


def project_area(project: Polygon) -> float:
    """Convenience: the polygon's installable area in mm²."""
    return float(project.area)


def _layout_status(installed_area: float, uncovered_area: float) -> str:
    """`failed` if nothing was placed, `complete` if the project is
    fully covered (within floating-point tolerance), `partial` otherwise."""
    if installed_area <= AREA_EPSILON_MM2:
        return "failed"
    if uncovered_area <= AREA_EPSILON_MM2:
        return "complete"
    return "partial"


def _inventory_status(
    layout_status: str, slabs_used: int, slab_inventory_size: int
) -> str:
    """`sufficient` whenever the project is fully covered; `insufficient`
    if every input slab contributed and there is still uncovered area;
    `unknown` if coverage is incomplete but slabs remain unused (the
    engine had material left but the strategy couldn't place it).
    """
    if layout_status == "complete":
        return "sufficient"
    if slabs_used >= slab_inventory_size:
        return "insufficient"
    return "unknown"


def compute_basic_metrics(
    project: Polygon,
    pieces: list[PlacedPiece],
    slabs: list[Slab],
) -> LayoutMetrics:
    project_usable_area = float(project.area)
    installed_area = sum(_piece_area(p) for p in pieces)
    uncovered_area = max(0.0, project_usable_area - installed_area)
    coverage_pct = (
        installed_area / project_usable_area * 100.0
        if project_usable_area
        else 0.0
    )

    used_slab_ids = {p.slab_id for p in pieces}
    slab_lookup = {s.slab_id: s for s in slabs}
    # MVP convention: a slab is "consumed" the moment any piece is cut
    # from it; offcut reuse is disabled. Total slab area used = sum of
    # full areas of every slab that contributed at least one piece.
    total_slab_area_used = sum(
        slab_lookup[sid].width * slab_lookup[sid].height for sid in used_slab_ids
    )
    waste_area = max(0.0, total_slab_area_used - installed_area)
    waste_pct = (
        waste_area / total_slab_area_used * 100.0
        if total_slab_area_used
        else 0.0
    )

    layout_status = _layout_status(installed_area, uncovered_area)
    inventory_status = _inventory_status(
        layout_status, len(used_slab_ids), len(slabs)
    )

    return LayoutMetrics(
        project_usable_area=round(project_usable_area, 2),
        installed_area=round(installed_area, 2),
        uncovered_area=round(uncovered_area, 2),
        coverage_percentage=round(coverage_pct, 2),
        total_slab_area_used=round(total_slab_area_used, 2),
        waste_area=round(waste_area, 2),
        waste_percentage=round(waste_pct, 2),
        reusable_offcut_area=0.0,
        non_reusable_waste_area=round(waste_area, 2),
        piece_count=len(pieces),
        slabs_used=len(used_slab_ids),
        seam_count=0,
        total_seam_length=0.0,
        small_piece_count=0,
        layout_status=layout_status,
        inventory_status=inventory_status,
        cut_count_estimate=0,
        cutting_complexity_score=1,
        estimated_production_difficulty="low",
    )
