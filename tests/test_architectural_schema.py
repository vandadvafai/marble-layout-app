"""Tests for the architectural plan schema + JSON loader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from placement_engine.architectural import (
    DEFAULT_MIN_PIECE_WIDTH_MM,
    DEFAULT_SMALL_PIECE_THRESHOLD_MM,
    MATCHING_BOOK_MATCH,
    MATCHING_NONE,
    MATCHING_VEIN_MATCH,
    SUPPORTED_MATCHING_MODES,
    SUPPORTED_VISIBILITY_LEVELS,
    VISIBILITY_HIGH,
    VISIBILITY_LOW,
    VISIBILITY_MEDIUM,
    ArchitecturalPlan,
    Column,
    Doorway,
    Space,
    load_architectural_plan,
    write_architectural_plan_json,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# defaults + constants
# ---------------------------------------------------------------------------


def test_default_min_piece_width_is_10cm():
    """The 'absolute worst-case minimum piece width is 10 cm' rule
    must be the schema default — no hidden overrides."""
    assert DEFAULT_MIN_PIECE_WIDTH_MM == 100.0


def test_supported_matching_modes_includes_all_three():
    assert MATCHING_NONE in SUPPORTED_MATCHING_MODES
    assert MATCHING_VEIN_MATCH in SUPPORTED_MATCHING_MODES
    assert MATCHING_BOOK_MATCH in SUPPORTED_MATCHING_MODES


def test_supported_visibility_levels_covers_five_steps():
    """Designers asked for very-high → very-low; five steps fit the
    visibility weight curve cleanly."""
    assert len(SUPPORTED_VISIBILITY_LEVELS) == 5


# ---------------------------------------------------------------------------
# dataclass construction + to_dict round-trip
# ---------------------------------------------------------------------------


def test_minimal_plan_has_sensible_defaults():
    plan = ArchitecturalPlan(target_id="t")
    assert plan.matching_mode == MATCHING_NONE
    assert plan.min_piece_width_mm == DEFAULT_MIN_PIECE_WIDTH_MM
    assert plan.small_piece_threshold_mm == DEFAULT_SMALL_PIECE_THRESHOLD_MM
    assert plan.spaces == []
    assert plan.doorways == []
    assert plan.columns == []


def test_to_dict_round_trip_via_disk(tmp_path: Path):
    plan = ArchitecturalPlan(
        target_id="t",
        spaces=[Space("s0", "Room", polygon=[(0, 0), (1, 0), (1, 1), (0, 1)],
                      visibility=VISIBILITY_HIGH)],
        doorways=[Doorway("d0", segment=((0, 0), (1, 0)),
                          is_main_entrance=True, width_mm=900)],
        columns=[Column("c0", polygon=[(2, 2), (3, 2), (3, 3), (2, 3)])],
    )
    path = write_architectural_plan_json(plan, tmp_path / "plan.json")
    reload = load_architectural_plan(path)
    assert reload.target_id == "t"
    assert len(reload.spaces) == 1
    assert reload.spaces[0].visibility == VISIBILITY_HIGH
    assert reload.doorways[0].is_main_entrance is True
    assert reload.doorways[0].width_mm == 900.0
    assert len(reload.columns) == 1


def test_to_dict_keys_are_stable():
    plan = ArchitecturalPlan(target_id="t")
    d = plan.to_dict()
    expected = {
        "target_id", "matching_mode",
        "min_piece_width_mm", "min_piece_height_mm",
        "small_piece_threshold_mm", "column_seam_proximity_mm",
        "spaces", "doorways", "columns", "notes",
    }
    assert set(d.keys()) == expected


# ---------------------------------------------------------------------------
# loader — error handling
# ---------------------------------------------------------------------------


def test_load_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        load_architectural_plan("/tmp/does-not-exist-99999.json")


def test_load_unsupported_matching_mode_raises(tmp_path: Path):
    p = tmp_path / "plan.json"
    p.write_text(json.dumps({"target_id": "t", "matching_mode": "diagonal"}))
    with pytest.raises(ValueError, match="unsupported matching_mode"):
        load_architectural_plan(p)


def test_load_unsupported_visibility_raises(tmp_path: Path):
    p = tmp_path / "plan.json"
    p.write_text(json.dumps({
        "target_id": "t",
        "spaces": [{"space_id": "s", "polygon": [[0, 0], [1, 0], [1, 1]],
                    "visibility": "epic"}],
    }))
    with pytest.raises(ValueError, match="unsupported visibility"):
        load_architectural_plan(p)


def test_load_doorway_requires_two_point_segment(tmp_path: Path):
    p = tmp_path / "plan.json"
    p.write_text(json.dumps({
        "target_id": "t",
        "doorways": [{"doorway_id": "d", "segment": [[0, 0]]}],
    }))
    with pytest.raises(ValueError, match="two-point segment"):
        load_architectural_plan(p)


def test_load_skips_unknown_top_level_fields(tmp_path: Path):
    """Unknown fields shouldn't break the loader — the JSON can carry
    extra metadata for downstream tooling."""
    p = tmp_path / "plan.json"
    p.write_text(json.dumps({
        "target_id": "t",
        "version": "1.0",
        "designer_notes": "hello",
        "spaces": [],
    }))
    plan = load_architectural_plan(p)
    assert plan.target_id == "t"


# ---------------------------------------------------------------------------
# demo fixtures load cleanly
# ---------------------------------------------------------------------------


def test_demo_l_shape_plan_loads():
    plan = load_architectural_plan(
        REPO_ROOT / "examples/architectural/demo_l_shape_floor.json",
    )
    assert plan.target_id == "demo_l_shape_floor"
    assert len(plan.spaces) == 1
    assert plan.spaces[0].visibility == VISIBILITY_HIGH
    assert any(d.is_main_entrance for d in plan.doorways)


def test_demo_apartment_plan_loads_with_columns():
    plan = load_architectural_plan(
        REPO_ROOT / "examples/architectural/demo_irregular_apartment_floor.json",
    )
    assert len(plan.columns) == 3
    assert len(plan.doorways) == 2


def test_demo_rectangle_plan_loads():
    plan = load_architectural_plan(
        REPO_ROOT / "examples/architectural/demo_rectangle_floor.json",
    )
    assert plan.matching_mode == MATCHING_NONE
