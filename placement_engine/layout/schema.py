"""Layout-piece dataclasses + JSON serialization.

A `Piece` represents one tile-grid cell intersected with the usable
polygon. Full tiles and edge pieces share the same dataclass; the
``is_full_tile`` / ``is_edge_piece`` flags discriminate. The actual
cut polygon (exterior ring of the clipped piece) is the authoritative
geometry — nominal_* fields exist for traceability back to the grid.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from placement_engine.layout.anchoring import SliverEvaluation, SliverPolicy
from placement_engine.layout.inventory_stats import InventoryDimensionSummary
from placement_engine.layout.zoning import LayoutZone
from placement_engine.target_area.dxf_target import TargetGeometry

# Sentinel ``anchor_mode`` value for layouts whose pieces span more
# than one zone. Each zone in ``LayoutResult.zones`` then carries its
# own ``anchor_mode``; the top-level field exists for backward-compat
# JSON consumers only.
ANCHOR_PER_ZONE: str = "per_zone"
# Default zone id stamped on every piece when zoning collapsed to a
# single rectangle covering the bbox. Lets every downstream consumer
# rely on ``piece.zone_id`` being a non-empty string.
DEFAULT_ZONE_ID: str = "z0"

# Sentinel layout-basis labels surfaced in the JSON / report.
LAYOUT_BASIS_INVENTORY_MEDIAN: str = "inventory_median"
LAYOUT_BASIS_EXPLICIT: str = "explicit"


@dataclass
class Piece:
    """One layout piece — a full tile or an edge piece clipped from one."""

    piece_id: str
    row: int
    col: int
    # Nominal grid position + tile size (where this piece would sit if
    # the floor were infinite). Useful for designer traceability.
    nominal_x_mm: float
    nominal_y_mm: float
    nominal_width_mm: float
    nominal_height_mm: float
    # Authoritative geometry: exterior ring of the actual clipped piece
    # in mm coordinates. Closed (last vertex == first).
    actual_cut_polygon: list[tuple[float, float]]
    # Bbox of the actual clipped polygon.
    bounding_width_mm: float
    bounding_height_mm: float
    # Area of the clipped polygon in m² (accounts for any interior
    # holes via Shapely's signed area handling).
    actual_area_m2: float
    # Classification flags.
    is_full_tile: bool
    is_edge_piece: bool
    intersects_hole: bool
    # Inner rings of the clipped piece (mm coords, closed). Populated
    # only when a hole sits strictly interior to the tile, leaving a
    # hole inside the piece — this is the signal downstream layers
    # (cut list, fabrication) use to detect "requires an internal cut".
    # Empty for full tiles, edge clips, and edge-touching holes.
    interior_holes: list[list[tuple[float, float]]] = field(default_factory=list)
    # Free-form notes — e.g. "sliver", "split_by_hole". Designer-facing.
    notes: list[str] = field(default_factory=list)
    # The zone this piece was generated inside. Always populated by
    # the inventory-driven entry point (defaults to ``"z0"`` for
    # single-zone layouts); ``"z0"`` is also the default when a piece
    # is constructed directly via the lower-level grid generator.
    zone_id: str = DEFAULT_ZONE_ID


@dataclass
class LayoutResult:
    """A full grid layout for a single (tile_width, tile_height) run."""

    target: TargetGeometry
    tile_width_mm: float
    tile_height_mm: float
    origin: tuple[float, float]
    pieces: list[Piece]
    # How the tile size was chosen for this run:
    #   "inventory_median" — median width/height of a supplied inventory
    #   "explicit"         — caller passed tile dimensions directly
    layout_basis: str = LAYOUT_BASIS_EXPLICIT
    # When ``layout_basis == "inventory_median"``: where the inventory
    # was loaded from and what it looked like statistically. Both are
    # ``None`` for explicit runs.
    source_inventory_path: str | None = None
    inventory_dimension_summary: InventoryDimensionSummary | None = None
    # Anchor-selection trace (populated by
    # ``generate_tile_layout_from_inventory``):
    #   * ``anchor_mode`` — the winning mode (or ``"explicit_origin"``
    #     when the caller bypassed selection by passing ``origin=``).
    #     ``None`` for the bare ``generate_tile_layout`` entry point.
    #   * ``sliver_policy`` — the cuttability thresholds in force.
    #   * ``candidate_evaluations`` — every mode tried, with its
    #     scorecard and which one was selected. Empty when the
    #     anchor-selection layer wasn't invoked.
    anchor_mode: str | None = None
    sliver_policy: SliverPolicy | None = None
    candidate_evaluations: list[SliverEvaluation] = field(default_factory=list)
    # Zone decomposition trace. Populated by the inventory-driven
    # entry point; always at least one entry (the bbox) when zoning
    # ran. Empty when the lower-level ``generate_tile_layout`` was
    # called directly (no zoning step).
    #
    # Multi-zone layouts (more than one entry here):
    #   * ``anchor_mode`` above is ``"per_zone"``
    #   * top-level ``candidate_evaluations`` is empty
    #   * per-zone anchor / origin / evaluations live in each
    #     ``LayoutZone``
    # Single-zone layouts mirror the per-zone metadata into the
    # top-level fields so existing JSON consumers keep working.
    zones: list[LayoutZone] = field(default_factory=list)

    # -----------------------------------------------------------------
    # Derived metrics
    # -----------------------------------------------------------------

    @property
    def full_tile_count(self) -> int:
        return sum(1 for p in self.pieces if p.is_full_tile)

    @property
    def edge_piece_count(self) -> int:
        return sum(1 for p in self.pieces if p.is_edge_piece)

    @property
    def sliver_count(self) -> int:
        return sum(1 for p in self.pieces if "sliver" in p.notes)

    @property
    def total_actual_area_m2(self) -> float:
        return sum(p.actual_area_m2 for p in self.pieces)

    @property
    def coverage_percentage(self) -> float:
        usable = self.target.usable_area_m2
        return 100.0 * self.total_actual_area_m2 / usable if usable > 0 else 0.0

    # -----------------------------------------------------------------
    # JSON
    # -----------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        target = self.target
        return {
            "target": {
                "target_id": target.target_id,
                "name": target.name,
                "source_dxf_path": (
                    str(target.source_dxf_path) if target.source_dxf_path else None
                ),
                "bbox": list(target.bbox),
                "boundary": [list(p) for p in target.boundary],
                "holes": [[list(p) for p in hole] for hole in target.holes],
                "boundary_area_m2": round(target.boundary_area_m2, 4),
                "holes_area_m2": round(target.holes_area_m2, 4),
                "usable_area_m2": round(target.usable_area_m2, 4),
            },
            "grid": {
                "tile_width_mm": self.tile_width_mm,
                "tile_height_mm": self.tile_height_mm,
                "origin": list(self.origin),
                "layout_basis": self.layout_basis,
                "source_inventory_path": self.source_inventory_path,
                "inventory_dimension_summary": (
                    self.inventory_dimension_summary.to_dict()
                    if self.inventory_dimension_summary is not None else None
                ),
                # Anchor-selection trace. These are always emitted (as
                # ``null`` / empty list when the lower-level entry
                # point bypassed selection) so downstream consumers
                # can rely on the keys being present.
                "anchor_mode": self.anchor_mode,
                "sliver_policy": (
                    self.sliver_policy.to_dict()
                    if self.sliver_policy is not None else None
                ),
                "min_sliver_width_mm": (
                    self.sliver_policy.min_sliver_width_mm
                    if self.sliver_policy is not None else None
                ),
                "min_sliver_height_mm": (
                    self.sliver_policy.min_sliver_height_mm
                    if self.sliver_policy is not None else None
                ),
                "candidate_evaluations": [
                    ev.to_dict() for ev in self.candidate_evaluations
                ],
                # Sliver counts in candidate_evaluations describe the
                # anchor-selector's PRE-ABSORPTION view. Slivers
                # below the cuttable threshold are folded into
                # adjacent pieces by the absorption pass — survivors
                # are tagged with an "absorbed_sliver:<id>" note on
                # the holder piece in pieces[]. Pieces actually
                # exported as cuts are the rows in pieces[], never
                # the entries listed here.
                "candidate_evaluations_note": (
                    "Pre-absorption sliver counts. Final cut pieces "
                    "live in pieces[]; absorbed slivers are tagged "
                    "on their holder piece's notes[]."
                ),
                # Zone decomposition trace. Always present (empty list
                # when zoning didn't run) so consumers can rely on the
                # key existing.
                "zones": [z.to_dict() for z in self.zones],
                "zone_count": len(self.zones),
            },
            "pieces": [
                {
                    **{
                        k: v for k, v in asdict(p).items()
                        if k not in ("actual_cut_polygon", "interior_holes")
                    },
                    "actual_cut_polygon": [list(pt) for pt in p.actual_cut_polygon],
                    "interior_holes": [
                        [list(pt) for pt in ring] for ring in p.interior_holes
                    ],
                }
                for p in self.pieces
            ],
            "derived": {
                "piece_count": len(self.pieces),
                "full_tile_count": self.full_tile_count,
                "edge_piece_count": self.edge_piece_count,
                "sliver_count": self.sliver_count,
                "total_actual_area_m2": round(self.total_actual_area_m2, 4),
                "usable_area_m2": round(target.usable_area_m2, 4),
                "coverage_percentage": round(self.coverage_percentage, 2),
            },
        }


def write_layout_json(result: LayoutResult, path: str | Path) -> Path:
    """Serialize a `LayoutResult` to a JSON file. Returns the written path."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return p
