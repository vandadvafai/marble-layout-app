"""Build an `Assignment` from a cut list + a slab inventory.

V1 algorithm — deterministic, no rotation, no optimisation:

    1. Walk cut-list pieces in priority order
         (full → edge → hole → sliver)
       Within a priority class, largest piece first so it grabs the
       best available slab before smaller pieces.

    2. For each piece, find unused slabs whose ``width × height`` covers
       the piece's bounding box. Pick the smallest by area (least
       waste). Mark that slab as used and record the waste.

    3. If no unused slab fits, classify why:
         * ``no_slab_fits``           — even with every slab unused,
                                        nothing in stock would cover
                                        this piece
         * ``all_fitting_slabs_used`` — slabs that could have fit were
                                        already consumed by higher-
                                        priority pieces

    4. Slabs the algorithm never picked up are reported in
       ``unused_slab_ids`` for the next milestone (offcut tracking,
       waste reuse, …) to consume.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from placement_engine.assignment.schema import (
    ASSIGNMENT_ASSIGNED,
    ASSIGNMENT_UNASSIGNED,
    UNASSIGNED_ALL_FITTING_USED,
    UNASSIGNED_NO_SLAB_FITS,
    Assignment,
    AssignmentRecord,
)
from placement_engine.inventory import load_inventory
from placement_engine.inventory.model import InventorySlab

# Priority order: lower number = picked first. Matches the cut-list
# classification priority (full pieces are most valuable to land on
# their ideal slab; slivers come last because they fit almost
# anywhere).
_PRIORITY: dict[str, int] = {
    "full": 0,
    "edge": 1,
    "hole": 2,
    "sliver": 3,
}


def build_assignment(
    cut_list: str | Path | dict[str, Any],
    inventory: str | Path,
) -> Assignment:
    """Build an `Assignment` from a cut list (path or dict) + inventory path."""
    cut_list_dict, cl_path = _load_cut_list(cut_list)
    inv_path = Path(inventory)
    inv = load_inventory(inv_path)
    slabs = list(inv.slabs)

    raw_pieces = list(cut_list_dict.get("pieces", []))
    records, used_slab_ids = _assign_pieces(raw_pieces, slabs)

    unused_slab_ids = [s.slab_id for s in slabs if s.slab_id not in used_slab_ids]

    target = cut_list_dict.get("target", {})
    return Assignment(
        source_cut_list_path=str(cl_path) if cl_path else "",
        source_inventory_path=str(inv_path),
        target_id=str(target.get("target_id", "")),
        target_name=str(target.get("name", "")),
        pieces=records,
        unused_slab_ids=unused_slab_ids,
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _load_cut_list(
    cut_list: str | Path | dict[str, Any],
) -> tuple[dict[str, Any], Path | None]:
    """Resolve the input to ``(dict, source_path_or_None)``."""
    if isinstance(cut_list, dict):
        return cut_list, None
    p = Path(cut_list)
    if not p.exists():
        raise FileNotFoundError(f"cut_list JSON not found: {p}")
    return json.loads(p.read_text(encoding="utf-8")), p


def _sort_pieces_by_priority(
    pieces: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Stable sort: priority class first, then largest area first.

    Largest-first within a class is the right default: it gives the
    most-constrained pieces (the big full tiles) first crack at the
    biggest slabs, before smaller pieces start "eating" capacity.
    """
    def key(piece: dict[str, Any]) -> tuple[int, float]:
        cls = piece.get("classification", "")
        return (
            _PRIORITY.get(cls, 99),
            -float(piece.get("area_m2", 0.0)),
        )
    return sorted(pieces, key=key)


