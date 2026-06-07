"""Targeted tests for the architectural-aware candidate selector.

Scope is intentionally narrow per the cost-control rules: just the
selector's own behaviour. The rule evaluator and seam detector have
their own test files; we don't re-cover them here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from placement_engine.architectural import (
    DEFAULT_CANDIDATE_ANCHOR_MODES,
    STRATEGY_ANCHOR,
    STRATEGY_COLUMN_ALIGNED,
    STRATEGY_DOORWAY_CENTRED,
    STRATEGY_GRID_OFFSET,
    ArchitecturalPlan,
    CandidateSelectionResult,
    CandidateSpec,
    Column,
    Doorway,
    LayoutCandidate,
    Space,
    enumerate_candidate_specs,
    load_architectural_plan,
    select_best_layout,
)
from placement_engine.architectural.rules import (
    PieceEvaluation,
    RULE_MIN_PIECE_SIZE,
    RuleReport,
    RuleResult,
    STATUS_PASS,
    STATUS_VIOLATION,
)
from placement_engine.architectural.schema import VISIBILITY_HIGH
from placement_engine.architectural.selector import (
    _pick_winner,
)
from placement_engine.layout import (
    ANCHOR_BOTTOM_LEFT,
    ANCHOR_BOTTOM_RIGHT,
    ANCHOR_TOP_LEFT,
    ANCHOR_TOP_RIGHT,
)
from placement_engine.target_area import (
    TargetGeometry,
    load_target_geometry_from_dxf,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
EX_L = REPO_ROOT / "examples/cad_inputs/demo/demo_l_shape_floor.dxf"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@dataclass
class _Slab:
    width_mm: float
    height_mm: float


def _real_inventory() -> list[_Slab]:
    return [
        _Slab(1590, 1590), _Slab(1590, 1980), _Slab(1550, 2040),
        _Slab(1590, 2200), _Slab(1570, 2320),
        _Slab(1600, 2500), _Slab(1610, 2620),
    ]


def _l_shape_plan() -> ArchitecturalPlan:
    """Minimal plan for the L-shape demo fixture."""
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


def _fake_candidate(
    candidate_id: str, anchor: str, score: float,
    *, is_valid: bool = True, disqs: list[str] | None = None,
    hard_count: int = 0,
) -> LayoutCandidate:
    """Build a LayoutCandidate with a hand-built RuleReport — bypasses
    layout generation so we can test selector logic against scenarios
    the current generator can't naturally produce (e.g. mixed
    validity across candidates)."""
    report = RuleReport(
        target_id="t", target_name="t",
        source_layout_path="", source_cut_list_path="",
        architectural_plan=ArchitecturalPlan(target_id="t"),
        pieces=[], seams=[],
        rules=[
            RuleResult(
                rule_id=RULE_MIN_PIECE_SIZE,
                status=STATUS_VIOLATION if not is_valid else STATUS_PASS,
                count=hard_count,
                message="synthetic",
                score_delta=-100.0 * hard_count if not is_valid else 0.0,
            ),
        ],
        design_score=score,
        score_breakdown={RULE_MIN_PIECE_SIZE: 0.0},
        hard_violation_count=hard_count,
    )
    return LayoutCandidate(
        candidate_id=candidate_id,
        anchor_mode=anchor,
        layout=None,  # type: ignore[arg-type]
        report=report,
        is_valid=is_valid,
        disqualifications=list(disqs or []),
    )


# ---------------------------------------------------------------------------
# 1. Candidate generation: anchor + grid_offset baseline, plus
#    doorway/column-aware specs when the plan declares them
# ---------------------------------------------------------------------------


def test_select_best_layout_includes_four_anchor_candidates():
    """The four global anchor candidates are always present, regardless
    of plan content. Doorway/column specs are additive."""
    geom = load_target_geometry_from_dxf(EX_L)
    result = select_best_layout(geom, _real_inventory(), _l_shape_plan())
    assert isinstance(result, CandidateSelectionResult)
    anchor_cands = [c for c in result.candidates if c.strategy == STRATEGY_ANCHOR]
    assert len(anchor_cands) == 4
    assert {c.anchor_mode for c in anchor_cands} == set(DEFAULT_CANDIDATE_ANCHOR_MODES)


def test_l_shape_demo_includes_doorway_centred_candidate():
    """The L-shape plan declares one doorway → exactly one
    doorway_centred candidate joins the pool."""
    geom = load_target_geometry_from_dxf(EX_L)
    result = select_best_layout(geom, _real_inventory(), _l_shape_plan())
    doorway_cands = [
        c for c in result.candidates
        if c.strategy == STRATEGY_DOORWAY_CENTRED
    ]
    assert len(doorway_cands) == 1
    # The plan's only doorway is the main entrance.
    assert "main_entrance" in doorway_cands[0].candidate_id


def test_l_shape_includes_grid_offset_candidates():
    geom = load_target_geometry_from_dxf(EX_L)
    result = select_best_layout(geom, _real_inventory(), _l_shape_plan())
    offsets = [
        c for c in result.candidates
        if c.strategy == STRATEGY_GRID_OFFSET
    ]
    assert len(offsets) == 2  # half-x + half-y


def test_l_shape_total_candidate_count_is_seven():
    """4 anchor + 2 grid_offset + 1 doorway_centred = 7."""
    geom = load_target_geometry_from_dxf(EX_L)
    result = select_best_layout(geom, _real_inventory(), _l_shape_plan())
    assert len(result.candidates) == 7


def test_candidate_ids_are_unique_and_sequential():
    geom = load_target_geometry_from_dxf(EX_L)
    result = select_best_layout(geom, _real_inventory(), _l_shape_plan())
    ids = [c.candidate_id for c in result.candidates]
    assert len(set(ids)) == len(ids)
    # IDs are C01_, C02_, …, in order.
    for i, c in enumerate(result.candidates, start=1):
        assert c.candidate_id.startswith(f"C{i:02d}_")


def test_v1_limitations_surfaced_in_result():
    geom = load_target_geometry_from_dxf(EX_L)
    result = select_best_layout(geom, _real_inventory(), _l_shape_plan())
    # The global-anchor limitation is the headline one.
    assert any("global_anchor_only" in s for s in result.v1_limitations)


# ---------------------------------------------------------------------------
# 2. Hard-rule rejection: sub-100 mm pieces disqualify
# ---------------------------------------------------------------------------


def test_sub_100mm_piece_marks_anchor_candidates_invalid():
    """A 1640 mm × 2200 mm floor with median tile 1590 × 2200 leaves
    a 50 mm leftover. With absorption disabled, every anchor-mode
    candidate produces that 50 mm sliver and is flagged R1-invalid.

    Note: grid_offset candidates may escape the gate when their
    half-tile shift coincidentally places the leftover at a wider
    position — that's the strategy doing its job, not a regression.
    The contract under test is that the hard-rule gate WILL reject
    every candidate that genuinely produces a sub-minimum piece.
    """
    geom = TargetGeometry(
        target_id="rect_with_sliver", name="rect_with_sliver",
        boundary=[(0, 0), (1640, 0), (1640, 2200), (0, 2200)],
    )
    plan = ArchitecturalPlan(target_id="rect_with_sliver")
    result = select_best_layout(
        geom, _real_inventory(), plan,
        enable_absorption=False,
    )
    anchor_cands = [c for c in result.candidates if c.strategy == STRATEGY_ANCHOR]
    assert len(anchor_cands) == 4
    for c in anchor_cands:
        assert not c.is_valid, (
            f"{c.candidate_id} should be invalid (50 mm sliver) "
            f"but was marked valid"
        )
        assert RULE_MIN_PIECE_SIZE in c.disqualifications
    # Every R1-invalid candidate must have at least one piece in the
    # final layout below the plan's min.
    for c in anchor_cands:
        assert any(
            min(p.bbox_width_mm, p.bbox_height_mm) < 100
            for p in c.report.pieces
        ), (
            f"{c.candidate_id} flagged R1 but no piece appears below "
            f"the 100 mm minimum"
        )


def test_grid_offset_with_partial_coverage_is_disqualified():
    """Companion to the test above: with the hard coverage rule in
    place, grid_offset candidates that produce missing-floor strips
    (because their origin shifts tiles off the bbox left edge) are
    correctly marked invalid even when they escape R1. This is the
    expected behaviour after the coverage hard rule landed — partial
    coverage is never acceptable.
    """
    geom = TargetGeometry(
        target_id="rect_with_sliver", name="rect_with_sliver",
        boundary=[(0, 0), (1640, 0), (1640, 2200), (0, 2200)],
    )
    plan = ArchitecturalPlan(target_id="rect_with_sliver")
    result = select_best_layout(
        geom, _real_inventory(), plan,
        enable_absorption=False,
    )
    # offset_half_x shifts origin to x=795, leaving x∈[0, 795] bare.
    # The coverage rule must catch this even though R1 might pass.
    half_x = next(
        c for c in result.candidates
        if c.candidate_id.endswith("_offset_half_x")
    )
    assert not half_x.is_valid, (
        "offset_half_x should be invalid due to coverage shortfall "
        "(it leaves a bare strip on the left)"
    )
    assert "R9_full_coverage" in half_x.disqualifications


def test_invalid_candidate_cannot_win_when_a_valid_one_exists():
    """Mixed-validity case (synthetic): one valid candidate at score
    50 must beat an invalid one at score 200."""
    cands = [
        _fake_candidate("C01_a", "a", score=200.0,
                         is_valid=False, hard_count=2,
                         disqs=[RULE_MIN_PIECE_SIZE]),
        _fake_candidate("C02_b", "b", score=50.0, is_valid=True),
        _fake_candidate("C03_c", "c", score=30.0, is_valid=True),
    ]
    winner_id, reason, valid_count = _pick_winner(cands)
    assert winner_id == "C02_b"
    assert valid_count == 2
    assert "200" not in reason  # the invalid 200-scorer never reaches the reason


def test_all_invalid_falls_back_to_least_bad():
    """When no candidate clears the hard gate, the selector picks the
    least-bad (fewest hard violations, then highest score) and the
    reason calls it out."""
    cands = [
        _fake_candidate("C01_a", "a", score=10.0,
                         is_valid=False, hard_count=5,
                         disqs=[RULE_MIN_PIECE_SIZE]),
        _fake_candidate("C02_b", "b", score=80.0,
                         is_valid=False, hard_count=1,
                         disqs=[RULE_MIN_PIECE_SIZE]),
        _fake_candidate("C03_c", "c", score=50.0,
                         is_valid=False, hard_count=3,
                         disqs=[RULE_MIN_PIECE_SIZE]),
    ]
    winner_id, reason, valid_count = _pick_winner(cands)
    assert winner_id == "C02_b"  # highest score among invalids
    assert valid_count == 0
    assert "every candidate failed" in reason


# ---------------------------------------------------------------------------
# 3. Architectural scoring drives selection (not first-candidate)
# ---------------------------------------------------------------------------


def test_winner_is_highest_design_score_among_valid_candidates():
    cands = [
        _fake_candidate("C01_a", "a", score=42.0, is_valid=True),
        _fake_candidate("C02_b", "b", score=99.0, is_valid=True),
        _fake_candidate("C03_c", "c", score=75.0, is_valid=True),
    ]
    winner_id, reason, valid_count = _pick_winner(cands)
    assert winner_id == "C02_b"
    assert valid_count == 3


def test_deterministic_tie_break_when_scores_match():
    """All valid + tied scores → alphabetical candidate_id wins."""
    cands = [
        _fake_candidate("C03_top_right", "top_right", score=100.0, is_valid=True),
        _fake_candidate("C01_bottom_left", "bottom_left", score=100.0, is_valid=True),
        _fake_candidate("C02_top_left", "top_left", score=100.0, is_valid=True),
    ]
    winner_id, reason, _ = _pick_winner(cands)
    assert winner_id == "C01_bottom_left"
    assert "tied" in reason


def test_l_shape_demo_selects_a_valid_candidate_with_positive_score():
    """End-to-end sanity: the L-shape demo plan should produce a
    non-empty valid pool with a positive-scoring winner. The exact
    valid count depends on which strategies happen to clear R1 on
    the L-shape geometry — we assert the contract, not the count.
    """
    geom = load_target_geometry_from_dxf(EX_L)
    result = select_best_layout(geom, _real_inventory(), _l_shape_plan())
    selected = result.selected
    assert selected.is_valid
    # All 4 anchor candidates clear R1 (post-absorption); other
    # strategies may or may not depending on origin choice.
    assert result.valid_candidate_count >= 4
    assert selected.design_score > 0
    # The reason should reference the score, not a fallback.
    assert "every candidate failed" not in result.selection_reason


# ---------------------------------------------------------------------------
# Comparison summary shape
# ---------------------------------------------------------------------------


def test_summary_dict_exposes_key_breakdowns():
    geom = load_target_geometry_from_dxf(EX_L)
    result = select_best_layout(geom, _real_inventory(), _l_shape_plan())
    for c in result.candidates:
        s = c.summary_dict()
        # The four headline buckets the validation report needs.
        for key in (
            "design_score", "is_valid", "disqualifications",
            "doorway_seam_conflicts", "pieces_below_minimum",
            "small_pieces_by_visibility", "column_seam_rewards",
            "rule_status", "score_breakdown",
        ):
            assert key in s, f"summary_dict missing {key!r}"


# ---------------------------------------------------------------------------
# Determinism on a real fixture
# ---------------------------------------------------------------------------


def test_selector_is_deterministic_on_l_shape():
    geom = load_target_geometry_from_dxf(EX_L)
    inv = _real_inventory()
    plan = _l_shape_plan()
    winners = {
        select_best_layout(geom, inv, plan).selected_candidate_id
        for _ in range(3)
    }
    assert len(winners) == 1


# ---------------------------------------------------------------------------
# Defensive
# ---------------------------------------------------------------------------


def test_empty_candidate_specs_raises():
    geom = load_target_geometry_from_dxf(EX_L)
    with pytest.raises(ValueError, match="at least one"):
        select_best_layout(
            geom, _real_inventory(), _l_shape_plan(),
            candidate_specs=[],
        )


# ---------------------------------------------------------------------------
# Strategy enumeration — unit tests that don't run the layout engine
# ---------------------------------------------------------------------------


def _bbox_geometry(w: float = 8000, h: float = 4000):
    return TargetGeometry(
        target_id="rect", name="rect",
        boundary=[(0, 0), (w, 0), (w, h), (0, h)],
    )


def test_enumerate_empty_plan_yields_anchor_plus_grid_offset_only():
    """A plan with no doorways and no columns produces only the
    4 anchor + 2 grid_offset baseline."""
    plan = ArchitecturalPlan(target_id="empty")
    specs = enumerate_candidate_specs(_bbox_geometry(), 1590, 2200, plan)
    by_strategy = {s.strategy: 0 for s in specs}
    for s in specs:
        by_strategy[s.strategy] += 1
    assert by_strategy.get(STRATEGY_ANCHOR) == 4
    assert by_strategy.get(STRATEGY_GRID_OFFSET) == 2
    assert by_strategy.get(STRATEGY_DOORWAY_CENTRED, 0) == 0
    assert by_strategy.get(STRATEGY_COLUMN_ALIGNED, 0) == 0
    assert len(specs) == 6


def test_enumerate_with_doorway_adds_one_per_doorway():
    plan = ArchitecturalPlan(
        target_id="t",
        doorways=[
            Doorway("d1", segment=((3500, 0), (4500, 0))),
            Doorway("d2", segment=((0, 1500), (0, 2500))),
        ],
    )
    specs = enumerate_candidate_specs(_bbox_geometry(), 1590, 2200, plan)
    doorways = [s for s in specs if s.strategy == STRATEGY_DOORWAY_CENTRED]
    assert len(doorways) == 2
    assert {s.candidate_id.split("_doorway_", 1)[1] for s in doorways} == {"d1", "d2"}


def test_doorway_origin_centres_a_tile_on_midpoint_x_for_horizontal_doorway():
    """A horizontal doorway centred on x=4000 with tile_w=1590 should
    place the grid origin at x = 4000 - 1590/2 = 3205."""
    plan = ArchitecturalPlan(
        target_id="t",
        doorways=[Doorway("d1", segment=((3500, 0), (4500, 0)))],
    )
    specs = enumerate_candidate_specs(_bbox_geometry(), 1590, 2200, plan)
    door = next(s for s in specs if s.strategy == STRATEGY_DOORWAY_CENTRED)
    ox, oy = door.origin
    assert ox == pytest.approx(3205.0)
    # y axis unchanged from bbox y0=0 for a horizontal doorway.
    assert oy == pytest.approx(0.0)


def test_doorway_origin_handles_vertical_doorway_via_y_axis():
    """A vertical doorway should shift the grid on the y axis instead."""
    plan = ArchitecturalPlan(
        target_id="t",
        doorways=[Doorway("d1", segment=((0, 1500), (0, 2500)))],
    )
    specs = enumerate_candidate_specs(_bbox_geometry(), 1590, 2200, plan)
    door = next(s for s in specs if s.strategy == STRATEGY_DOORWAY_CENTRED)
    ox, oy = door.origin
    # x stays at bbox x0; y is centred on midpoint y=2000.
    assert ox == pytest.approx(0.0)
    assert oy == pytest.approx(2000.0 - 2200 / 2)


def test_enumerate_with_column_adds_two_per_column():
    plan = ArchitecturalPlan(
        target_id="t",
        columns=[
            Column("col1", polygon=[
                (3000, 1500), (3500, 1500), (3500, 2000), (3000, 2000),
            ]),
        ],
    )
    specs = enumerate_candidate_specs(_bbox_geometry(), 1590, 2200, plan)
    cols = [s for s in specs if s.strategy == STRATEGY_COLUMN_ALIGNED]
    assert len(cols) == 2  # left + right edges
    origins_x = sorted(s.origin[0] for s in cols)
    assert origins_x == [3000.0, 3500.0]  # left edge then right edge


def test_grid_offset_specs_use_half_tile_dimensions():
    plan = ArchitecturalPlan(target_id="t")
    specs = enumerate_candidate_specs(_bbox_geometry(), 1590, 2200, plan)
    offsets = {s.candidate_id.split("_", 1)[1]: s
               for s in specs if s.strategy == STRATEGY_GRID_OFFSET}
    assert offsets["offset_half_x"].origin == pytest.approx((795.0, 0.0))
    assert offsets["offset_half_y"].origin == pytest.approx((0.0, 1100.0))


def test_strategy_and_description_surface_in_summary_dict():
    geom = load_target_geometry_from_dxf(EX_L)
    result = select_best_layout(geom, _real_inventory(), _l_shape_plan())
    for c in result.candidates:
        s = c.summary_dict()
        assert "strategy" in s
        assert "description" in s
        assert s["strategy"] in (
            STRATEGY_ANCHOR, STRATEGY_GRID_OFFSET,
            STRATEGY_DOORWAY_CENTRED, STRATEGY_COLUMN_ALIGNED,
        )


def test_sliver_handling_note_appears_in_result_dict():
    geom = load_target_geometry_from_dxf(EX_L)
    result = select_best_layout(geom, _real_inventory(), _l_shape_plan())
    d = result.to_dict()
    assert "sliver_handling_note" in d
    assert "absorption" in d["sliver_handling_note"].lower()


def test_sliver_handling_note_appears_in_layout_json():
    """Layout JSON must surface the same clarifying note so consumers
    reading layout.json directly don't misread pre-absorption sliver
    counts as final R1 violations."""
    geom = load_target_geometry_from_dxf(EX_L)
    result = select_best_layout(geom, _real_inventory(), _l_shape_plan())
    selected_layout = result.layout.to_dict()
    assert "candidate_evaluations_note" in selected_layout["grid"]


def test_explicit_spec_with_origin_yields_anchor_label_not_named_mode():
    """Specs with an explicit origin should NOT be reported as a named
    anchor mode (they bypassed the named-anchor path). The selector
    surfaces the layout layer's actual anchor field instead."""
    geom = load_target_geometry_from_dxf(EX_L)
    spec = CandidateSpec(
        candidate_id="manual_origin",
        strategy=STRATEGY_GRID_OFFSET,
        description="explicit test origin",
        origin=(100.0, 100.0),
    )
    result = select_best_layout(
        geom, _real_inventory(), _l_shape_plan(),
        candidate_specs=[spec],
    )
    cand = result.candidates[0]
    # Either "explicit_origin" or whatever the layout layer reports —
    # crucially NOT one of the four named anchor modes.
    assert cand.anchor_mode not in set(DEFAULT_CANDIDATE_ANCHOR_MODES)
