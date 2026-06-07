"""Targeted tests for three rule-layer fixes (0.1.28):

1. R9 — coverage hard rule. Any candidate covering less of the
   target floor than ``plan.min_coverage_ratio`` is disqualified.
2. R7 — reward only when a SINGLE piece spans the FULL doorway
   opening, not when two adjacent pieces each cover half.
3. R2 — detect seams that land inside the doorway opening even
   when the seam's intersection with the doorway segment is a
   single point (the "vertical seam emanating from the
   threshold" case).

Scope is intentionally narrow per the cost-control rules: we
exercise rules.py directly with hand-built layout dicts plus one
end-to-end L-shape check. The selector layer has its own test file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from placement_engine.architectural import (
    ArchitecturalPlan,
    Doorway,
    Space,
    load_architectural_plan,
    select_best_layout,
)
from placement_engine.architectural.rules import (
    RULE_FULL_COVERAGE,
    RULE_FULL_SLABS_IN_DOORWAYS,
    RULE_NO_SEAMS_IN_DOORWAYS,
    STATUS_PASS,
    STATUS_REWARD,
    STATUS_VIOLATION,
    evaluate_layout,
)
from placement_engine.architectural.schema import (
    DEFAULT_MIN_COVERAGE_RATIO,
    VISIBILITY_HIGH,
)
from placement_engine.target_area import load_target_geometry_from_dxf

REPO_ROOT = Path(__file__).resolve().parent.parent
EX_L = REPO_ROOT / "examples/cad_inputs/demo/demo_l_shape_floor.dxf"


# ---------------------------------------------------------------------------
# helpers — build minimal layout dicts the rule layer accepts
# ---------------------------------------------------------------------------


def _rect_piece(piece_id: str, x: float, y: float, w: float, h: float):
    """A single rectangular full-tile piece in the canonical layout
    JSON shape."""
    return {
        "piece_id": piece_id,
        "zone_id": "z0",
        "row": 0, "col": 0,
        "nominal_x_mm": x, "nominal_y_mm": y,
        "nominal_width_mm": w, "nominal_height_mm": h,
        "actual_cut_polygon": [
            (x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y),
        ],
        "bounding_width_mm": w, "bounding_height_mm": h,
        "actual_area_m2": (w * h) / 1_000_000.0,
        "is_full_tile": True, "is_edge_piece": False,
        "intersects_hole": False, "interior_holes": [],
        "notes": [],
    }


def _layout_dict(
    pieces: list[dict],
    *,
    target_id: str = "t",
    coverage_pct: float = 100.0,
) -> dict:
    """Wrap pieces in the minimal layout dict shape evaluate_layout
    expects. ``coverage_pct`` populates derived.coverage_percentage —
    the value R9 reads directly."""
    return {
        "target": {"target_id": target_id, "name": target_id},
        "grid": {},
        "pieces": pieces,
        "derived": {"coverage_percentage": coverage_pct},
    }


def _rule(report, rule_id: str):
    return next(r for r in report.rules if r.rule_id == rule_id)


# ---------------------------------------------------------------------------
# 1. R9 — full coverage (hard rule)
# ---------------------------------------------------------------------------


def test_r9_full_coverage_passes_at_threshold():
    """100% coverage should always pass R9, regardless of any other
    rule outcomes."""
    pieces = [_rect_piece("p1", 0, 0, 1000, 1000)]
    report = evaluate_layout(
        _layout_dict(pieces, coverage_pct=100.0),
        {"pieces": []},
        ArchitecturalPlan(target_id="t"),
    )
    r9 = _rule(report, RULE_FULL_COVERAGE)
    assert r9.status == STATUS_PASS
    assert r9.score_delta == 0.0


def test_r9_full_coverage_passes_within_default_tolerance():
    """Default tolerance is 99.9% — 99.95% coverage passes."""
    report = evaluate_layout(
        _layout_dict([_rect_piece("p1", 0, 0, 1000, 1000)],
                     coverage_pct=99.95),
        {"pieces": []},
        ArchitecturalPlan(target_id="t"),
    )
    assert _rule(report, RULE_FULL_COVERAGE).status == STATUS_PASS


def test_r9_full_coverage_fails_below_threshold():
    """91.82% coverage (the C05 case from the validation report) must
    fail R9 under the default 99.9% threshold."""
    report = evaluate_layout(
        _layout_dict([_rect_piece("p1", 0, 0, 1000, 1000)],
                     coverage_pct=91.82),
        {"pieces": []},
        ArchitecturalPlan(target_id="t"),
    )
    r9 = _rule(report, RULE_FULL_COVERAGE)
    assert r9.status == STATUS_VIOLATION
    assert r9.score_delta < 0
    assert report.hard_violation_count >= 1
    # Designer-facing message should call out the actual vs required
    # ratios so it's clear from the report what failed.
    assert "91.82" in r9.message
    assert "99.90" in r9.message or "99.9" in r9.message


def test_r9_threshold_is_plan_configurable():
    """A plan can demand strict 100% coverage; 99.95% then fails."""
    plan = ArchitecturalPlan(target_id="t", min_coverage_ratio=1.0)
    report = evaluate_layout(
        _layout_dict([_rect_piece("p1", 0, 0, 1000, 1000)],
                     coverage_pct=99.95),
        {"pieces": []},
        plan,
    )
    assert _rule(report, RULE_FULL_COVERAGE).status == STATUS_VIOLATION


def test_r9_default_ratio_is_999_per_milli():
    """Sanity check on the default — guards against future accidental
    tweaks."""
    assert DEFAULT_MIN_COVERAGE_RATIO == 0.999


# ---------------------------------------------------------------------------
# 2. R7 — reward only single piece spanning the full doorway
# ---------------------------------------------------------------------------


def test_r7_rewards_single_piece_spanning_full_doorway():
    """A single 2000 mm-wide piece covering doorway [400, 1400]
    (=1000 mm wide) gets the per-doorway reward exactly once."""
    pieces = [_rect_piece("spanner", 0, 0, 2000, 1000)]
    plan = ArchitecturalPlan(
        target_id="t",
        doorways=[Doorway("d1", segment=((400, 0), (1400, 0)))],
    )
    report = evaluate_layout(
        _layout_dict(pieces),
        {"pieces": []},
        plan,
    )
    r7 = _rule(report, RULE_FULL_SLABS_IN_DOORWAYS)
    assert r7.status == STATUS_REWARD
    assert r7.count == 1
    assert "spanner" in r7.affected_ids


def test_r7_does_not_reward_two_pieces_each_touching_half():
    """Two adjacent pieces, each covering half the doorway, with a
    seam at the doorway midpoint, must NOT receive R7 reward.

    Pre-fix this case awarded +16 (2 pieces × +8) — the exact bug
    the user reported in the validation report.
    """
    # Doorway runs from x=400 to x=1400 (width 1000).
    # Piece A: x∈[0, 900], piece B: x∈[900, 2000].
    # Neither piece individually contains the full doorway segment.
    pieces = [
        _rect_piece("left", 0, 0, 900, 1000),
        _rect_piece("right", 900, 0, 1100, 1000),
    ]
    plan = ArchitecturalPlan(
        target_id="t",
        doorways=[Doorway("d1", segment=((400, 0), (1400, 0)))],
    )
    report = evaluate_layout(
        _layout_dict(pieces),
        {"pieces": []},
        plan,
    )
    r7 = _rule(report, RULE_FULL_SLABS_IN_DOORWAYS)
    assert r7.status == STATUS_PASS, (
        f"two pieces each touching half should not be rewarded; "
        f"got status={r7.status}, count={r7.count}"
    )
    assert r7.count == 0
    assert r7.score_delta == 0.0


def test_r7_reward_is_one_per_doorway_not_per_piece():
    """Even if multiple pieces (vertically stacked rows) span the
    same doorway, the reward fires once per doorway."""
    pieces = [
        _rect_piece("row0", 0, 0, 2000, 1000),  # spans doorway
        _rect_piece("row1", 0, 1000, 2000, 1000),  # also "spans"
    ]
    plan = ArchitecturalPlan(
        target_id="t",
        doorways=[Doorway("d1", segment=((400, 0), (1400, 0)))],
    )
    report = evaluate_layout(
        _layout_dict(pieces),
        {"pieces": []},
        plan,
    )
    r7 = _rule(report, RULE_FULL_SLABS_IN_DOORWAYS)
    assert r7.count == 1, "one reward per doorway, not per spanning piece"


def test_r7_with_two_doorways_rewards_each_when_separately_spanned():
    """Reward stacks across distinct doorways — each gets its own
    +1 when there's a single-piece span."""
    pieces = [
        _rect_piece("p1", 0, 0, 2000, 1000),   # spans d1
        _rect_piece("p2", 3000, 0, 2000, 1000),  # spans d2
    ]
    plan = ArchitecturalPlan(
        target_id="t",
        doorways=[
            Doorway("d1", segment=((400, 0), (1400, 0))),
            Doorway("d2", segment=((3400, 0), (4400, 0))),
        ],
    )
    report = evaluate_layout(
        _layout_dict(pieces),
        {"pieces": []},
        plan,
    )
    assert _rule(report, RULE_FULL_SLABS_IN_DOORWAYS).count == 2


