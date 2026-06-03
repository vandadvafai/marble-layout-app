"""Global slab-to-piece optimisation via min-cost bipartite matching.

Wraps ``scipy.optimize.linear_sum_assignment`` (the Hungarian
algorithm, which handles rectangular cost matrices in modern scipy)
over a piece × slab cost matrix. The cost function in ``scoring.py``
collapses our lexicographic priorities — class > area > waste — into
a single linear objective the matcher can solve directly.

V1 single strategy: ``min_waste_global``. The flag exists so later
strategies can plug in without touching the API.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import linear_sum_assignment

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
from placement_engine.optimisation.schema import OptimisationResult
from placement_engine.optimisation.scoring import (
    INFEASIBLE_THRESHOLD,
    piece_slab_cost,
)

OPTIMISATION_STRATEGY_MIN_WASTE_GLOBAL: str = "min_waste_global"

# Strategies the optimiser will accept. Extend here when new scoring
# variants land.
SUPPORTED_STRATEGIES: frozenset[str] = frozenset({
    OPTIMISATION_STRATEGY_MIN_WASTE_GLOBAL,
})


def optimise_assignment(
    cut_list: str | Path | dict[str, Any],
    inventory: str | Path,
    *,
    strategy: str = OPTIMISATION_STRATEGY_MIN_WASTE_GLOBAL,
) -> OptimisationResult:
    """Build an optimal assignment from a cut list + inventory.

    Currently supports one strategy; ``strategy`` is required-ish so
    the CLI and tests can pass it explicitly. Unknown strategies are
    a hard error rather than silently falling back.
    """
    if strategy not in SUPPORTED_STRATEGIES:
        raise ValueError(
            f"unknown strategy {strategy!r}; supported: "
            f"{sorted(SUPPORTED_STRATEGIES)}"
        )

    cut_list_dict, cl_path = _load_cut_list(cut_list)
    inv_path = Path(inventory)
    inv = load_inventory(inv_path)
    slabs = list(inv.slabs)
    pieces = list(cut_list_dict.get("pieces", []))

    target = cut_list_dict.get("target", {})
    target_id = str(target.get("target_id", ""))
    target_name = str(target.get("name", ""))

    if not pieces:
        empty_assignment = Assignment(
            source_cut_list_path=str(cl_path) if cl_path else "",
            source_inventory_path=str(inv_path),
            target_id=target_id, target_name=target_name,
            pieces=[],
            unused_slab_ids=[s.slab_id for s in slabs],
        )
        return OptimisationResult(assignment=empty_assignment, strategy=strategy)

    records, used_slab_ids = _solve(pieces, slabs)
    unused_slab_ids = [s.slab_id for s in slabs if s.slab_id not in used_slab_ids]

    assignment = Assignment(
        source_cut_list_path=str(cl_path) if cl_path else "",
        source_inventory_path=str(inv_path),
        target_id=target_id, target_name=target_name,
        pieces=records,
        unused_slab_ids=unused_slab_ids,
    )
    return OptimisationResult(assignment=assignment, strategy=strategy)


# ---------------------------------------------------------------------------
# core solver
# ---------------------------------------------------------------------------


def _solve(
    pieces: list[dict[str, Any]],
    slabs: list[InventorySlab],
) -> tuple[list[AssignmentRecord], set[str]]:
    """Run the Hungarian matching and translate the result.

    Returns the per-piece assignment records (in the **original**
    cut-list order) plus the set of slab IDs the matching consumed.
    """
    used_slab_ids: set[str] = set()
    records_by_id: dict[str, AssignmentRecord] = {}

    # No slabs: nothing can be assigned, period.
    if not slabs:
        for p in pieces:
            records_by_id[str(p["piece_id"])] = _make_unassigned(
                p, UNASSIGNED_NO_SLAB_FITS,
            )
        return _ordered(pieces, records_by_id), used_slab_ids

    cost = _cost_matrix(pieces, slabs)

    # Rectangular Hungarian. Returns a matching of length min(n_p, n_s).
    row_idx, col_idx = linear_sum_assignment(cost)

    matched_piece_indices: set[int] = set()
    for pi, sj in zip(row_idx.tolist(), col_idx.tolist()):
        c = cost[pi, sj]
        piece = pieces[pi]
        if c >= INFEASIBLE_THRESHOLD:
            # Forced infeasible pair — drop it. The slab stays
            # *unused* (don't add to used_slab_ids). The piece's
            # ``reason`` reflects whether ANY slab in the inventory
            # could have fit it (not just the one scipy paired).
            ever_fits = any(
                s.width_mm >= float(piece.get("bounding_width_mm", 0.0))
                and s.height_mm >= float(piece.get("bounding_height_mm", 0.0))
                for s in slabs
            )
            reason = (
                UNASSIGNED_ALL_FITTING_USED if ever_fits
                else UNASSIGNED_NO_SLAB_FITS
            )
            records_by_id[str(piece["piece_id"])] = _make_unassigned(
                piece, reason,
            )
        else:
            slab = slabs[sj]
            slab_area = (slab.width_mm * slab.height_mm) / 1_000_000.0
            piece_area = float(piece.get("area_m2", 0.0))
            records_by_id[str(piece["piece_id"])] = AssignmentRecord(
                piece_id=str(piece["piece_id"]),
                source_layout_piece_id=str(piece.get("source_layout_piece_id", "")),
                classification=str(piece.get("classification", "")),
                piece_width_mm=float(piece.get("bounding_width_mm", 0.0)),
                piece_height_mm=float(piece.get("bounding_height_mm", 0.0)),
                piece_area_m2=piece_area,
                assignment_status=ASSIGNMENT_ASSIGNED,
                assigned_slab_id=slab.slab_id,
                slab_width_mm=float(slab.width_mm),
                slab_height_mm=float(slab.height_mm),
                slab_area_m2=slab_area,
                waste_area_m2=max(slab_area - piece_area, 0.0),
                reason=None,
                cut_polygon_exterior=_coords(piece.get("cut_polygon_exterior", [])),
                cut_polygon_interiors=[
                    _coords(ring)
                    for ring in (piece.get("cut_polygon_interiors") or [])
                ],
            )
            used_slab_ids.add(slab.slab_id)
        matched_piece_indices.add(pi)

    # Pieces the matching never touched — distinguish reasons:
    #   * was feasible with at least one slab → all_fitting_slabs_used
    #     (other pieces won the slabs that could have fit)
    #   * was infeasible with every slab → no_slab_fits
    for i, piece in enumerate(pieces):
        if i in matched_piece_indices:
            continue
        ever_fits = any(
            s.width_mm >= float(piece.get("bounding_width_mm", 0.0))
            and s.height_mm >= float(piece.get("bounding_height_mm", 0.0))
            for s in slabs
        )
        reason = (
            UNASSIGNED_ALL_FITTING_USED if ever_fits else UNASSIGNED_NO_SLAB_FITS
        )
        records_by_id[str(piece["piece_id"])] = _make_unassigned(piece, reason)

    return _ordered(pieces, records_by_id), used_slab_ids


def _cost_matrix(
    pieces: list[dict[str, Any]],
    slabs: list[InventorySlab],
) -> np.ndarray:
    """Build the piece × slab cost matrix via `piece_slab_cost`."""
    n_p, n_s = len(pieces), len(slabs)
    cost = np.zeros((n_p, n_s), dtype=np.float64)
    for i, p in enumerate(pieces):
        pw = float(p.get("bounding_width_mm", 0.0))
        ph = float(p.get("bounding_height_mm", 0.0))
        pa = float(p.get("area_m2", 0.0))
        pc = str(p.get("classification", ""))
        for j, s in enumerate(slabs):
            slab_area = (s.width_mm * s.height_mm) / 1_000_000.0
            fits = s.width_mm >= pw and s.height_mm >= ph
            cost[i, j] = piece_slab_cost(pc, pa, slab_area, fits)
    return cost


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_unassigned(piece: dict[str, Any], reason: str) -> AssignmentRecord:
    return AssignmentRecord(
        piece_id=str(piece["piece_id"]),
        source_layout_piece_id=str(piece.get("source_layout_piece_id", "")),
        classification=str(piece.get("classification", "")),
        piece_width_mm=float(piece.get("bounding_width_mm", 0.0)),
        piece_height_mm=float(piece.get("bounding_height_mm", 0.0)),
        piece_area_m2=float(piece.get("area_m2", 0.0)),
        assignment_status=ASSIGNMENT_UNASSIGNED,
        assigned_slab_id=None,
        slab_width_mm=None, slab_height_mm=None, slab_area_m2=None,
        waste_area_m2=None,
        reason=reason,
        cut_polygon_exterior=_coords(piece.get("cut_polygon_exterior", [])),
        cut_polygon_interiors=[
            _coords(ring)
            for ring in (piece.get("cut_polygon_interiors") or [])
        ],
    )


def _ordered(
    pieces: list[dict[str, Any]],
    records_by_id: dict[str, AssignmentRecord],
) -> list[AssignmentRecord]:
    """Output records in the input cut-list order for readable JSON."""
    return [
        records_by_id[str(p["piece_id"])]
        for p in pieces
        if str(p["piece_id"]) in records_by_id
    ]


def _coords(raw: Any) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for pt in raw or []:
        if len(pt) >= 2:
            out.append((float(pt[0]), float(pt[1])))
    return out


def _load_cut_list(
    cut_list: str | Path | dict[str, Any],
) -> tuple[dict[str, Any], Path | None]:
    """Path-or-dict input, mirrors the greedy assignment builder."""
    if isinstance(cut_list, dict):
        return cut_list, None
    p = Path(cut_list)
    if not p.exists():
        raise FileNotFoundError(f"cut_list JSON not found: {p}")
    return json.loads(p.read_text(encoding="utf-8")), p