def _assign_pieces(
    raw_pieces: list[dict[str, Any]],
    slabs: list[InventorySlab],
) -> tuple[list[AssignmentRecord], set[str]]:
    """Walk pieces (in priority order) and pick the smallest-fitting slab
    for each. Records are returned in the **original cut-list order**.
    """
    used_slab_ids: set[str] = set()
    # Pre-compute area cache to keep the inner loop tight.
    slab_area: dict[str, float] = {
        s.slab_id: (s.width_mm * s.height_mm) / 1_000_000.0 for s in slabs
    }

    # Picking order is by priority class then descending area.
    picking_order = _sort_pieces_by_priority(raw_pieces)

    records_by_piece_id: dict[str, AssignmentRecord] = {}
    for piece in picking_order:
        piece_id = str(piece.get("piece_id", ""))
        piece_w = float(piece.get("bounding_width_mm", 0.0))
        piece_h = float(piece.get("bounding_height_mm", 0.0))
        piece_area = float(piece.get("area_m2", 0.0))

        # 1. Filter candidates: unused, w & h cover the piece.
        candidates = [
            s for s in slabs
            if s.slab_id not in used_slab_ids
            and s.width_mm >= piece_w
            and s.height_mm >= piece_h
        ]

        if candidates:
            # 2. Pick smallest by area (least waste).
            candidates.sort(key=lambda s: slab_area[s.slab_id])
            chosen = candidates[0]
            used_slab_ids.add(chosen.slab_id)
            chosen_area = slab_area[chosen.slab_id]
            records_by_piece_id[piece_id] = AssignmentRecord(
                piece_id=piece_id,
                source_layout_piece_id=str(piece.get("source_layout_piece_id", "")),
                classification=str(piece.get("classification", "")),
                piece_width_mm=piece_w,
                piece_height_mm=piece_h,
                piece_area_m2=piece_area,
                assignment_status=ASSIGNMENT_ASSIGNED,
                assigned_slab_id=chosen.slab_id,
                slab_width_mm=float(chosen.width_mm),
                slab_height_mm=float(chosen.height_mm),
                slab_area_m2=chosen_area,
                waste_area_m2=max(chosen_area - piece_area, 0.0),
                reason=None,
                cut_polygon_exterior=_coords(piece.get("cut_polygon_exterior", [])),
                cut_polygon_interiors=[
                    _coords(ring)
                    for ring in piece.get("cut_polygon_interiors", []) or []
                ],
            )
        else:
            # 3. Classify why we couldn't place it.
            ever_fits = any(
                s.width_mm >= piece_w and s.height_mm >= piece_h
                for s in slabs
            )
            reason = (
                UNASSIGNED_ALL_FITTING_USED if ever_fits
                else UNASSIGNED_NO_SLAB_FITS
            )
            records_by_piece_id[piece_id] = AssignmentRecord(
                piece_id=piece_id,
                source_layout_piece_id=str(piece.get("source_layout_piece_id", "")),
                classification=str(piece.get("classification", "")),
                piece_width_mm=piece_w,
                piece_height_mm=piece_h,
                piece_area_m2=piece_area,
                assignment_status=ASSIGNMENT_UNASSIGNED,
                assigned_slab_id=None,
                slab_width_mm=None,
                slab_height_mm=None,
                slab_area_m2=None,
                waste_area_m2=None,
                reason=reason,
                cut_polygon_exterior=_coords(piece.get("cut_polygon_exterior", [])),
                cut_polygon_interiors=[
                    _coords(ring)
                    for ring in piece.get("cut_polygon_interiors", []) or []
                ],
            )

    # Preserve the cut-list's original order in the output records so
    # assignment.json reads top-to-bottom the same way the cut list
    # does. The picking pass above ran in priority order — that does
    # not leak into the output order.
    ordered_ids = [str(p.get("piece_id", "")) for p in raw_pieces]
    records = [
        records_by_piece_id[pid] for pid in ordered_ids
        if pid in records_by_piece_id
    ]
    return records, used_slab_ids


def _coords(raw: Any) -> list[tuple[float, float]]:
    """Coerce a polygon coordinate list (lists-of-pairs) into tuples."""
    out: list[tuple[float, float]] = []
    for pt in raw or []:
        if len(pt) >= 2:
            out.append((float(pt[0]), float(pt[1])))
    return out