# ---------------------------------------------------------------------------
# 3. R2 — detect seams landing in the interior of a doorway opening
# ---------------------------------------------------------------------------


def test_r2_detects_seam_at_doorway_interior_point():
    """Two adjacent pieces sharing a vertical seam at x=900, where
    the seam touches the doorway segment at (900, 0) — a point
    strictly inside the [400, 1400] doorway range. This is the
    C05 case from the previous validation report.

    Pre-fix: R2 didn't fire because Shapely.crosses() excludes
    endpoint touches and (900, 0) is the seam's endpoint.
    Post-fix: R2 fires because the touch point lies in the
    doorway's interior.
    """
    pieces = [
        _rect_piece("left", 0, 0, 900, 1000),
        _rect_piece("right", 900, 0, 900, 1000),
    ]
    plan = ArchitecturalPlan(
        target_id="t",
        doorways=[
            Doorway("d1", segment=((400, 0), (1400, 0)),
                    is_main_entrance=True),
        ],
    )
    report = evaluate_layout(
        _layout_dict(pieces),
        {"pieces": []},
        plan,
    )
    r2 = _rule(report, RULE_NO_SEAMS_IN_DOORWAYS)
    assert r2.status == STATUS_VIOLATION, (
        f"expected R2 violation when a seam emanates from inside "
        f"the doorway opening; got {r2.status}"
    )
    assert r2.count == 1
    # Main entrance → 50-point penalty (not the 25-point regular one).
    assert r2.score_delta == -50.0


