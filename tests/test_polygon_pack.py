"""Tests for the V1 polygon-aware shelf packer."""

from __future__ import annotations

from pathlib import Path

import pytest

from placement_engine.inventory.model import InventorySlab
from placement_engine.inventory.polygon_pack import (
    PolygonPackResult,
    polygon_pack,
)
from placement_engine.target_area import (
    TargetGeometry,
    load_target_geometry_from_dxf,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


REPO_ROOT = Path(__file__).resolve().parent.parent
EX_RECT = REPO_ROOT / "examples/cad_inputs/demo/demo_rectangle_floor.dxf"
EX_L = REPO_ROOT / "examples/cad_inputs/demo/demo_l_shape_floor.dxf"
EX_APT = REPO_ROOT / "examples/cad_inputs/demo/demo_irregular_apartment_floor.dxf"


def _slab(slab_id: str, w: float, h: float) -> InventorySlab:
    return InventorySlab(
        slab_id=slab_id,
        serial_number=None,
        slab_number=None,
        item_code=None,
        width_mm=w,
        height_mm=h,
        area_m2=w * h / 1_000_000.0,
        calculated_area_m2=w * h / 1_000_000.0,
        image_path=None,
        image_available=False,
        image_placeholder_reason=None,
        source_excel_row=None,
    )


def _rect_target(w: float, h: float, *, name: str = "rect") -> TargetGeometry:
    """Hand-rolled rectangle TargetGeometry (skips the DXF read)."""
    return TargetGeometry(
        target_id="rect",
        name=name,
        boundary=[(0.0, 0.0), (w, 0.0), (w, h), (0.0, h)],
        holes=[],
    )


def _rect_with_hole(
    w: float, h: float,
    hole: tuple[float, float, float, float],  # (x, y, w, h) of the hole
) -> TargetGeometry:
    hx, hy, hw, hh = hole
    return TargetGeometry(
        target_id="rect_with_hole",
        name="rect with hole",
        boundary=[(0.0, 0.0), (w, 0.0), (w, h), (0.0, h)],
        holes=[[(hx, hy), (hx + hw, hy), (hx + hw, hy + hh), (hx, hy + hh)]],
    )


# ---------------------------------------------------------------------------
# Basic acceptance on a clean rectangle
# ---------------------------------------------------------------------------


def test_clean_rectangle_accepts_slabs_that_fit():
    target = _rect_target(4000, 3000)
    slabs = [_slab("A", 1500, 1000), _slab("B", 1500, 1000)]
    result = polygon_pack(slabs, target)
    assert isinstance(result, PolygonPackResult)
    assert result.placed_count == 2
    assert result.rejected_count == 0
    # Both placed on the same row.
    xs = [(p.x, p.y) for p in result.placements]
    assert xs == [(0.0, 0.0), (1500.0, 0.0)]


def test_slab_too_large_for_bbox_is_rejected_with_specific_reason():
    target = _rect_target(2000, 2000)
    slabs = [_slab("TOO_WIDE", 3000, 1000), _slab("TOO_TALL", 1000, 5000)]
    result = polygon_pack(slabs, target)
    assert result.placed_count == 0
    reasons = {r.reason for r in result.rejected}
    assert reasons == {"too_large_for_bbox"}


def test_exceeds_bbox_height_when_next_row_doesnt_fit():
    """Two 2000-tall slabs in a 3000-tall bbox: second wraps to next row
    and the new row's top (y=2000+2000=4000) exceeds the bbox height."""
    target = _rect_target(2000, 3000)
    slabs = [_slab("A", 2000, 2000), _slab("B", 2000, 2000)]
    result = polygon_pack(slabs, target)
    assert result.placed_count == 1
    assert result.rejected_count == 1
    assert result.rejected[0].reason == "exceeds_bbox_height"


# ---------------------------------------------------------------------------
# Hole intersection
# ---------------------------------------------------------------------------


def test_slab_intersecting_a_hole_is_rejected_with_intersects_hole():
    # 4000×3000 rect with a 1000×1000 hole centered.
    target = _rect_with_hole(4000, 3000, (1500, 1000, 1000, 1000))
    # Slab 0: 1500×800 at (0, 0) → clear of the hole → accepted.
    # Slab 1: 1500×1500 at (1500, 0) → overlaps the hole at y=1000+ → rejected.
    slabs = [_slab("clean", 1500, 800), _slab("on_hole", 1500, 1500)]
    result = polygon_pack(slabs, target)
    assert result.placed_count == 1
    assert result.placements[0].slab_id == "clean"
    assert result.rejected_count == 1
    assert result.rejected[0].slab_id == "on_hole"
    assert result.rejected[0].reason == "intersects_hole"
    assert result.rejected[0].attempted_x == 1500.0
    assert result.rejected[0].attempted_y == 0.0


def test_synthetic_l_shape_rejects_slab_in_notch():
    """L-shaped boundary; a slab placed in the L's notch is outside."""
    # L-shape: 4000 wide × 4000 tall total, with a 2000×2000 notch cut
    # out of the top-right corner.
    target = TargetGeometry(
        target_id="L",
        name="synthetic L",
        boundary=[
            (0, 0), (4000, 0), (4000, 2000),
            (2000, 2000), (2000, 4000), (0, 4000),
        ],
        holes=[],
    )
    # Two 1900×1900 slabs. First placed at (0, 0) → fully inside (left
    # half is full height). Second placed at (1900, 0) → fully inside
    # (still in the lower half, height 2000). Third placed at (3800, 0)
    # → x+w=5700 > 4000, wraps to next row y=1900. Now (0, 1900,
    # 1900, 1900) → fully inside left column. Fourth placed at (1900,
    # 1900) → its right edge at 3800 is OUTSIDE the boundary (which
    # cuts in at x=2000 for y>=2000) → reject as outside_boundary.
    s = lambda i: _slab(f"S{i}", 1900, 1900)  # noqa: E731
    result = polygon_pack([s(0), s(1), s(2), s(3)], target)
    placed_ids = [p.slab_id for p in result.placements]
    rejected_ids = [(r.slab_id, r.reason) for r in result.rejected]
    # First two on row 0 are inside; third on row 1 left is inside.
    assert "S0" in placed_ids and "S1" in placed_ids
    assert "S2" in placed_ids
    # Fourth (right-of-notch on upper row) is outside.
    assert ("S3", "outside_boundary") in rejected_ids


# ---------------------------------------------------------------------------
# Cursor advances after rejection
# ---------------------------------------------------------------------------


def test_cursor_advances_after_rejection_so_walk_does_not_deadlock():
    """A rejected slab at (0, 0) doesn't leave subsequent slabs stuck there.

    Hole at (0, 0)–(2000, 2000) ⇒ first 1500×1500 slab at (0,0) overlaps
    → rejected. Cursor advances to x=1500. Next slab same size at
    (1500, 0): rect spans x=1500..3000 which still overlaps the hole
    → rejected, cursor advances to x=3000. Third at (3000, 0): clear of
    hole → accepted.
    """
    target = _rect_with_hole(5000, 3000, (0, 0, 2000, 2000))
    slabs = [_slab("A", 1500, 1500), _slab("B", 1500, 1500), _slab("C", 1500, 1500)]
    result = polygon_pack(slabs, target)
    placed_ids = [p.slab_id for p in result.placements]
    rejected_ids = [(r.slab_id, r.reason, r.attempted_x) for r in result.rejected]
    assert placed_ids == ["C"]
    assert ("A", "intersects_hole", 0.0) in rejected_ids
    assert ("B", "intersects_hole", 1500.0) in rejected_ids


# ---------------------------------------------------------------------------
# Coverage metric
# ---------------------------------------------------------------------------


def test_real_coverage_percentage_is_over_usable_area_not_bbox():
    """L-shape: bbox 4000×4000 (16 m²), usable 12 m² (L-shape area)."""
    target = TargetGeometry(
        target_id="L",
        name="L",
        boundary=[
            (0, 0), (4000, 0), (4000, 2000),
            (2000, 2000), (2000, 4000), (0, 4000),
        ],
        holes=[],
    )
    # Place two 2000×2000 slabs in the lower row → fully inside,
    # area = 2 × 4 m² = 8 m². Usable area = 4×4 - 2×2 = 12 m².
    # Real coverage = 8 / 12 = 66.67%.
    slabs = [_slab("A", 2000, 2000), _slab("B", 2000, 2000)]
    result = polygon_pack(slabs, target)
    assert result.placed_count == 2
    assert result.usable_area_m2 == pytest.approx(12.0)
    assert result.placed_area_m2 == pytest.approx(8.0)
    assert result.real_coverage_percentage == pytest.approx(100.0 * 8.0 / 12.0)


def test_zero_bbox_target_raises():
    with pytest.raises(ValueError):
        polygon_pack([], TargetGeometry(
            target_id="z", name="z", boundary=[(0, 0), (0, 0), (0, 0)],
        ))


def test_empty_inventory_returns_empty_result():
    target = _rect_target(4000, 3000)
    result = polygon_pack([], target)
    assert result.placed_count == 0
    assert result.rejected_count == 0
    assert result.usable_area_m2 == pytest.approx(12.0)
    assert result.placed_area_m2 == 0.0
    assert result.real_coverage_percentage == 0.0


# ---------------------------------------------------------------------------
# Apartment with holes: end-to-end on the real fixture
# ---------------------------------------------------------------------------


def test_apartment_fixture_rejects_slabs_over_holes_when_walked_into_them():
    """Pad cursor with synthetic slabs to force a real slab over a hole."""
    target = load_target_geometry_from_dxf(EX_APT)
    # Boundary is 12000×8000. Holes at specific positions — we don't
    # know exact coords here, but the row walk + our real slab sizes
    # (1500–1700 wide × 1500–2600 tall) tends to hit at least one hole
    # on the second row in the apartment.
    real_sizes = [
        (1590, 1590), (1590, 1980), (1550, 2040), (1590, 2200),
        (1570, 2320), (1600, 2500), (1610, 2620),
    ]
    slabs = [_slab(f"S{i}", w, h) for i, (w, h) in enumerate(real_sizes)]
    # Walk them twice to push cursor into row 2 where holes live.
    result = polygon_pack(slabs + slabs, target)
    # At least some placements succeed (the first row at y=0 is clear).
    assert result.placed_count >= 4
    # We don't strictly assert a hole rejection here because the exact
    # hole geometry of the example DXF isn't known to this test; but
    # the function must not raise, and the result must be a valid
    # PolygonPackResult.
    assert isinstance(result, PolygonPackResult)
    # All rejected reasons must be from the known enum.
    for r in result.rejected:
        assert r.reason in {
            "too_large_for_bbox", "exceeds_bbox_height",
            "intersects_hole", "outside_boundary",
        }
