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

from placement_engine.layout.inventory_stats import InventoryDimensionSummary
from placement_engine.target_area.dxf_target import TargetGeometry

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
