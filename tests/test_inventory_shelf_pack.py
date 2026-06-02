"""Tests for the V1 smoke-only shelf packer."""

from __future__ import annotations

from pathlib import Path

import pytest

from placement_engine.inventory.model import InventorySlab
from placement_engine.inventory.shelf_pack import (
    Placement,
    ShelfPackResult,
    shelf_pack,
)
from placement_engine.target_area import TargetArea


def _s(slab_id: str, w: float, h: float, image_available: bool = False) -> InventorySlab:
    return InventorySlab(
        slab_id=slab_id,
        serial_number=None,
        slab_number=None,
        item_code=None,
        width_mm=w,
        height_mm=h,
        area_m2=w * h / 1_000_000.0,
        calculated_area_m2=w * h / 1_000_000.0,
        image_path=Path("/x.jpg") if image_available else None,
        image_available=image_available,
        image_placeholder_reason=None,
        source_excel_row=None,
    )


def _target(w: float, h: float, name: str = "test target") -> TargetArea:
    return TargetArea(target_id="test", name=name, width_mm=w, height_mm=h)


def test_single_slab_fits_at_origin():
    slabs = [_s("A", 1000, 800)]
    result = shelf_pack(slabs, _target(4000, 3000))
    assert len(result.placements) == 1
    p = result.placements[0]
    assert (p.x, p.y) == (0.0, 0.0)
    assert (p.width_mm, p.height_mm) == (1000, 800)
    assert result.overflow == []


def test_row_fills_then_wraps():
    """3 slabs of 1500mm fit two-per-row in a 4000mm-wide target."""
    slabs = [_s("A", 1500, 800), _s("B", 1500, 800), _s("C", 1500, 800)]
    result = shelf_pack(slabs, _target(4000, 3000))
    # A and B share row 0; C wraps to row 1 at y=800.
    coords = [(p.x, p.y) for p in result.placements]
    assert coords == [(0.0, 0.0), (1500.0, 0.0), (0.0, 800.0)]
    assert result.overflow == []


def test_row_height_is_max_of_row():
    """A taller slab in a row pushes subsequent rows down by its height."""
    slabs = [_s("A", 1500, 800), _s("B", 1500, 1200), _s("C", 1500, 500)]
    result = shelf_pack(slabs, _target(4000, 3000))
    coords = [(p.x, p.y) for p in result.placements]
    # Row 0 height = max(800, 1200) = 1200; C starts at y=1200.
    assert coords == [(0.0, 0.0), (1500.0, 0.0), (0.0, 1200.0)]


def test_slab_taller_than_target_overflows():
    too_tall = _s("TALL", 2000, 5000)
    result = shelf_pack([too_tall], _target(4000, 3000))
    assert result.placements == []
    assert [s.slab_id for s in result.overflow] == ["TALL"]


def test_slab_wider_than_target_overflows():
    too_wide = _s("WIDE", 5000, 1000)
    result = shelf_pack([too_wide], _target(4000, 3000))
    assert result.placements == []
    assert [s.slab_id for s in result.overflow] == ["WIDE"]


def test_row_wrap_past_top_edge_overflows():
    """After two full rows of 1500mm, a third 1500mm slab won't fit."""
    slabs = [_s(f"S{i}", 4000, 1500) for i in range(3)]
    result = shelf_pack(slabs, _target(4000, 3000))
    assert [p.slab_id for p in result.placements] == ["S0", "S1"]
    assert [s.slab_id for s in result.overflow] == ["S2"]


def test_metrics_match_placements():
    slabs = [_s("A", 1000, 800), _s("B", 1000, 800)]
    result = shelf_pack(slabs, _target(4000, 3000))
    assert result.target_area_m2 == pytest.approx(12.0)
    assert result.placed_area_m2 == pytest.approx(2 * 1000 * 800 / 1_000_000.0)
    assert result.uncovered_area_m2 == pytest.approx(12.0 - 1.6)
    assert result.coverage_percentage == pytest.approx(100.0 * 1.6 / 12.0)


def test_target_dimensions_change_placement_outcome():
    """A wider target accepts more slabs per row, a smaller target fewer."""
    slabs = [_s(f"S{i}", 2000, 1000) for i in range(4)]
    big = shelf_pack(slabs, _target(5000, 3000))
    small = shelf_pack(slabs, _target(3000, 2000))
    # Big: row 1 = S0,S1 (two of 2000mm fit in 5000mm with 1000mm to spare),
    # row 2 = S2,S3 at y=1000. All 4 fit.
    assert len(big.placements) == 4
    # Small: row 1 = S0 (next 2000mm wouldn't fit in 3000mm),
    # row 2 = S1 (y=1000, fits because 2000 <= 2000),
    # row 3 = S2 would be at y=2000 + 1000 = 3000 > 2000 → overflow.
    assert len(small.placements) == 2
    assert len(small.overflow) == 2


def test_empty_inventory_returns_empty_result():
    result = shelf_pack([], _target(4000, 3000))
    assert result.placements == [] and result.overflow == []
    assert result.placed_area_m2 == 0.0
    assert result.uncovered_area_m2 == pytest.approx(12.0)


def test_placement_carries_image_info():
    slabs = [_s("with-img", 1000, 800, image_available=True),
             _s("no-img", 1000, 800, image_available=False)]
    result = shelf_pack(slabs, _target(4000, 3000))
    by_id = {p.slab_id: p for p in result.placements}
    assert by_id["with-img"].image_available is True
    assert by_id["with-img"].image_path is not None
    assert by_id["no-img"].image_available is False
    assert by_id["no-img"].image_path is None


def test_result_carries_target_reference():
    """ShelfPackResult.target lets consumers introspect what we packed into."""
    target = _target(5000, 3000, name="Test Room")
    result = shelf_pack([], target)
    assert result.target is target
    assert result.target_width_mm == 5000
    assert result.target_height_mm == 3000
    assert result.target.name == "Test Room"
