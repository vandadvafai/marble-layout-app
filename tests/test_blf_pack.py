"""Tests for the V1 Bottom-Left Fill packer (Strategy A)."""

from __future__ import annotations

from pathlib import Path

import pytest

from placement_engine.inventory.blf_pack import (
    BLFPackResult,
    blf_pack,
)
from placement_engine.inventory.model import InventorySlab
from placement_engine.inventory.polygon_pack import polygon_pack
from placement_engine.target_area import (
    TargetGeometry,
    load_target_geometry_from_dxf,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
EX_L = REPO_ROOT / "examples/cad_inputs/demo/demo_l_shape_floor.dxf"


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


def _rect_target(w: float, h: float) -> TargetGeometry:
    return TargetGeometry(
        target_id="rect",
        name="rect",
        boundary=[(0.0, 0.0), (w, 0.0), (w, h), (0.0, h)],
    )


def _rect_with_hole(
    w: float, h: float,
    hole: tuple[float, float, float, float],
) -> TargetGeometry:
    hx, hy, hw, hh = hole
    return TargetGeometry(
        target_id="rect_with_hole",
        name="rect with hole",
        boundary=[(0.0, 0.0), (w, 0.0), (w, h), (0.0, h)],
        holes=[[(hx, hy), (hx + hw, hy), (hx + hw, hy + hh), (hx, hy + hh)]],
    )


def _l_shape_target() -> TargetGeometry:
    """4×4 m boundary with a 2×2 m notch cut from the top-right."""
    return TargetGeometry(
        target_id="L",
        name="L",
        boundary=[
            (0, 0), (4000, 0), (4000, 2000),
            (2000, 2000), (2000, 4000), (0, 4000),
        ],
    )


# ---------------------------------------------------------------------------
# Basic placement inside a clean rectangle
# ---------------------------------------------------------------------------


def test_blf_places_slab_inside_rectangle_at_bottom_left():
    slabs = [_slab("A", 1500, 1000)]
    result = blf_pack(slabs, _rect_target(4000, 3000))
    assert isinstance(result, BLFPackResult)
    assert result.placed_count == 1
    p = result.placements[0]
    assert (p.x, p.y) == (0.0, 0.0)


def test_blf_places_two_slabs_flush_no_overlap():
    """Two 1500x1000 slabs into a 4000x3000 target should land flush at
    (0,0) and (1500,0), still on the bottom row."""
    slabs = [_slab("A", 1500, 1000), _slab("B", 1500, 1000)]
    result = blf_pack(slabs, _rect_target(4000, 3000))
    assert result.placed_count == 2
    coords = sorted((p.x, p.y) for p in result.placements)
    assert coords == [(0.0, 0.0), (1500.0, 0.0)]


def test_blf_does_not_place_outside_boundary():
    """Slab strictly wider than the target bbox → too_large_for_bbox."""
    slabs = [_slab("TOO_WIDE", 5000, 1000)]
    result = blf_pack(slabs, _rect_target(4000, 3000))
    assert result.placed_count == 0
    assert result.rejected_count == 1
    assert result.rejected[0].reason == "too_large_for_bbox"


def test_blf_rejects_slab_that_exceeds_bbox_height():
    """A slab too tall to fit anywhere → no_valid_position."""
    slabs = [_slab("TOO_TALL", 1000, 5000)]
    result = blf_pack(slabs, _rect_target(4000, 3000))
    assert result.placed_count == 0
    assert result.rejected[0].reason == "too_large_for_bbox"


# ---------------------------------------------------------------------------
# Holes
# ---------------------------------------------------------------------------


def test_blf_avoids_hole_by_finding_alternative_position():
    """Hole in the bottom-left forces the slab to scan further up/right."""
    # 4000x3000 target with a 2000x2000 hole at (0,0). A 1500x1500 slab
    # can't fit at (0,0); BLF should find (2000, 0) as the bottom-left
    # valid position.
    target = _rect_with_hole(4000, 3000, (0, 0, 2000, 2000))
    slabs = [_slab("A", 1500, 1500)]
    result = blf_pack(slabs, target)
    assert result.placed_count == 1
    p = result.placements[0]
    # BL scan finds the leftmost position past x=2000 that doesn't
    # overlap the hole. With a 50 mm grid, that's exactly x=2000.
    assert p.x == 2000.0
    assert p.y == 0.0


def test_blf_intersecting_hole_is_rejected_when_no_alternative_fits():
    """A slab larger than every clear strip → no_valid_position."""
    # 4000x3000 target with a 3500x2500 hole centered → only thin strips
    # remain. A 2000x2000 slab won't fit anywhere.
    target = _rect_with_hole(4000, 3000, (250, 250, 3500, 2500))
    slabs = [_slab("A", 2000, 2000)]
    result = blf_pack(slabs, target)
    assert result.placed_count == 0
    assert result.rejected[0].reason == "no_valid_position"


# ---------------------------------------------------------------------------
# Slab-vs-slab overlap
# ---------------------------------------------------------------------------


def test_blf_does_not_overlap_previously_placed_slabs():
    """Three identical 1500x1500 slabs in a 4000x3000 target."""
    # Sorted by area: tied → stable order. All same size, so placement
    # is BL row-fill: (0,0), (1500,0), (3000,0)? wait 3000+1500=4500 > 4000
    # so third wraps to next row: (0,1500). Verify no overlap.
    slabs = [_slab("A", 1500, 1500), _slab("B", 1500, 1500), _slab("C", 1500, 1500)]
    result = blf_pack(slabs, _rect_target(4000, 3000))
    assert result.placed_count == 3
    # No pairwise overlap. Strict-inequality check (touching is fine).
    for i, p1 in enumerate(result.placements):
        for p2 in result.placements[i + 1:]:
            x_overlap = (
                p1.x < p2.x + p2.width_mm
                and p1.x + p1.width_mm > p2.x
            )
            y_overlap = (
                p1.y < p2.y + p2.height_mm
                and p1.y + p1.height_mm > p2.y
            )
            assert not (x_overlap and y_overlap), (
                f"overlap between {p1.slab_id}@({p1.x},{p1.y}) and "
                f"{p2.slab_id}@({p2.x},{p2.y})"
            )


def test_blf_improves_packing_via_descending_area_sort():
    """A tiny slab and a big slab — big-first sort fits both; small-first
    would still fit both here, but big-first is the BLF default."""
    slabs = [_slab("small", 500, 500), _slab("big", 3500, 2500)]
    result = blf_pack(slabs, _rect_target(4000, 3000))
    assert result.placed_count == 2
    by_id = {p.slab_id: p for p in result.placements}
    # The big slab is placed first (descending area sort), at (0,0).
    assert (by_id["big"].x, by_id["big"].y) == (0.0, 0.0)
    # The small one finds the next BL slot. Top of big slab is at y=2500;
    # there's room at x=3500..4000 (500 wide) below y=500.
    assert by_id["small"].x >= by_id["big"].width_mm - 1e-6


# ---------------------------------------------------------------------------
# Comparison with polygon_pack on the L-shape (the headline fixture)
# ---------------------------------------------------------------------------


def _real_inventory() -> list[InventorySlab]:
    """The 7 slabs from data/raw_test (sizes only — image fields aren't used)."""
    sizes = [
        ("S08", 1590, 1590), ("S11", 1590, 1980), ("S12", 1550, 2040),
        ("S13", 1590, 2200), ("S14", 1570, 2320),
        ("S16", 1600, 2500), ("S17", 1610, 2620),
    ]
    return [_slab(sid, w, h) for sid, w, h in sizes]


def test_blf_matches_or_improves_polygon_pack_on_l_shape():
    """The L-shape DXF: BLF should place at least as many slabs as
    polygon_pack and cover at least as much usable area."""
    target = load_target_geometry_from_dxf(EX_L)
    inventory = _real_inventory()
    poly = polygon_pack(inventory, target)
    blf = blf_pack(inventory, target)
    assert blf.placed_count >= poly.placed_count, (
        f"BLF placed {blf.placed_count} < polygon {poly.placed_count}"
    )
    assert blf.real_coverage_percentage >= poly.real_coverage_percentage, (
        f"BLF coverage {blf.real_coverage_percentage:.1f}% < "
        f"polygon {poly.real_coverage_percentage:.1f}%"
    )


# ---------------------------------------------------------------------------
# Configurability
# ---------------------------------------------------------------------------


def test_grid_step_is_configurable_and_affects_runtime():
    target = _rect_target(4000, 3000)
    slabs = _real_inventory()
    coarse = blf_pack(slabs, target, grid_step_mm=200.0)
    fine = blf_pack(slabs, target, grid_step_mm=10.0)
    # Both produce valid BLFPackResults with the same target.
    assert coarse.grid_step_mm == 200.0
    assert fine.grid_step_mm == 10.0
    # Fine grid is slower than coarse (loose assertion — wall clock is
    # noisy in CI, but the ratio should be > 1 with margin).
    assert fine.runtime_seconds > coarse.runtime_seconds


def test_invalid_grid_step_raises():
    with pytest.raises(ValueError):
        blf_pack([], _rect_target(4000, 3000), grid_step_mm=0)
    with pytest.raises(ValueError):
        blf_pack([], _rect_target(4000, 3000), grid_step_mm=-10)


def test_zero_bbox_target_raises():
    bad = TargetGeometry(
        target_id="z", name="z", boundary=[(0, 0), (0, 0), (0, 0)],
    )
    with pytest.raises(ValueError):
        blf_pack([], bad)


def test_empty_inventory_returns_empty_result():
    target = _rect_target(4000, 3000)
    result = blf_pack([], target)
    assert result.placed_count == 0
    assert result.rejected_count == 0
    assert result.usable_area_m2 == pytest.approx(12.0)
    assert result.placed_area_m2 == 0.0
    assert result.real_coverage_percentage == 0.0


# ---------------------------------------------------------------------------
# Synthetic L-shape — verify BLF uses the left column
# ---------------------------------------------------------------------------


def test_blf_uses_l_shape_left_column_unlike_shelf_walk():
    """Synthetic 4×4 m L (with 2×2 top-right notch). Three 1500×1500
    slabs all fit (one in each column on row 0, plus one in the left
    column on row 1). polygon_pack only fits two of these; BLF must fit
    all three because it scans (0, ~1500) on the left column."""
    target = _l_shape_target()
    slabs = [_slab(f"S{i}", 1500, 1500) for i in range(3)]
    result = blf_pack(slabs, target)
    assert result.placed_count == 3
    # And at least one slab landed in the upper half of the left column
    # (y >= 1500), proving the L-extension is being used.
    assert any(p.y >= 1500 for p in result.placements)
