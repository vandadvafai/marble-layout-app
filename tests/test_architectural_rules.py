"""Tests for the architectural rule evaluator."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from placement_engine.architectural import (
    MATCHING_NONE,
    VISIBILITY_HIGH,
    VISIBILITY_LOW,
    ArchitecturalPlan,
    Column,
    Doorway,
    PieceEvaluation,
    Seam,
    SeamEvaluation,
    Space,
    detect_seams,
    evaluate_layout,
    load_architectural_plan,
    write_rule_report_json,
)
from placement_engine.architectural.rules import (
    RULE_ABSORBED_SLIVERS,
    RULE_FULL_SLABS_IN_DOORWAYS,
    RULE_MATCHING_MODE,
    RULE_MIN_PIECE_SIZE,
    RULE_NO_SEAMS_IN_DOORWAYS,
    RULE_ONE_DIRECTION_PER_SPACE,
    RULE_SEAMS_NEAR_COLUMNS,
    RULE_SMALL_PIECES_IN_LOW_VISIBILITY,
    STATUS_INFO,
    STATUS_NOT_APPLICABLE,
    STATUS_PASS,
    STATUS_REWARD,
    STATUS_VIOLATION,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _rect_piece(
    piece_id: str, x0: float, y0: float, x1: float, y1: float,
    *,
    zone_id: str = "z0",
    nominal_w: float = 1590,
    nominal_h: float = 2200,
    notes: list[str] | None = None,
    is_full: bool | None = None,
) -> dict[str, Any]:
    """Build a layout-JSON-shaped piece dict for a clean rectangle."""
    poly = [
        (x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0),
    ]
    w, h = x1 - x0, y1 - y0
    is_full_default = (
        is_full if is_full is not None else (w == nominal_w and h == nominal_h)
    )
    return {
        "piece_id": piece_id,
        "row": 0, "col": 0,
        "nominal_x_mm": x0, "nominal_y_mm": y0,
        "nominal_width_mm": nominal_w, "nominal_height_mm": nominal_h,
        "actual_cut_polygon": [list(pt) for pt in poly],
        "bounding_width_mm": w, "bounding_height_mm": h,
        "actual_area_m2": (w * h) / 1e6,
        "is_full_tile": is_full_default,
        "is_edge_piece": not is_full_default,
        "intersects_hole": False,
        "interior_holes": [],
        "notes": notes or [],
        "zone_id": zone_id,
    }


def _layout(pieces: list[dict[str, Any]], target_id: str = "t") -> dict[str, Any]:
    return {
        "target": {"target_id": target_id, "name": "t"},
        "pieces": pieces,
        "grid": {"tile_width_mm": 1590, "tile_height_mm": 2200},
    }


def _empty_cl() -> dict[str, Any]:
    return {"pieces": [], "summary": {}}


# ---------------------------------------------------------------------------
# seam detection
# ---------------------------------------------------------------------------


def test_two_pieces_sharing_full_edge_produce_one_seam():
    pieces = [
        _rect_piece("A", 0, 0, 1590, 2200),
        _rect_piece("B", 1590, 0, 3180, 2200),
    ]
    seams = detect_seams(pieces)
    assert len(seams) == 1
    assert seams[0].length_mm == pytest.approx(2200.0)
    assert {seams[0].piece_a_id, seams[0].piece_b_id} == {"A", "B"}


def test_seam_count_for_a_2x2_grid_is_four():
    """2 × 2 grid → 2 horizontal seams + 2 vertical seams = 4."""
    pieces = [
        _rect_piece("00", 0, 0, 1590, 2200),
        _rect_piece("10", 1590, 0, 3180, 2200),
        _rect_piece("01", 0, 2200, 1590, 4400),
        _rect_piece("11", 1590, 2200, 3180, 4400),
    ]
    seams = detect_seams(pieces)
    assert len(seams) == 4


def test_pieces_not_touching_have_no_seam():
    pieces = [
        _rect_piece("A", 0, 0, 1000, 1000),
        _rect_piece("B", 2000, 2000, 3000, 3000),
    ]
    assert detect_seams(pieces) == []


def test_corner_touch_does_not_produce_a_seam():
    """Two pieces sharing only a single point at the corner — not a
    visible seam, so should be ignored."""
    pieces = [
        _rect_piece("A", 0, 0, 1000, 1000),
        _rect_piece("B", 1000, 1000, 2000, 2000),
    ]
    assert detect_seams(pieces) == []


# ---------------------------------------------------------------------------
# R1 — minimum piece size
# ---------------------------------------------------------------------------


def _find_rule(report, rule_id: str):
    return next(r for r in report.rules if r.rule_id == rule_id)


def test_r1_flags_pieces_below_minimum():
    """A 50 mm wide piece must violate R1 under the default 100 mm policy."""
    layout = _layout([
        _rect_piece("A", 0, 0, 50, 1000),
        _rect_piece("B", 50, 0, 1640, 1000),
    ])
    plan = ArchitecturalPlan(target_id="t")
    report = evaluate_layout(layout, _empty_cl(), plan)
    r1 = _find_rule(report, RULE_MIN_PIECE_SIZE)
    assert r1.status == STATUS_VIOLATION
    assert r1.count == 1
    assert "A" in r1.affected_ids
    assert r1.score_delta < 0


def test_r1_passes_when_all_pieces_above_minimum():
    layout = _layout([
        _rect_piece("A", 0, 0, 1590, 2200),
        _rect_piece("B", 1590, 0, 3180, 2200),
    ])
    plan = ArchitecturalPlan(target_id="t")
    report = evaluate_layout(layout, _empty_cl(), plan)
    r1 = _find_rule(report, RULE_MIN_PIECE_SIZE)
    assert r1.status == STATUS_PASS
    assert r1.count == 0


# ---------------------------------------------------------------------------
# R2 — no seams in doorways
# ---------------------------------------------------------------------------


def test_r2_flags_seam_crossing_a_doorway():
    """A horizontal seam at y=2200 running across [0,3180] should
    intersect a doorway segment at (1500, 1500)→(1500, 3000)."""
    pieces = [
        _rect_piece("A", 0, 0, 1590, 2200),
        _rect_piece("B", 1590, 0, 3180, 2200),
        _rect_piece("C", 0, 2200, 1590, 4400),
        _rect_piece("D", 1590, 2200, 3180, 4400),
    ]
    plan = ArchitecturalPlan(
        target_id="t",
        doorways=[
            Doorway("main", segment=((1500, 1500), (1500, 3000)),
                    is_main_entrance=True),
        ],
    )
    report = evaluate_layout(_layout(pieces), _empty_cl(), plan)
    r2 = _find_rule(report, RULE_NO_SEAMS_IN_DOORWAYS)
    assert r2.status == STATUS_VIOLATION
    assert r2.count >= 1
    # Main-entrance crossings get the bigger penalty.
    assert r2.score_delta <= -50.0


def test_r2_not_applicable_when_no_doorways():
    layout = _layout([
        _rect_piece("A", 0, 0, 1590, 2200),
        _rect_piece("B", 1590, 0, 3180, 2200),
    ])
    plan = ArchitecturalPlan(target_id="t")
    report = evaluate_layout(layout, _empty_cl(), plan)
    r2 = _find_rule(report, RULE_NO_SEAMS_IN_DOORWAYS)
    assert r2.status == STATUS_NOT_APPLICABLE


def test_r2_passes_when_seam_avoids_doorway():
    """The seam at x=1590 between A and B does NOT cross a doorway
    at the far right wall."""
    layout = _layout([
        _rect_piece("A", 0, 0, 1590, 2200),
        _rect_piece("B", 1590, 0, 3180, 2200),
    ])
    plan = ArchitecturalPlan(
        target_id="t",
        doorways=[Doorway("d", segment=((3180, 800), (3180, 1400)))],
    )
    report = evaluate_layout(layout, _empty_cl(), plan)
    r2 = _find_rule(report, RULE_NO_SEAMS_IN_DOORWAYS)
    assert r2.status == STATUS_PASS


# ---------------------------------------------------------------------------
# R3 — one direction per space
# ---------------------------------------------------------------------------


def test_r3_passes_when_one_space_has_uniform_nominal_size():
    pieces = [
        _rect_piece("A", 0, 0, 1590, 2200),
        _rect_piece("B", 1590, 0, 3180, 2200),
    ]
    plan = ArchitecturalPlan(
        target_id="t",
        spaces=[Space("main", "Room",
                      polygon=[(0, 0), (3180, 0), (3180, 2200), (0, 2200)],
                      visibility=VISIBILITY_HIGH)],
    )
    report = evaluate_layout(_layout(pieces), _empty_cl(), plan)
    assert _find_rule(report, RULE_ONE_DIRECTION_PER_SPACE).status == STATUS_PASS


def test_r3_flags_mixed_orientations_in_one_space():
    """Pieces with different nominal w×h pairs in the same space →
    R3 violation."""
    pieces = [
        _rect_piece("A", 0, 0, 1590, 2200, nominal_w=1590, nominal_h=2200),
        _rect_piece("B", 1590, 0, 3790, 1590, nominal_w=2200, nominal_h=1590),
    ]
    plan = ArchitecturalPlan(
        target_id="t",
        spaces=[Space("main", "Room",
                      polygon=[(0, 0), (3790, 0), (3790, 2200), (0, 2200)])],
    )
    report = evaluate_layout(_layout(pieces), _empty_cl(), plan)
    r3 = _find_rule(report, RULE_ONE_DIRECTION_PER_SPACE)
    assert r3.status == STATUS_VIOLATION
    assert "main" in r3.affected_ids


# ---------------------------------------------------------------------------
# R5 — seams near columns earn a reward
# ---------------------------------------------------------------------------


def test_r5_rewards_seams_close_to_a_column():
    pieces = [
        _rect_piece("A", 0, 0, 1590, 2200),
        _rect_piece("B", 1590, 0, 3180, 2200),
    ]
    # Column sits right at the A/B seam.
    plan = ArchitecturalPlan(
        target_id="t",
        columns=[Column("C1", polygon=[
            (1500, 1500), (1700, 1500), (1700, 1700), (1500, 1700),
        ])],
    )
    report = evaluate_layout(_layout(pieces), _empty_cl(), plan)
    r5 = _find_rule(report, RULE_SEAMS_NEAR_COLUMNS)
    assert r5.status == STATUS_REWARD
    assert r5.score_delta > 0
    assert r5.count >= 1


def test_r5_not_applicable_when_no_columns():
    layout = _layout([_rect_piece("A", 0, 0, 1590, 2200)])
    plan = ArchitecturalPlan(target_id="t")
    report = evaluate_layout(layout, _empty_cl(), plan)
    assert _find_rule(report, RULE_SEAMS_NEAR_COLUMNS).status == STATUS_NOT_APPLICABLE


# ---------------------------------------------------------------------------
# R6 — small pieces in low-visibility spaces are cheaper
# ---------------------------------------------------------------------------


def test_r6_small_piece_penalty_scales_with_visibility():
    """Same small piece, two layouts — high visibility scores worse."""
    small_piece = _rect_piece("SP", 0, 0, 200, 200)
    high_plan = ArchitecturalPlan(
        target_id="t",
        spaces=[Space("main", "Room",
                      polygon=[(0, 0), (200, 0), (200, 200), (0, 200)],
                      visibility=VISIBILITY_HIGH)],
    )
    low_plan = ArchitecturalPlan(
        target_id="t",
        spaces=[Space("main", "Room",
                      polygon=[(0, 0), (200, 0), (200, 200), (0, 200)],
                      visibility=VISIBILITY_LOW)],
    )
    high_report = evaluate_layout(_layout([small_piece]), _empty_cl(), high_plan)
    low_report = evaluate_layout(_layout([small_piece]), _empty_cl(), low_plan)
    # Both should flag the small piece, but the high-visibility penalty
    # has a larger magnitude than the low-visibility one.
    high_r6 = _find_rule(high_report, RULE_SMALL_PIECES_IN_LOW_VISIBILITY)
    low_r6 = _find_rule(low_report, RULE_SMALL_PIECES_IN_LOW_VISIBILITY)
    assert high_r6.score_delta < low_r6.score_delta  # more negative = worse


def test_r6_passes_when_no_small_pieces():
    layout = _layout([_rect_piece("A", 0, 0, 1590, 2200)])
    plan = ArchitecturalPlan(target_id="t")
    report = evaluate_layout(layout, _empty_cl(), plan)
    assert _find_rule(report, RULE_SMALL_PIECES_IN_LOW_VISIBILITY).status == STATUS_PASS


# ---------------------------------------------------------------------------
# R7 — full slab covering a doorway is rewarded
# ---------------------------------------------------------------------------


def test_r7_rewards_full_slab_spanning_doorway():
    """A single 1590 × 2200 piece that straddles the doorway line wins R7."""
    layout = _layout([_rect_piece("A", 0, 0, 1590, 2200)])
    plan = ArchitecturalPlan(
        target_id="t",
        doorways=[Doorway("m", segment=((500, 0), (1000, 0)),
                          is_main_entrance=True)],
    )
    report = evaluate_layout(layout, _empty_cl(), plan)
    r7 = _find_rule(report, RULE_FULL_SLABS_IN_DOORWAYS)
    assert r7.status == STATUS_REWARD
    assert r7.score_delta > 0


# ---------------------------------------------------------------------------
# R8 — absorbed slivers are reported (informational)
# ---------------------------------------------------------------------------


def test_r8_lists_absorbed_holder_pieces():
    layout = _layout([
        _rect_piece("A", 0, 0, 1620, 2200,
                    notes=["absorbed_sliver:z0_tile_r0_c0"]),
    ])
    plan = ArchitecturalPlan(target_id="t")
    report = evaluate_layout(layout, _empty_cl(), plan)
    r8 = _find_rule(report, RULE_ABSORBED_SLIVERS)
    assert r8.status == STATUS_INFO
    assert r8.count == 1


# ---------------------------------------------------------------------------
# Demo fixtures — end-to-end smoke
# ---------------------------------------------------------------------------


def test_l_shape_demo_evaluates_without_error():
    plan = load_architectural_plan(
        REPO_ROOT / "examples/architectural/demo_l_shape_floor.json",
    )
    layout = json.loads(
        (REPO_ROOT / "outputs/layouts/demo_l_shape_floor/layout.json").read_text()
    )
    cl = json.loads(
        (REPO_ROOT / "outputs/cut_lists/demo_l_shape_floor/cut_list.json").read_text()
    )
    report = evaluate_layout(layout, cl, plan)
    # R1 must pass on a post-absorption layout (no piece below 100 mm).
    assert _find_rule(report, RULE_MIN_PIECE_SIZE).status == STATUS_PASS
    # Score is in the valid [0, 200] range.
    assert 0 <= report.design_score <= 200


def test_apartment_demo_with_columns_finds_some_seams():
    plan = load_architectural_plan(
        REPO_ROOT / "examples/architectural/demo_irregular_apartment_floor.json",
    )
    layout = json.loads(
        (REPO_ROOT / "outputs/layouts/demo_irregular_apartment_floor/layout.json").read_text()
    )
    cl = json.loads(
        (REPO_ROOT / "outputs/cut_lists/demo_irregular_apartment_floor/cut_list.json").read_text()
    )
    report = evaluate_layout(layout, cl, plan)
    # Apartment has 3 columns; rule R5 must be in a "near-column-aware"
    # state (either reward or pass).
    r5 = _find_rule(report, RULE_SEAMS_NEAR_COLUMNS)
    assert r5.status in {STATUS_PASS, STATUS_REWARD}


# ---------------------------------------------------------------------------
# JSON I/O for the report
# ---------------------------------------------------------------------------


def test_rule_report_json_round_trip(tmp_path: Path):
    layout = _layout([_rect_piece("A", 0, 0, 1590, 2200)])
    plan = ArchitecturalPlan(target_id="t")
    report = evaluate_layout(layout, _empty_cl(), plan)
    path = write_rule_report_json(report, tmp_path / "report.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert set(data.keys()) >= {
        "target_id", "target_name", "source_layout_path",
        "source_cut_list_path", "architectural_plan",
        "pieces", "seams", "rules", "design_score",
        "score_breakdown",
        "hard_violation_count", "soft_violation_count", "reward_count",
    }


def test_rule_report_summary_dict_is_compact():
    layout = _layout([_rect_piece("A", 0, 0, 1590, 2200)])
    plan = ArchitecturalPlan(target_id="t")
    report = evaluate_layout(layout, _empty_cl(), plan)
    s = report.summary_dict()
    # The summary skips the heavy pieces[] / seams[] arrays.
    assert "pieces" not in s
    assert "seams" not in s
    assert "rule_status" in s
    assert "design_score" in s


# ---------------------------------------------------------------------------
# Default-plan resilience: zero spaces is still a valid evaluation
# ---------------------------------------------------------------------------


def test_evaluating_with_no_spaces_falls_back_to_medium_visibility():
    layout = _layout([_rect_piece("A", 0, 0, 200, 200)])
    plan = ArchitecturalPlan(target_id="t")  # zero spaces declared
    report = evaluate_layout(layout, _empty_cl(), plan)
    # Piece gets medium-visibility classification as the default.
    assert report.pieces[0].visibility == "medium"
    # The small-piece rule still fires (the small piece is real).
    assert _find_rule(report, RULE_SMALL_PIECES_IN_LOW_VISIBILITY).count == 1
