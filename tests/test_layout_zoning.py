"""Tests for the layout zoning / decomposition layer."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from placement_engine.layout import (
    ANCHOR_BOTTOM_LEFT,
    ANCHOR_BOTTOM_RIGHT,
    ANCHOR_PER_ZONE,
    DEFAULT_ZONE_ID,
    LayoutZone,
    decompose_into_zones,
    generate_tile_layout_from_inventory,
    is_rectilinear,
    write_layout_json,
)
from placement_engine.target_area import (
    TargetGeometry,
    load_target_geometry_from_dxf,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
EX_RECT = REPO_ROOT / "examples/cad_inputs/demo/demo_rectangle_floor.dxf"
EX_L = REPO_ROOT / "examples/cad_inputs/demo/demo_l_shape_floor.dxf"
EX_APT = REPO_ROOT / "examples/cad_inputs/demo/demo_irregular_apartment_floor.dxf"


# ---------------------------------------------------------------------------
# helpers
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


def _rect(w: float, h: float) -> TargetGeometry:
    return TargetGeometry(
        target_id="rect", name="rect",
        boundary=[(0, 0), (w, 0), (w, h), (0, h)],
    )


def _l_shape(
    outer_w: float, outer_h: float, notch_w: float, notch_h: float,
) -> TargetGeometry:
    """L-shape with the upper-LEFT corner notched out."""
    return TargetGeometry(
        target_id="l", name="l",
        boundary=[
            (0, 0), (outer_w, 0), (outer_w, outer_h),
            (outer_w - notch_w, outer_h),
            (outer_w - notch_w, outer_h - notch_h),
            (0, outer_h - notch_h),
        ],
    )


# ---------------------------------------------------------------------------
# is_rectilinear
# ---------------------------------------------------------------------------


def test_rectangle_is_rectilinear():
    assert is_rectilinear(_rect(1000, 1000)) is True


def test_l_shape_is_rectilinear():
    assert is_rectilinear(load_target_geometry_from_dxf(EX_L)) is True


def test_apartment_with_holes_is_rectilinear():
    """The demo apartment has axis-aligned holes and an axis-aligned
    boundary — should still pass is_rectilinear."""
    assert is_rectilinear(load_target_geometry_from_dxf(EX_APT)) is True


def test_diagonal_polygon_is_not_rectilinear():
    geom = TargetGeometry(
        target_id="d", name="d",
        boundary=[(0, 0), (1000, 0), (1000, 500), (500, 1000), (0, 1000)],
    )
    assert is_rectilinear(geom) is False


# ---------------------------------------------------------------------------
# decompose_into_zones — basic shapes
# ---------------------------------------------------------------------------


def test_rectangle_decomposes_to_single_zone():
    """A clean rectangle has nothing to split — one zone covering the bbox."""
    zones = decompose_into_zones(_rect(6000, 4000))
    assert len(zones) == 1
    assert zones[0].bbox == (0.0, 0.0, 6000.0, 4000.0)
    assert zones[0].zone_id == "z0"


def test_l_shape_decomposes_to_exactly_two_zones():
    """The demo L-shape decomposes at x=4800 into a lower-left bar
    and an upper-right column."""
    zones = decompose_into_zones(load_target_geometry_from_dxf(EX_L))
    assert len(zones) == 2
    bboxes = sorted(z.bbox for z in zones)
    assert bboxes == [
        (0.0, 0.0, 4800.0, 2600.0),
        (4800.0, 0.0, 8000.0, 4000.0),
    ]


def test_l_shape_zone_area_equals_usable_area():
    """Sum of zone bbox areas equals the usable floor area (no holes)."""
    geom = load_target_geometry_from_dxf(EX_L)
    zones = decompose_into_zones(geom)
    total = sum(z.area_m2 for z in zones)
    assert total == pytest.approx(geom.usable_area_m2, abs=1e-3)


def test_apartment_collapses_to_two_zones_despite_holes():
    """Holes do NOT drive zone splitting — only the boundary's step
    line at x=8000 matters."""
    geom = load_target_geometry_from_dxf(EX_APT)
    zones = decompose_into_zones(geom)
    assert len(zones) == 2
    by_bbox = sorted(z.bbox for z in zones)
    assert by_bbox == [
        (0.0, 0.0, 8000.0, 8000.0),
        (8000.0, 3000.0, 12000.0, 8000.0),
    ]
    # Holes are assigned to whichever zone contains them.
    z0 = next(z for z in zones if z.bbox[0] == 0)
    z1 = next(z for z in zones if z.bbox[0] == 8000)
    assert len(z0.interior_holes) == 2  # the two holes < x=8000
    assert len(z1.interior_holes) == 1  # the [9500..10500] hole


def test_apartment_zone_area_minus_holes_equals_usable_area():
    geom = load_target_geometry_from_dxf(EX_APT)
    zones = decompose_into_zones(geom)
    bbox_area = sum(z.area_m2 for z in zones)
    holes_area = 0.0
    for z in zones:
        for hole in z.interior_holes:
            xs = [pt[0] for pt in hole]
            ys = [pt[1] for pt in hole]
            holes_area += (max(xs) - min(xs)) * (max(ys) - min(ys)) / 1e6
    assert (bbox_area - holes_area) == pytest.approx(geom.usable_area_m2, abs=1e-3)


def test_diagonal_polygon_falls_back_to_single_bbox_zone():
    """Non-rectilinear polygons skip the decomposition entirely."""
    geom = TargetGeometry(
        target_id="d", name="d",
        boundary=[(0, 0), (1000, 0), (1000, 500), (500, 1000), (0, 1000)],
    )
    zones = decompose_into_zones(geom)
    assert len(zones) == 1
    assert zones[0].bbox == geom.bbox


# ---------------------------------------------------------------------------
# exterior_edges classification
# ---------------------------------------------------------------------------


def test_rectangle_single_zone_has_all_exterior_edges():
    zones = decompose_into_zones(_rect(6000, 4000))
    e = zones[0].exterior_edges
    assert e.left is True and e.right is True
    assert e.bottom is True and e.top is True


def test_l_shape_step_edge_is_interior_for_both_zones():
    """z0's right edge (at the step) and z1's left edge (same line)
    should both be flagged interior. All other edges stay exterior."""
    zones = decompose_into_zones(load_target_geometry_from_dxf(EX_L))
    z0 = next(z for z in zones if z.bbox[0] == 0)
    z1 = next(z for z in zones if z.bbox[0] == 4800)
    # z0's right edge touches z1 → interior.
    assert z0.exterior_edges.right is False
    # z1's left edge touches z0 → interior.
    assert z1.exterior_edges.left is False
    # All other edges stay exterior.
    assert z0.exterior_edges.left is True
    assert z0.exterior_edges.bottom is True
    assert z0.exterior_edges.top is True
    assert z1.exterior_edges.right is True
    assert z1.exterior_edges.bottom is True
    assert z1.exterior_edges.top is True


# ---------------------------------------------------------------------------
# Integration — generate_tile_layout_from_inventory with zoning
# ---------------------------------------------------------------------------


def test_l_shape_zoned_layout_has_no_pieces_crossing_step_line():
    """Critical correctness invariant: with zoning, no piece's bbox
    straddles the architectural step at x=4800. Pre-zoning, col 3 in
    the global grid straddled this line — that's exactly what zoning
    prevents."""
    geom = load_target_geometry_from_dxf(EX_L)
    layout = generate_tile_layout_from_inventory(geom, _real_inventory())
    for p in layout.pieces:
        bx0 = min(x for x, _ in p.actual_cut_polygon)
        bx1 = max(x for x, _ in p.actual_cut_polygon)
        # Pieces in z0 must end at or before 4800; pieces in z1 must
        # start at or after 4800. Sub-mm tolerance for shapely noise.
        if p.zone_id == "z0":
            assert bx1 <= 4800.0 + 1.0
        else:
            assert bx0 >= 4800.0 - 1.0


def test_l_shape_zoned_layout_puts_all_slivers_on_exterior_boundaries():
    """The user's exact requirement: no thin sliver between the two
    architectural zones. Every sliver in the L-shape layout must touch
    an exterior boundary of its zone — i.e. the floor's real outer
    edge, not the zone-to-zone seam."""
    geom = load_target_geometry_from_dxf(EX_L)
    layout = generate_tile_layout_from_inventory(geom, _real_inventory())
    zones_by_id = {z.zone_id: z for z in layout.zones}
    interior_slivers = 0
    for p in layout.pieces:
        if "sliver" not in p.notes:
            continue
        zone = zones_by_id[p.zone_id]
        zx0, zy0, zx1, zy1 = zone.bbox
        xs = [x for x, _ in p.actual_cut_polygon]
        ys = [y for _, y in p.actual_cut_polygon]
        px0, py0, px1, py1 = min(xs), min(ys), max(xs), max(ys)
        touches = []
        if abs(px0 - zx0) < 1.0 and not zone.exterior_edges.left:
            touches.append("left")
        if abs(px1 - zx1) < 1.0 and not zone.exterior_edges.right:
            touches.append("right")
        if abs(py0 - zy0) < 1.0 and not zone.exterior_edges.bottom:
            touches.append("bottom")
        if abs(py1 - zy1) < 1.0 and not zone.exterior_edges.top:
            touches.append("top")
        if touches:
            interior_slivers += 1
    assert interior_slivers == 0


def test_l_shape_area_conserved_under_zoning():
    geom = load_target_geometry_from_dxf(EX_L)
    layout = generate_tile_layout_from_inventory(geom, _real_inventory())
    assert layout.total_actual_area_m2 == pytest.approx(geom.usable_area_m2, abs=1e-3)
    assert layout.coverage_percentage == pytest.approx(100.0, abs=0.1)


def test_apartment_area_conserved_under_zoning():
    geom = load_target_geometry_from_dxf(EX_APT)
    layout = generate_tile_layout_from_inventory(geom, _real_inventory())
    assert layout.total_actual_area_m2 == pytest.approx(geom.usable_area_m2, abs=1e-3)


def test_rectangle_keeps_legacy_piece_ids():
    """Single-zone layouts must keep the historical ``tile_rN_cN`` IDs
    so downstream tests and tooling that grep for these stay happy."""
    layout = generate_tile_layout_from_inventory(_rect(6000, 4000), _real_inventory())
    assert len(layout.zones) == 1
    for p in layout.pieces:
        assert p.piece_id.startswith("tile_")
        assert p.zone_id == DEFAULT_ZONE_ID


def test_multi_zone_piece_ids_are_unique_and_zone_prefixed():
    geom = load_target_geometry_from_dxf(EX_L)
    layout = generate_tile_layout_from_inventory(geom, _real_inventory())
    ids = [p.piece_id for p in layout.pieces]
    assert len(set(ids)) == len(ids)
    # Every multi-zone piece_id starts with its zone_id.
    for p in layout.pieces:
        assert p.piece_id.startswith(p.zone_id + "_"), (
            f"piece {p.piece_id} not prefixed with its zone {p.zone_id}"
        )


def test_every_piece_carries_a_zone_id():
    """Downstream consumers (cut_list etc.) read ``piece.zone_id`` to
    group pieces; the field must always be populated."""
    geom = load_target_geometry_from_dxf(EX_APT)
    layout = generate_tile_layout_from_inventory(geom, _real_inventory())
    valid_ids = {z.zone_id for z in layout.zones}
    for p in layout.pieces:
        assert p.zone_id in valid_ids


# ---------------------------------------------------------------------------
# JSON shape — downstream consumers should still work
# ---------------------------------------------------------------------------


def test_layout_json_includes_zone_metadata(tmp_path: Path):
    geom = load_target_geometry_from_dxf(EX_L)
    layout = generate_tile_layout_from_inventory(geom, _real_inventory())
    path = write_layout_json(layout, tmp_path / "layout.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    grid = data["grid"]
    assert grid["zone_count"] == 2
    assert grid["anchor_mode"] == ANCHOR_PER_ZONE
    zones = grid["zones"]
    assert len(zones) == 2
    for z in zones:
        assert "zone_id" in z
        assert "bbox" in z
        assert "polygon" in z
        assert "anchor_mode" in z
        assert "origin" in z
        assert "exterior_edges" in z
        assert "interior_holes" in z
    # Every piece in the pieces[] list carries its zone_id.
    for p in data["pieces"]:
        assert "zone_id" in p


def test_pieces_field_unchanged_for_single_zone_layout_json(tmp_path: Path):
    """A single-zone layout's piece JSON keys are still a superset of
    the pre-zoning shape — added ``zone_id`` only."""
    layout = generate_tile_layout_from_inventory(_rect(6000, 4000), _real_inventory())
    path = write_layout_json(layout, tmp_path / "layout.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "piece_id", "row", "col",
        "nominal_x_mm", "nominal_y_mm",
        "nominal_width_mm", "nominal_height_mm",
        "actual_cut_polygon",
        "bounding_width_mm", "bounding_height_mm",
        "actual_area_m2",
        "is_full_tile", "is_edge_piece", "intersects_hole",
        "notes", "interior_holes",
        "zone_id",
    }
    assert set(data["pieces"][0].keys()) == expected


# ---------------------------------------------------------------------------
# Downstream-cohabitation — cut_list builder consumes new layouts
# ---------------------------------------------------------------------------


def test_cut_list_builder_consumes_zoned_layout(tmp_path: Path):
    """The cut-list builder only reads tile_width/height and the
    polygon per piece — zoning must NOT break it."""
    from placement_engine.cut_list import build_cut_list, write_cut_list_json

    geom = load_target_geometry_from_dxf(EX_L)
    layout = generate_tile_layout_from_inventory(geom, _real_inventory())
    layout_path = write_layout_json(layout, tmp_path / "layout.json")
    cut_list = build_cut_list(layout_path)
    out = write_cut_list_json(cut_list, tmp_path / "cut_list.json")
    cl = json.loads(out.read_text(encoding="utf-8"))
    # Same piece count as layout.
    assert cl["summary"]["total_pieces"] == len(layout.pieces)


def test_assignment_builder_consumes_zoned_layout(tmp_path: Path):
    """And the assignment layer must still read the cut list cleanly."""
    from placement_engine.assignment import build_assignment, write_assignment_json
    from placement_engine.cut_list import build_cut_list, write_cut_list_json

    geom = load_target_geometry_from_dxf(EX_L)
    layout = generate_tile_layout_from_inventory(geom, _real_inventory())
    layout_path = write_layout_json(layout, tmp_path / "layout.json")
    cl_path = write_cut_list_json(
        build_cut_list(layout_path), tmp_path / "cut_list.json",
    )
    # Inline a minimal inventory fixture (mirrors test_assignment_builder).
    inv_records = [{
        "slab_id": f"S{i}", "serial_number": f"S{i}", "slab_number": f"{i}",
        "item_code": "P", "image_id": None,
        "height_cm": int(h / 10), "width_cm": int(w / 10),
        "height_mm": h, "width_mm": w,
        "area_m2": w * h / 1e6, "calculated_area_m2": w * h / 1e6,
        "dimension_source": "explicit_excel",
        "image_path": None, "image_found": False,
        "image_match_method": "not_found",
        "source_excel_row": 2, "warnings": [],
    } for i, (w, h) in enumerate([
        (1590, 1590), (1590, 1980), (1550, 2040),
        (1590, 2200), (1570, 2320), (1600, 2500), (1610, 2620),
    ])]
    inv_path = tmp_path / "clean_slabs.json"
    inv_path.write_text(json.dumps({
        "source_excel": "x", "image_dir": "x", "sheet_name": "Sheet1",
        "record_count": len(inv_records),
        "warning_counts": {}, "mapped_columns": {}, "unmapped_columns": [],
        "records": inv_records,
    }), encoding="utf-8")

    asg = build_assignment(cl_path, inv_path)
    out = write_assignment_json(asg, tmp_path / "assignment.json")
    data = json.loads(out.read_text(encoding="utf-8"))
    # Sanity: assignments cover every cut-list piece.
    assert data["summary"]["total_pieces"] == len(layout.pieces)


def test_cutting_planner_consumes_zoned_layout(tmp_path: Path):
    """And the cutting planner shouldn't care about zoning either."""
    from placement_engine.cut_list import build_cut_list, write_cut_list_json
    from placement_engine.cutting import build_cutting_plan

    geom = load_target_geometry_from_dxf(EX_L)
    layout = generate_tile_layout_from_inventory(geom, _real_inventory())
    layout_path = write_layout_json(layout, tmp_path / "layout.json")
    cl_path = write_cut_list_json(
        build_cut_list(layout_path), tmp_path / "cut_list.json",
    )
    inv_records = [{
        "slab_id": "S", "serial_number": "S", "slab_number": "1",
        "item_code": "P", "image_id": None,
        "height_cm": 220, "width_cm": 159, "height_mm": 2200, "width_mm": 1590,
        "area_m2": 3.498, "calculated_area_m2": 3.498,
        "dimension_source": "explicit_excel", "image_path": None,
        "image_found": False, "image_match_method": "not_found",
        "source_excel_row": 2, "warnings": [],
    }]
    inv_path = tmp_path / "clean_slabs.json"
    inv_path.write_text(json.dumps({
        "source_excel": "x", "image_dir": "x", "sheet_name": "Sheet1",
        "record_count": 1, "warning_counts": {},
        "mapped_columns": {}, "unmapped_columns": [],
        "records": inv_records,
    }), encoding="utf-8")
    plan = build_cutting_plan(cl_path, inv_path)
    # Every piece is accounted for (assigned or unassigned).
    total = plan.summary.assigned_cut_pieces + plan.summary.unassigned_cut_pieces
    assert total == len(layout.pieces)


# ---------------------------------------------------------------------------
# enable_zoning escape hatch
# ---------------------------------------------------------------------------


def test_enable_zoning_false_collapses_to_single_bbox_zone():
    """When zoning is disabled, behaviour matches pre-zoning: one zone
    equal to the bbox. Lets callers opt out if they want the old
    bbox-wide grid back."""
    geom = load_target_geometry_from_dxf(EX_L)
    layout = generate_tile_layout_from_inventory(
        geom, _real_inventory(), enable_zoning=False,
    )
    assert len(layout.zones) == 1
    # Anchor selection runs on the bbox (single-zone), so anchor_mode
    # is one of the directional names, not ``per_zone``.
    assert layout.anchor_mode in {ANCHOR_BOTTOM_LEFT, ANCHOR_BOTTOM_RIGHT}
