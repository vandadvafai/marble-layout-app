"""Tests for `placement_engine.target_area.dxf_target`."""

from __future__ import annotations

from pathlib import Path

import pytest

from placement_engine.target_area import (
    TargetArea,
    TargetGeometry,
    load_target_geometry_from_dxf,
)
from placement_engine.target_area.dxf_target import _normalize_polygons


REPO_ROOT = Path(__file__).resolve().parent.parent
EX_RECT = REPO_ROOT / "examples/cad_inputs/demo/demo_rectangle_floor.dxf"
EX_HOLE = REPO_ROOT / "examples/cad_inputs/floor_with_hole_standardized.dxf"
EX_L = REPO_ROOT / "examples/cad_inputs/demo/demo_l_shape_floor.dxf"
EX_APT = REPO_ROOT / "examples/cad_inputs/demo/demo_irregular_apartment_floor.dxf"


# ---------------------------------------------------------------------------
# _normalize_polygons
# ---------------------------------------------------------------------------


def test_normalize_polygons_translates_to_origin():
    boundary = [(100, 200), (1100, 200), (1100, 800), (100, 800)]
    holes = [[(300, 300), (400, 300), (400, 400), (300, 400)]]
    norm_b, norm_h, src = _normalize_polygons(boundary, holes)
    assert norm_b[0] == (0, 0)
    assert norm_b[2] == (1000, 600)
    assert norm_h[0][0] == (200, 100)
    assert src == (100, 200, 1100, 800)


def test_normalize_polygons_is_a_noop_when_already_at_origin():
    boundary = [(0, 0), (1000, 0), (1000, 600), (0, 600)]
    norm_b, _, src = _normalize_polygons(boundary, [])
    assert norm_b == boundary  # unchanged
    assert src == (0, 0, 1000, 600)


# ---------------------------------------------------------------------------
# load_target_geometry_from_dxf — happy path on real examples
# ---------------------------------------------------------------------------


def test_loads_simple_rectangle_dxf():
    geom = load_target_geometry_from_dxf(EX_RECT)
    assert isinstance(geom, TargetGeometry)
    assert geom.width_mm == pytest.approx(6000.0)
    assert geom.height_mm == pytest.approx(4000.0)
    assert geom.boundary_area_m2 == pytest.approx(24.0)
    assert geom.holes == []
    assert geom.usable_area_m2 == pytest.approx(24.0)
    assert geom.source_dxf_path == EX_RECT
    assert geom.target_id == "demo_rectangle_floor"


def test_loads_floor_with_one_hole():
    geom = load_target_geometry_from_dxf(EX_HOLE)
    assert geom.width_mm == pytest.approx(6000.0)
    assert geom.height_mm == pytest.approx(4000.0)
    assert geom.boundary_area_m2 == pytest.approx(24.0)
    assert len(geom.holes) == 1
    assert geom.holes_area_m2 == pytest.approx(0.3)
    assert geom.usable_area_m2 == pytest.approx(23.7)


def test_loads_l_shape_with_correct_usable_area():
    """L-shape: bbox 8000×4000 mm (32 m²) but boundary area only 25.28 m²."""
    geom = load_target_geometry_from_dxf(EX_L)
    assert geom.width_mm == pytest.approx(8000.0)
    assert geom.height_mm == pytest.approx(4000.0)
    # 32 m² is the bbox; the irregular boundary is smaller.
    assert geom.boundary_area_m2 == pytest.approx(25.28)
    assert geom.holes == []
    assert geom.usable_area_m2 == pytest.approx(25.28)
    # bbox area is bigger than usable — that's the smoke caveat.
    bbox_area = geom.width_mm * geom.height_mm / 1_000_000.0
    assert bbox_area > geom.usable_area_m2


def test_loads_apartment_with_three_holes():
    geom = load_target_geometry_from_dxf(EX_APT)
    assert geom.width_mm == pytest.approx(12000.0)
    assert geom.height_mm == pytest.approx(8000.0)
    assert len(geom.holes) == 3
    # Sum of hole areas reported by inspect: 0.16 + 0.16 + 1.00 = 1.32 m².
    assert geom.holes_area_m2 == pytest.approx(1.32, abs=0.01)
    assert geom.usable_area_m2 == pytest.approx(84.0 - 1.32, abs=0.01)


# ---------------------------------------------------------------------------
# TargetGeometry → TargetArea adapter
# ---------------------------------------------------------------------------


def test_as_bounding_target_area_returns_rectangle_with_bbox_dimensions():
    geom = load_target_geometry_from_dxf(EX_L)
    target = geom.as_bounding_target_area()
    assert isinstance(target, TargetArea)
    # Bbox of the L-shape.
    assert target.width_mm == pytest.approx(8000.0)
    assert target.height_mm == pytest.approx(4000.0)
    # required_area_m2 carries the *usable* area (boundary - holes),
    # so the existing TargetArea required-vs-calculated cross-check can
    # surface "this rectangle is much bigger than the real usable area".
    assert target.required_area_m2 == pytest.approx(25.28)
    # The id matches the geometry's id; the name marks the bbox provenance.
    assert target.target_id == geom.target_id
    assert "bbox" in target.name.lower()


def test_as_bounding_target_area_for_full_rectangle_no_warning():
    """For a pure rectangle DXF, bbox area == usable area; no mismatch."""
    from placement_engine.target_area import target_area_warnings

    geom = load_target_geometry_from_dxf(EX_RECT)
    target = geom.as_bounding_target_area()
    # Pure rectangle: usable area == calculated area, no warning.
    assert target_area_warnings(target) == []


def test_as_bounding_target_area_for_l_shape_warns_on_required_mismatch():
    """L-shape: bbox area (32) differs from usable (25.28) by ~21% > 5%."""
    from placement_engine.target_area import target_area_warnings

    geom = load_target_geometry_from_dxf(EX_L)
    target = geom.as_bounding_target_area()
    assert "required_area_mismatch" in target_area_warnings(target)


# ---------------------------------------------------------------------------
# Sanity: boundary is closed-polygon-shaped
# ---------------------------------------------------------------------------


def test_boundary_is_list_of_xy_tuples_in_mm():
    geom = load_target_geometry_from_dxf(EX_RECT)
    assert isinstance(geom.boundary, list)
    assert len(geom.boundary) >= 3
    for point in geom.boundary:
        assert isinstance(point, tuple) and len(point) == 2
        assert all(isinstance(c, (int, float)) for c in point)


def test_id_and_name_can_be_overridden():
    geom = load_target_geometry_from_dxf(
        EX_RECT, target_id="custom_id", name="Custom Room"
    )
    assert geom.target_id == "custom_id"
    assert geom.name == "Custom Room"
