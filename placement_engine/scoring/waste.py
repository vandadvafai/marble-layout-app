"""Basic area / waste metrics for a layout option.

Only the metrics we can compute reliably from MVP geometry are populated
here. Seam metrics, complexity scores, and risk flags will plug in via
their own modules and merge into `LayoutMetrics` in the engine.
"""

from __future__ import annotations

from shapely.geometry import Polygon

from placement_engine.models import LayoutMetrics, PlacedPiece, Slab


def _piece_area(piece: PlacedPiece) -> float:
    return Polygon(piece.project_polygon).area


def compute_basic_metrics(
    project: Polygon,
    pieces: list[PlacedPiece],
    slabs: list[Slab],
) -> LayoutMetrics:
    installed_area = sum(_piece_area(p) for p in pieces)

    used_slab_ids = {p.slab_id for p in pieces}
    slab_lookup = {s.slab_id: s for s in slabs}
    # MVP convention: a slab is "consumed" the moment any piece is cut from
    # it; offcut reuse is disabled. Total slab area used = sum of full
    # areas of every slab that contributed at least one piece.
    total_slab_area_used = sum(
        slab_lookup[sid].width * slab_lookup[sid].height for sid in used_slab_ids
    )

    waste_area = max(0.0, total_slab_area_used - installed_area)
    waste_pct = (waste_area / total_slab_area_used * 100.0) if total_slab_area_used else 0.0

    return LayoutMetrics(
        installed_area=round(installed_area, 2),
        total_slab_area_used=round(total_slab_area_used, 2),
        waste_area=round(waste_area, 2),
        waste_percentage=round(waste_pct, 2),
        reusable_offcut_area=0.0,
        non_reusable_waste_area=round(waste_area, 2),
        piece_count=len(pieces),
        slabs_used=len(used_slab_ids),
        cut_count_estimate=0,
        seam_count=0,
        total_seam_length=0.0,
        small_piece_count=0,
        cutting_complexity_score=1,
        estimated_production_difficulty="low",
    )


def project_area(project: Polygon) -> float:
    """Convenience: the polygon's installable area in mm²."""
    return float(project.area)
