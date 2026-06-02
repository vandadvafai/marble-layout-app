"""Tests for `placement_engine.target_area` — V1 rectangular target."""

from __future__ import annotations

import pytest

from placement_engine.target_area import TargetArea, target_area_warnings


def test_valid_target_constructs_cleanly():
    t = TargetArea(
        target_id="room_1",
        name="Living room floor",
        width_mm=5000,
        height_mm=3000,
    )
    assert t.target_id == "room_1"
    assert t.name == "Living room floor"
    assert t.width_mm == 5000
    assert t.height_mm == 3000
    assert t.required_area_m2 is None
    assert t.notes is None


def test_zero_width_raises():
    with pytest.raises(ValueError, match="width_mm"):
        TargetArea(target_id="x", name="x", width_mm=0, height_mm=3000)


def test_negative_width_raises():
    with pytest.raises(ValueError, match="width_mm"):
        TargetArea(target_id="x", name="x", width_mm=-100, height_mm=3000)


def test_zero_height_raises():
    with pytest.raises(ValueError, match="height_mm"):
        TargetArea(target_id="x", name="x", width_mm=5000, height_mm=0)


def test_negative_height_raises():
    with pytest.raises(ValueError, match="height_mm"):
        TargetArea(target_id="x", name="x", width_mm=5000, height_mm=-1)


def test_negative_required_area_raises():
    with pytest.raises(ValueError, match="required_area_m2"):
        TargetArea(
            target_id="x", name="x", width_mm=5000, height_mm=3000,
            required_area_m2=-2.0,
        )


def test_calculated_area_uses_mm_to_m2_conversion():
    t = TargetArea(target_id="x", name="x", width_mm=5000, height_mm=3000)
    # 5 m × 3 m = 15 m².
    assert t.calculated_area_m2 == pytest.approx(15.0)


def test_calculated_area_with_non_integer_dimensions():
    t = TargetArea(target_id="x", name="x", width_mm=1234.5, height_mm=2345.6)
    assert t.calculated_area_m2 == pytest.approx(1234.5 * 2345.6 / 1_000_000.0)


def test_no_required_area_means_no_mismatch_warning():
    t = TargetArea(target_id="x", name="x", width_mm=5000, height_mm=3000)
    assert target_area_warnings(t) == []


def test_required_area_within_tolerance_no_warning():
    # calculated = 15.0; required = 15.5 → 3.3% diff, within 5%.
    t = TargetArea(
        target_id="x", name="x",
        width_mm=5000, height_mm=3000,
        required_area_m2=15.5,
    )
    assert "required_area_mismatch" not in target_area_warnings(t)


def test_required_area_outside_tolerance_warns():
    # calculated = 15.0; required = 20.0 → 25% diff, over 5%.
    t = TargetArea(
        target_id="x", name="x",
        width_mm=5000, height_mm=3000,
        required_area_m2=20.0,
    )
    assert "required_area_mismatch" in target_area_warnings(t)


def test_required_area_far_below_calculated_also_warns():
    t = TargetArea(
        target_id="x", name="x",
        width_mm=5000, height_mm=3000,
        required_area_m2=5.0,  # 67% below calculated
    )
    assert "required_area_mismatch" in target_area_warnings(t)


def test_notes_field_is_optional_and_preserved():
    t = TargetArea(
        target_id="x", name="x",
        width_mm=5000, height_mm=3000,
        notes="L-shape; assume rectangular for V1",
    )
    assert t.notes == "L-shape; assume rectangular for V1"
