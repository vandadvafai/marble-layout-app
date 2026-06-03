"""Dataclasses + JSON I/O for the V1 assignment layer."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Assignment-status labels.
ASSIGNMENT_ASSIGNED: str = "assigned"
ASSIGNMENT_UNASSIGNED: str = "unassigned"

# Unassigned reasons. Designers see these in the JSON / summary so they
# know whether they're inventory-bound (nothing in stock can fit) or
# capacity-bound (slabs that *could* fit were already used elsewhere).
UNASSIGNED_NO_SLAB_FITS: str = "no_slab_fits"
UNASSIGNED_ALL_FITTING_USED: str = "all_fitting_slabs_used"


@dataclass
class AssignmentRecord:
    """One cut-piece → slab record (or unassigned)."""

    # Identity / traceability
    piece_id: str
    source_layout_piece_id: str
    classification: str  # "full" | "edge" | "hole" | "sliver"
    # Piece dimensions (bounding box of the cut shape)
    piece_width_mm: float
    piece_height_mm: float
    piece_area_m2: float
    # Assignment outcome
    assignment_status: str   # "assigned" | "unassigned"
    assigned_slab_id: str | None
    slab_width_mm: float | None
    slab_height_mm: float | None
    slab_area_m2: float | None
    waste_area_m2: float | None
    reason: str | None       # populated only when unassigned
    # Geometry — duplicated from the cut list so the assignment.json
    # is self-renderable. Polygon vertex counts are tiny (≤ 9 typically).
    cut_polygon_exterior: list[tuple[float, float]] = field(default_factory=list)
    cut_polygon_interiors: list[list[tuple[float, float]]] = field(default_factory=list)


@dataclass
class AssignmentSummary:
    """Aggregate counts + areas — the fabrication one-pager.

    Two distinct area concepts are reported because they answer different
    questions:

      * ``assigned_area_m2`` / ``unassigned_area_m2`` are **floor-side**
        quantities — what fraction of the layout has and hasn't been
        supplied. ``unassigned_area_m2`` is the *uncovered floor area*,
        NOT slab waste.
      * ``slab_area_used_m2`` and ``estimated_waste_m2`` are
        **slab-side** quantities — the total surface area of slabs we
        consumed, and the amount of that area not covered by the piece
        it was assigned to (slab area − piece area, summed over assigned
        records). This is the V1 estimate of fabrication scrap and
        ignores offcut reuse (a later milestone).

    Identity: ``estimated_waste_m2 == slab_area_used_m2 − assigned_area_m2``.
    """

    # Piece counts
    total_pieces: int
    assigned_pieces: int
    unassigned_pieces: int
    # Per-classification breakdown of the *assigned* set.
    full_assigned: int
    edge_assigned: int
    hole_assigned: int
    sliver_assigned: int
    # Slab counts
    slabs_used: int
    unused_slabs: int
    total_slab_count: int
    # Floor-side areas (m²) — what the layout asked for vs. what we supplied.
    assigned_area_m2: float
    unassigned_area_m2: float
    # Slab-side areas (m²) — what we consumed and how much of it became waste.
    slab_area_used_m2: float
    estimated_waste_m2: float
    # Most common reason among unassigned pieces; null when nothing's unassigned.
    main_unassigned_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for key in (
            "assigned_area_m2", "unassigned_area_m2",
            "slab_area_used_m2", "estimated_waste_m2",
        ):
            d[key] = round(d[key], 4)
        return d


@dataclass
class Assignment:
    """Top-level mapping result."""

    source_cut_list_path: str
    source_inventory_path: str
    target_id: str
    target_name: str
    pieces: list[AssignmentRecord]
    unused_slab_ids: list[str] = field(default_factory=list)

    @property
    def summary(self) -> AssignmentSummary:
        assigned_records = [
            r for r in self.pieces if r.assignment_status == ASSIGNMENT_ASSIGNED
        ]
        unassigned_records = [
            r for r in self.pieces if r.assignment_status == ASSIGNMENT_UNASSIGNED
        ]
        by_class: dict[str, int] = {}
        for r in assigned_records:
            by_class[r.classification] = by_class.get(r.classification, 0) + 1
        used_slabs = {
            r.assigned_slab_id for r in assigned_records if r.assigned_slab_id
        }

        # Floor-side areas.
        assigned_area = sum(r.piece_area_m2 for r in assigned_records)
        unassigned_area = sum(r.piece_area_m2 for r in unassigned_records)
        # Slab-side areas.
        slab_area_used = sum(
            r.slab_area_m2 or 0.0 for r in assigned_records
        )
        estimated_waste = sum(
            r.waste_area_m2 or 0.0 for r in assigned_records
        )

        # Most common unassigned reason. Counter would be a touch more
        # idiomatic; the dict approach avoids importing collections here.
        reason_counts: dict[str, int] = {}
        for r in unassigned_records:
            if r.reason:
                reason_counts[r.reason] = reason_counts.get(r.reason, 0) + 1
        if reason_counts:
            main_reason = max(reason_counts.items(), key=lambda kv: kv[1])[0]
        else:
            main_reason = None

        return AssignmentSummary(
            total_pieces=len(self.pieces),
            assigned_pieces=len(assigned_records),
            unassigned_pieces=len(unassigned_records),
            full_assigned=by_class.get("full", 0),
            edge_assigned=by_class.get("edge", 0),
            hole_assigned=by_class.get("hole", 0),
            sliver_assigned=by_class.get("sliver", 0),
            slabs_used=len(used_slabs),
            unused_slabs=len(self.unused_slab_ids),
            total_slab_count=len(self.unused_slab_ids) + len(used_slabs),
            assigned_area_m2=assigned_area,
            unassigned_area_m2=unassigned_area,
            slab_area_used_m2=slab_area_used,
            estimated_waste_m2=estimated_waste,
            main_unassigned_reason=main_reason,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_cut_list_path": self.source_cut_list_path,
            "source_inventory_path": self.source_inventory_path,
            "target": {"target_id": self.target_id, "name": self.target_name},
            "pieces": [_record_to_dict(r) for r in self.pieces],
            "unused_slab_ids": list(self.unused_slab_ids),
            "summary": self.summary.to_dict(),
        }


def _record_to_dict(r: AssignmentRecord) -> dict[str, Any]:
    """Stable JSON shape: tuples → lists, floats rounded for readability."""
    return {
        "piece_id": r.piece_id,
        "source_layout_piece_id": r.source_layout_piece_id,
        "classification": r.classification,
        "piece_width_mm": r.piece_width_mm,
        "piece_height_mm": r.piece_height_mm,
        "piece_area_m2": round(r.piece_area_m2, 6),
        "assignment_status": r.assignment_status,
        "assigned_slab_id": r.assigned_slab_id,
        "slab_width_mm": r.slab_width_mm,
        "slab_height_mm": r.slab_height_mm,
        "slab_area_m2": (
            round(r.slab_area_m2, 6) if r.slab_area_m2 is not None else None
        ),
        "waste_area_m2": (
            round(r.waste_area_m2, 6) if r.waste_area_m2 is not None else None
        ),
        "reason": r.reason,
        "cut_polygon_exterior": [list(pt) for pt in r.cut_polygon_exterior],
        "cut_polygon_interiors": [
            [list(pt) for pt in ring] for ring in r.cut_polygon_interiors
        ],
    }


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


def write_assignment_json(assignment: Assignment, path: str | Path) -> Path:
    """Serialize the full assignment to JSON. Returns the written path."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(assignment.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return p


def write_summary_json(assignment: Assignment, path: str | Path) -> Path:
    """Serialize only the summary — fabrication's one-page view."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(assignment.summary.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return p