def test_r2_does_not_fire_when_seam_touches_doorway_endpoint():
    """A seam emanating from the doorway's endpoint (the wall corner)
    is fine — that's the threshold-meets-wall point, not a conflict
    in the opening."""
    # Doorway [1000, 2000]; seam at x=1000 sits exactly at the
    # doorway's left endpoint (the wall corner).
    pieces = [
        _rect_piece("left", 0, 0, 1000, 1000),
        _rect_piece("right", 1000, 0, 1000, 1000),
    ]
    plan = ArchitecturalPlan(
        target_id="t",
        doorways=[Doorway("d1", segment=((1000, 0), (2000, 0)))],
    )
    report = evaluate_layout(
        _layout_dict(pieces),
        {"pieces": []},
        plan,
    )
    assert _rule(report, RULE_NO_SEAMS_IN_DOORWAYS).status == STATUS_PASS


# ---------------------------------------------------------------------------
# End-to-end: rerun the L-shape selector and verify the user-reported
# scoring bug is gone
# ---------------------------------------------------------------------------


def _l_shape_plan() -> ArchitecturalPlan:
    return ArchitecturalPlan(
        target_id="demo_l_shape_floor",
        spaces=[Space(
            "main", "Main living area",
            polygon=[(0, 0), (8000, 0), (8000, 4000),
                     (4800, 4000), (4800, 2600), (0, 2600)],
            visibility=VISIBILITY_HIGH,
        )],
        doorways=[Doorway(
            "main_entrance", segment=((3500, 0), (4500, 0)),
            is_main_entrance=True, width_mm=1000,
        )],
    )


def _real_inventory():
    from dataclasses import dataclass

    @dataclass
    class _Slab:
        width_mm: float
        height_mm: float

    return [
        _Slab(1590, 1590), _Slab(1590, 1980), _Slab(1550, 2040),
        _Slab(1590, 2200), _Slab(1570, 2320),
        _Slab(1600, 2500), _Slab(1610, 2620),
    ]


def test_l_shape_c05_is_disqualified_by_coverage_after_fix():
    """The user-reported scenario: C05 (offset_half_x) won with 91.82%
    coverage in the previous milestone. With the coverage hard rule
    in place, C05 must be marked invalid — coverage shortfall AND
    a seam in the doorway both contribute to its rejection."""
    geom = load_target_geometry_from_dxf(EX_L)
    result = select_best_layout(geom, _real_inventory(), _l_shape_plan())
    c05 = next(
        c for c in result.candidates
        if c.candidate_id.endswith("_offset_half_x")
    )
    assert not c05.is_valid
    assert RULE_FULL_COVERAGE in c05.disqualifications


def test_l_shape_winner_is_now_a_full_coverage_anchor_candidate():
    """With coverage enforced and R7 fixed, the L-shape winner must
    come from the anchor-strategy pool (the only pool with 100%
    coverage on this fixture). Score should reflect the R7 fix:
    +8 (one doorway × one single spanning slab), not +16 (the
    pre-fix double-count)."""
    geom = load_target_geometry_from_dxf(EX_L)
    result = select_best_layout(geom, _real_inventory(), _l_shape_plan())
    winner = result.selected
    assert winner.is_valid
    assert winner.strategy == "anchor"
    # Score must not include the pre-fix +16 R7 bonus.
    r7 = winner.report.score_breakdown.get("R7_full_slabs_in_doorways", 0.0)
    assert r7 <= 8.0 + 1e-6, (
        f"R7 reward is {r7}; pre-fix bug awarded +16 by counting "
        f"two pieces each covering half the doorway."
    )


def test_l_shape_winner_design_score_at_most_108():
    """Headline number from the validation report: pre-fix winner
    scored 116 (108 + 8 from the inflated R7). Post-fix maximum is
    108 (100 baseline + 8 for one spanned doorway). Any winner above
    108 means the R7 fix regressed."""
    geom = load_target_geometry_from_dxf(EX_L)
    result = select_best_layout(geom, _real_inventory(), _l_shape_plan())
    winner = result.selected
    assert winner.design_score <= 108.0 + 1e-6
