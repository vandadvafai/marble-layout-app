"""Build a `CuttingPlan` from a cut list + slab inventory.

V1 algorithm — deterministic, no rotation:

  1. Walk cut-list pieces in priority order (full → edge → hole →
     sliver), largest area first within each class.

  2. For each piece, search every free rectangle across all slabs
     (the slab's "whole" rectangle if it hasn't been touched, or each
     of its current offcut rectangles otherwise). Filter to those
     that contain the piece's bounding box axis-aligned.

  3. Pick the rectangle that *leaves the least area behind* (smallest
     leftover area = host_area - piece_area). Tie-break on smaller
     host rectangle area so big rectangles stay reserved for big
     pieces.

  4. Place the piece at the chosen rectangle's bottom-left corner.
     Guillotine-split the host into a "right strip" (full host
     height, to the right of the piece) and a "top strip" (host
     width above the piece minus the right strip). Both strips
     become offcuts when they have positive area.

  5. If nothing fits, classify the failure mode:
       * no_slab_fits           — even a fresh unused slab can't
                                  contain the piece's bounding box.
       * all_fitting_slabs_used — some slab in inventory *could*
                                  have fit a fresh piece, but every
                                  remaining offcut on the slabs we've
                                  touched is too small.

Slabs the planner never wrote a placement to are listed in
``unused_slab_ids``. They remain entirely available for later cutting
rounds.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from placement_engine.cutting.schema import (
    CUTTING_UNASSIGNED_ALL_FITTING_USED,
    CUTTING_UNASSIGNED_NO_SLAB_FITS,
    CutPlacement,
    CuttingPlan,
    CuttingSlab,
    Offcut,
    UnassignedPiece,
)
from placement_engine.inventory import load_inventory
from placement_engine.inventory.model import InventorySlab

# Same priority order used in the assignment layer. Keeping the
# vocabulary identical lets fabricators reason about both reports in
# the same language.
_PRIORITY: dict[str, int] = {
    "full": 0,
    "edge": 1,
    "hole": 2,
    "sliver": 3,
}

# Sub-mm slivers that the guillotine split would otherwise leave behind
# are dropped. They cannot be cut to any usable piece and just bloat
# the offcut list with noise. 1 mm in either dimension is well below
# any real saw kerf.
_MIN_OFFCUT_SIDE_MM: float = 1.0


@dataclass
class _Rect:
    """Internal mutable rectangle — used both for offcuts and the
    initial "whole slab" rectangle before the first cut."""

    x: float
    y: float
    w: float
    h: float

    @property
    def area(self) -> float:
        return self.w * self.h


@dataclass
class _SlabState:
    """Mutable scratch state for one slab during the packing pass."""

    slab: InventorySlab
    free_rects: list[_Rect] = field(default_factory=list)
    placements: list[CutPlacement] = field(default_factory=list)
    touched: bool = False

    @property
    def slab_id(self) -> str:
        return self.slab.slab_id

    @property
    def width(self) -> float:
        return float(self.slab.width_mm)

    @property
    def height(self) -> float:
        return float(self.slab.height_mm)


def build_cutting_plan(
    cut_list: str | Path | dict[str, Any],
    inventory: str | Path,
) -> CuttingPlan:
    """Build a `CuttingPlan` from a cut list (path or dict) + inventory."""
    cut_list_dict, cl_path = _load_cut_list(cut_list)
    inv_path = Path(inventory)
    inv = load_inventory(inv_path)
    slabs = list(inv.slabs)

    raw_pieces = list(cut_list_dict.get("pieces", []))
    states: list[_SlabState] = [
        _SlabState(slab=s, free_rects=[_Rect(0.0, 0.0, float(s.width_mm), float(s.height_mm))])
        for s in slabs
    ]
    state_by_id: dict[str, _SlabState] = {st.slab_id: st for st in states}

    unassigned: list[UnassignedPiece] = []
    # Drive picking in priority order so high-value pieces grab the
    # best rectangles first.
    for piece in _sort_pieces_by_priority(raw_pieces):
        placement_or_reason = _place_piece(piece, states, slabs)
        if isinstance(placement_or_reason, str):
            unassigned.append(_make_unassigned(piece, reason=placement_or_reason))

    # Build the output slab list — only slabs that ended up with at
    # least one placement appear here. Untouched slabs go to
    # `unused_slab_ids`. This mirrors the assignment layer's contract.
    cutting_slabs: list[CuttingSlab] = []
    unused_slab_ids: list[str] = []
    for st in states:
        if not st.touched:
            unused_slab_ids.append(st.slab_id)
            continue
        used_area_m2 = sum(p.area_m2 for p in st.placements)
        slab_area_m2 = (st.width * st.height) / 1_000_000.0
        cutting_slabs.append(
            CuttingSlab(
                slab_id=st.slab_id,
                original_width_mm=st.width,
                original_height_mm=st.height,
                original_area_m2=slab_area_m2,
                used_area_m2=used_area_m2,
                # waste = consumed slab area - placed piece area. Identical
                # accounting to the assignment layer so reports compare.
                waste_area_m2=max(slab_area_m2 - used_area_m2, 0.0),
                placements=list(st.placements),
                offcuts=[
                    Offcut(
                        x_mm=r.x, y_mm=r.y, width_mm=r.w, height_mm=r.h,
                        area_m2=(r.w * r.h) / 1_000_000.0,
                    )
                    for r in st.free_rects
                    if r.w >= _MIN_OFFCUT_SIDE_MM and r.h >= _MIN_OFFCUT_SIDE_MM
                ],
            )
        )

    target = cut_list_dict.get("target", {})
    return CuttingPlan(
        source_cut_list_path=str(cl_path) if cl_path else "",
        source_inventory_path=str(inv_path),
        target_id=str(target.get("target_id", "")),
        target_name=str(target.get("name", "")),
        slabs=cutting_slabs,
        unassigned=unassigned,
        unused_slab_ids=unused_slab_ids,
    )


# ---------------------------------------------------------------------------
# placement core
# ---------------------------------------------------------------------------


def _place_piece(
    piece: dict[str, Any],
    states: list[_SlabState],
    inventory_slabs: list[InventorySlab],
) -> CutPlacement | str:
    """Try to place ``piece`` in the best available free rectangle.

    Returns a `CutPlacement` on success, or one of the
    ``CUTTING_UNASSIGNED_*`` reason strings on failure.
    """
    pw = float(piece.get("bounding_width_mm", 0.0))
    ph = float(piece.get("bounding_height_mm", 0.0))
    if pw <= 0 or ph <= 0:
        # A degenerate piece can never be cut. Treat as no-fit so the
        # report still mentions it instead of silently dropping it.
        return CUTTING_UNASSIGNED_NO_SLAB_FITS

    best: tuple[_SlabState, int, _Rect, tuple[float, int, float, str]] | None = None
    for st in states:
        for idx, rect in enumerate(st.free_rects):
            if rect.w + 1e-9 < pw or rect.h + 1e-9 < ph:
                continue
            leftover = rect.area - (pw * ph)
            # Sort key (lower is better):
            #   1. smallest leftover  → least waste
            #   2. prefer touched slabs (0 < 1) → don't crack open a
            #      fresh slab when an existing offcut fits equally well
            #   3. smaller host rectangle  → keep big rectangles for
            #      big pieces still to come
            #   4. slab_id alphabetical  → deterministic last resort
            key = (
                leftover,
                0 if st.touched else 1,
                rect.area,
                st.slab_id,
            )
            if best is None or key < best[3]:
                best = (st, idx, rect, key)

    if best is None:
        return _classify_no_fit(pw, ph, inventory_slabs)

    st, idx, rect, _key = best
    placement = CutPlacement(
        cut_piece_id=str(piece.get("piece_id", "")),
        source_layout_piece_id=str(piece.get("source_layout_piece_id", "")),
        slab_id=st.slab_id,
        x_mm=rect.x,
        y_mm=rect.y,
        width_mm=pw,
        height_mm=ph,
        area_m2=(pw * ph) / 1_000_000.0,
        classification=str(piece.get("classification", "")),
    )
    st.placements.append(placement)
    st.touched = True
    # Remove the host rectangle, then add up to two new offcuts from
    # the guillotine split. Drop sub-mm slivers (see _MIN_OFFCUT_SIDE_MM).
    del st.free_rects[idx]
    right_w = rect.w - pw
    top_h = rect.h - ph
    if right_w >= _MIN_OFFCUT_SIDE_MM and rect.h >= _MIN_OFFCUT_SIDE_MM:
        st.free_rects.append(_Rect(rect.x + pw, rect.y, right_w, rect.h))
    if top_h >= _MIN_OFFCUT_SIDE_MM and pw >= _MIN_OFFCUT_SIDE_MM:
        st.free_rects.append(_Rect(rect.x, rect.y + ph, pw, top_h))
    return placement


def _classify_no_fit(
    pw: float, ph: float, inventory_slabs: list[InventorySlab],
) -> str:
    """Decide whether *any* slab in inventory could ever have fit this piece."""
    ever_fits = any(
        s.width_mm + 1e-9 >= pw and s.height_mm + 1e-9 >= ph
        for s in inventory_slabs
    )
    return (
        CUTTING_UNASSIGNED_ALL_FITTING_USED if ever_fits
        else CUTTING_UNASSIGNED_NO_SLAB_FITS
    )


def _make_unassigned(piece: dict[str, Any], *, reason: str) -> UnassignedPiece:
    return UnassignedPiece(
        cut_piece_id=str(piece.get("piece_id", "")),
        source_layout_piece_id=str(piece.get("source_layout_piece_id", "")),
        classification=str(piece.get("classification", "")),
        width_mm=float(piece.get("bounding_width_mm", 0.0)),
        height_mm=float(piece.get("bounding_height_mm", 0.0)),
        area_m2=float(piece.get("area_m2", 0.0)),
        reason=reason,
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _load_cut_list(
    cut_list: str | Path | dict[str, Any],
) -> tuple[dict[str, Any], Path | None]:
    if isinstance(cut_list, dict):
        return cut_list, None
    p = Path(cut_list)
    if not p.exists():
        raise FileNotFoundError(f"cut_list JSON not found: {p}")
    return json.loads(p.read_text(encoding="utf-8")), p


def _sort_pieces_by_priority(
    pieces: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Stable sort: priority class first, then descending area."""
    def key(piece: dict[str, Any]) -> tuple[int, float]:
        cls = piece.get("classification", "")
        return (
            _PRIORITY.get(cls, 99),
            -float(piece.get("area_m2", 0.0)),
        )
    return sorted(pieces, key=key)
