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


def test_single_slab_fits_at_origin():
    slabs = [_s("A", 1000, 800)]
    result = shelf_pack(slabs, project_width_mm=4000, project_height_mm=3000)
    assert len(result.placements) == 1
    p = result.placements[0]
    assert (p.x, p.y) == (0.0, 0.0)
    assert (p.width_mm, p.height_mm) == (1000, 800)
    assert result.overflow == []


def test_row_fills_then_wraps():
    """3 slabs of 1500mm fit two-per-row in a 4000mm-wide project."""
    slabs = [_s("A", 1500, 800), _s("B", 1500, 800), _s("C", 1500, 800)]
    result = shelf_pack(slabs, project_width_mm=4000, project_height_mm=3000)
    # A and B share row 0; C wraps to row 1 at y=800.
    coords = [(p.x, p.y) for p in result.placements]
    assert coords == [(0.0, 0.0), (1500.0, 0.0), (0.0, 800.0)]
    assert result.overflow == []


def test_row_height_is_max_of_row():
    """A taller slab in a row pushes subsequent rows down by its height."""
    slabs = [_s("A", 1500, 800), _s("B", 1500, 1200), _s("C", 1500, 500)]
    result = shelf_pack(slabs, project_width_mm=4000, project_height_mm=3000)
    coords = [(p.x, p.y) for p in result.placements]
    # Row 0 height = max(800, 1200) = 1200; C starts at y=1200.
    assert coords == [(0.0, 0.0), (1500.0, 0.0), (0.0, 1200.0)]


def test_slab_taller_than_project_overflows():
    too_tall = _s("TALL", 2000, 5000)
    result = shelf_pack([too_tall], project_width_mm=4000, project_height_mm=3000)
    assert result.placements == []
    assert [s.slab_id for s in result.overflow] == ["TALL"]


def test_slab_wider_than_project_overflows():
    too_wide = _s("WIDE", 5000, 1000)
    result = shelf_pack([too_wide], project_width_mm=4000, project_height_mm=3000)
    assert result.placements == []
    assert [s.slab_id for s in result.overflow] == ["WIDE"]


def test_row_wrap_past_top_edge_overflows():
    """After two full rows of 1500mm, a third 1500mm slab won't fit."""
    slabs = [_s(f"S{i}", 4000, 1500) for i in range(3)]
    result = shelf_pack(slabs, project_width_mm=4000, project_height_mm=3000)
    assert [p.slab_id for p in result.placements] == ["S0", "S1"]
    assert [s.slab_id for s in result.overflow] == ["S2"]


def test_metrics_match_placements():
    slabs = [_s("A", 1000, 800), _s("B", 1000, 800)]
    result = shelf_pack(slabs, project_width_mm=4000, project_height_mm=3000)
    assert result.project_area_m2 == pytest.approx(12.0)
    assert result.placed_area_m2 == pytest.approx(2 * 1000 * 800 / 1_000_000.0)
    assert result.uncovered_area_m2 == pytest.approx(12.0 - 1.6)


def test_zero_or_negative_project_raises():
    with pytest.raises(ValueError):
        shelf_pack([], project_width_mm=0, project_height_mm=3000)
    with pytest.raises(ValueError):
        shelf_pack([], project_width_mm=4000, project_height_mm=-1)


def test_empty_inventory_returns_empty_result():
    result = shelf_pack([], project_width_mm=4000, project_height_mm=3000)
    assert result.placements == [] and result.overflow == []
    assert result.placed_area_m2 == 0.0
    assert result.uncovered_area_m2 == pytest.approx(12.0)


def test_placement_carries_image_info():
    slabs = [_s("with-img", 1000, 800, image_available=True),
             _s("no-img", 1000, 800, image_available=False)]
    result = shelf_pack(slabs, project_width_mm=4000, project_height_mm=3000)
    by_id = {p.slab_id: p for p in result.placements}
    assert by_id["with-img"].image_available is True
    assert by_id["with-img"].image_path is not None
    assert by_id["no-img"].image_available is False
    assert by_id["no-img"].image_path is None
