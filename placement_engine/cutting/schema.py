"""Dataclasses + JSON I/O for the V1 cutting plan.

Schema is intentionally narrow so fabrication consumers can round-trip
without surprise. Areas are kept in m² for readability (matching the
rest of the engine); coordinates and dimensions stay in mm.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Outcome labels. Mirror the assignment layer so existing tooling can
# group records the same way.
CUTTING_ASSIGNED: str = "assigned"
CUTTING_UNASSIGNED: str = "unassigned"

# Unassigned reasons — same vocabulary as the assignment layer so a
# fabricator looking at both reports sees the same words.
CUTTING_UNASSIGNED_NO_SLAB_FITS: str = "no_slab_fits"
CUTTING_UNASSIGNED_ALL_FITTING_USED: str = "all_fitting_slabs_used"


@dataclass
class CutPlacement:
    """One cut-list piece placed on one slab.

    ``x_mm`` / ``y_mm`` are the bottom-left corner of the piece's
    bounding box in the slab's local coordinate frame (origin at the
    bottom-left of the slab). The piece itself is laid out axis-
    aligned at its bounding-box orientation — no rotation in V1.
    """

    cut_piece_id: str
    source_layout_piece_id: str
    slab_id: str
    x_mm: float
    y_mm: float
    width_mm: float
    height_mm: float
    area_m2: float
    classification: str


@dataclass
class Offcut:
    """A remaining usable rectangle inside a slab after one or more cuts."""

    x_mm: float
    y_mm: float
    width_mm: float
    height_mm: float
    area_m2: float


@dataclass
class CuttingSlab:
    """A physical slab the planner consumed, with everything it contains."""

    slab_id: str
    original_width_mm: float
    original_height_mm: float
    original_area_m2: float
    used_area_m2: float
    waste_area_m2: float
    placements: list[CutPlacement] = field(default_factory=list)
    offcuts: list[Offcut] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "slab_id": self.slab_id,
            "original_width_mm": self.original_width_mm,
            "original_height_mm": self.original_height_mm,
            "original_area_m2": round(self.original_area_m2, 6),
            "used_area_m2": round(self.used_area_m2, 6),
            "waste_area_m2": round(self.waste_area_m2, 6),
            "placements": [_placement_to_dict(p) for p in self.placements],
            "offcuts": [_offcut_to_dict(o) for o in self.offcuts],
        }


@dataclass
class UnassignedPiece:
    """A cut-list piece the planner could not place on any slab."""

    cut_piece_id: str
    source_layout_piece_id: str
    classification: str
    width_mm: float
    height_mm: float
    area_m2: float
    reason: str  # CUTTING_UNASSIGNED_NO_SLAB_FITS / *_ALL_FITTING_USED

    def to_dict(self) -> dict[str, Any]:
        return {
            "cut_piece_id": self.cut_piece_id,
            "source_layout_piece_id": self.source_layout_piece_id,
            "classification": self.classification,
            "width_mm": self.width_mm,
            "height_mm": self.height_mm,
            "area_m2": round(self.area_m2, 6),
            "reason": self.reason,
        }


@dataclass
class CuttingSummary:
    """Aggregate counts + areas — fabrication's one-pager."""

    total_cut_pieces: int
    assigned_cut_pieces: int
    unassigned_cut_pieces: int
    total_slab_area_m2: float       # surface area of slabs the planner consumed
    used_cut_area_m2: float         # sum of placed piece areas
    estimated_waste_m2: float       # used-slab area - placed piece area
    slabs_used: int
    unused_slabs: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cut_pieces": self.total_cut_pieces,
            "assigned_cut_pieces": self.assigned_cut_pieces,
            "unassigned_cut_pieces": self.unassigned_cut_pieces,
            "total_slab_area_m2": round(self.total_slab_area_m2, 4),
            "used_cut_area_m2": round(self.used_cut_area_m2, 4),
            "estimated_waste_m2": round(self.estimated_waste_m2, 4),
            "slabs_used": self.slabs_used,
            "unused_slabs": self.unused_slabs,
        }


@dataclass
class CuttingPlan:
    """Top-level cutting plan — everything fabrication needs to cut."""

    source_cut_list_path: str
    source_inventory_path: str
    target_id: str
    target_name: str
    slabs: list[CuttingSlab] = field(default_factory=list)
    unassigned: list[UnassignedPiece] = field(default_factory=list)
    unused_slab_ids: list[str] = field(default_factory=list)

    @property
    def summary(self) -> CuttingSummary:
        assigned = sum(len(s.placements) for s in self.slabs)
        used_area = sum(s.used_area_m2 for s in self.slabs)
        slab_area = sum(s.original_area_m2 for s in self.slabs)
        waste = sum(s.waste_area_m2 for s in self.slabs)
        return CuttingSummary(
            total_cut_pieces=assigned + len(self.unassigned),
            assigned_cut_pieces=assigned,
            unassigned_cut_pieces=len(self.unassigned),
            total_slab_area_m2=slab_area,
            used_cut_area_m2=used_area,
            estimated_waste_m2=waste,
            slabs_used=len(self.slabs),
            unused_slabs=len(self.unused_slab_ids),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_cut_list_path": self.source_cut_list_path,
            "source_inventory_path": self.source_inventory_path,
            "target": {"target_id": self.target_id, "name": self.target_name},
            "slabs": [s.to_dict() for s in self.slabs],
            "unassigned": [u.to_dict() for u in self.unassigned],
            "unused_slab_ids": list(self.unused_slab_ids),
            "summary": self.summary.to_dict(),
        }


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------


def _placement_to_dict(p: CutPlacement) -> dict[str, Any]:
    return {
        "cut_piece_id": p.cut_piece_id,
        "source_layout_piece_id": p.source_layout_piece_id,
        "slab_id": p.slab_id,
        "x_mm": round(p.x_mm, 3),
        "y_mm": round(p.y_mm, 3),
        "width_mm": p.width_mm,
        "height_mm": p.height_mm,
        "area_m2": round(p.area_m2, 6),
        "classification": p.classification,
    }


def _offcut_to_dict(o: Offcut) -> dict[str, Any]:
    return {
        "x_mm": round(o.x_mm, 3),
        "y_mm": round(o.y_mm, 3),
        "width_mm": round(o.width_mm, 3),
        "height_mm": round(o.height_mm, 3),
        "area_m2": round(o.area_m2, 6),
    }


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


def write_cutting_plan_json(plan: CuttingPlan, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(plan.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return p


def write_cutting_plan_summary_json(plan: CuttingPlan, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(plan.summary.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return p
