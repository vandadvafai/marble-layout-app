"""Tests for `placement_engine.layout.grid` — tile layout generation."""

from __future__ import annotations

from pathlib import Path

import pytest

from placement_engine.layout import (
    LayoutResult,
    Piece,
    generate_tile_layout,
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


def _rect(w: float, h: float) -> TargetGeometry:
    return TargetGeometry(
        target_id="rect", name="rect",
        boundary=[(0, 0), (w, 0), (w, h), (0, h)],
    )


def _rect_with_hole(
    w: float, h: float, hole: tuple[float, float, float, float],
) -> TargetGeometry:
    hx, hy, hw, hh = hole
    return TargetGeometry(
        target_id="rh", name="rh",
        boundary=[(0, 0), (w, 0), (w, h), (0, h)],
        holes=[[(hx, hy), (hx + hw, hy), (hx + hw, hy + hh), (hx, hy + hh)]],
    )


# ---------------------------------------------------------------------------
# Clean rectangle — expected grid shape
# ---------------------------------------------------------------------------


def test_clean_rectangle_produces_expected_rows_and_columns():
    """A 6000 × 3600 mm rectangle with 1200 × 600 tiles → 5 cols × 6 rows."""
    layout = generate_tile_layout(_rect(6000, 3600), 1200, 600)
    # Exact integer divisor — every piece should be a full tile.
    assert layout.full_tile_count == 30
    assert layout.edge_piece_count == 0
    assert len(layout.pieces) == 30
    rows = {p.row for p in layout.pieces}
    cols = {p.col for p in layout.pieces}
    assert rows == set(range(6))
    assert cols == set(range(5))


def test_non_integer_divisor_produces_edge_row():
    """6000 × 4000 with 1200 × 600 → 5 cols × 6 full rows + 1 edge row.

    The edge row pieces are clipped to y ∈ [3600, 4000] (400 of 600 mm).
    """
    layout = generate_tile_layout(_rect(6000, 4000), 1200, 600)
    # 5 full cols × 6 full rows = 30 full tiles + 5 edge pieces in row 6.
    assert layout.full_tile_count == 30
    assert layout.edge_piece_count == 5
    edge_pieces = [p for p in layout.pieces if p.is_edge_piece]
    for ep in edge_pieces:
        assert ep.row == 6
        assert ep.bounding_height_mm == pytest.approx(400.0)
        assert ep.actual_area_m2 == pytest.approx(1200.0 * 400.0 / 1_000_000.0)


def test_tile_size_larger_than_floor_produces_single_clipped_piece():
    """A 2000 × 1000 tile on a 1500 × 800 floor → one edge piece equal
    to the whole floor."""
    layout = generate_tile_layout(_rect(1500, 800), 2000, 1000)
    assert len(layout.pieces) == 1
    p = layout.pieces[0]
    assert not p.is_full_tile and p.is_edge_piece
    assert p.actual_area_m2 == pytest.approx(1.5 * 0.8)


# ---------------------------------------------------------------------------
# Holes
# ---------------------------------------------------------------------------


def test_hole_inside_a_tile_marks_intersects_hole_and_reduces_area():
    """A 600 × 400 hole *fully interior* to one 1200 × 600 tile → still
    one piece, intersects_hole=True, area = nominal − hole.

    (When a hole touches tile edges it splits the tile — see
    ``test_hole_splits_one_tile_into_two_subpieces`` for that case.)
    """
    # 1200 × 1200 floor with a 600 × 400 hole that does NOT touch any
    # tile edge — sits strictly inside the bottom 1200 × 600 tile.
    layout = generate_tile_layout(
        _rect_with_hole(1200, 1200, (300, 100, 600, 400)),
        1200, 600,
    )
    bottom = next(p for p in layout.pieces if p.row == 0)
    top = next(p for p in layout.pieces if p.row == 1)
    assert bottom.intersects_hole is True
    assert bottom.is_edge_piece is True
    # Bottom piece area = 1200 × 600 − 600 × 400 = 0.48 m².
    assert bottom.actual_area_m2 == pytest.approx(0.48)
    assert top.is_full_tile is True
    # Top tile is fully inside the floor and untouched by the hole.
    assert top.intersects_hole is False


def test_hole_splits_one_tile_into_two_subpieces():
    """A vertical wall-shaped hole that splits a single tile into left
    and right halves emits TWO pieces with ``split_by_hole`` notes."""
    # 1200 × 600 tile; hole is a thin vertical strip at x ∈ [550, 650],
    # full height of the tile → splits into two 550-mm-wide pieces.
    target = TargetGeometry(
        target_id="rh2", name="rh2",
        boundary=[(0, 0), (1200, 0), (1200, 600), (0, 600)],
        holes=[[(550, 0), (650, 0), (650, 600), (550, 600)]],
    )
    layout = generate_tile_layout(target, 1200, 600)
    # Two sub-pieces in row 0, col 0; both flagged split_by_hole.
    assert len(layout.pieces) == 2
    for piece in layout.pieces:
        assert piece.row == 0 and piece.col == 0
        assert "split_by_hole" in piece.notes
        assert piece.is_edge_piece
        assert piece.intersects_hole
    ids = {p.piece_id for p in layout.pieces}
    assert ids == {"tile_r0_c0_p0", "tile_r0_c0_p1"}


def test_tile_fully_inside_a_hole_is_excluded():
    """A tile that sits entirely inside a hole produces no piece."""
    # 3000 × 1200 floor; one 1200 × 1200 hole at x ∈ [1200, 2400].
    target = TargetGeometry(
        target_id="rh3", name="rh3",
        boundary=[(0, 0), (3000, 0), (3000, 1200), (0, 1200)],
        holes=[[(1200, 0), (2400, 0), (2400, 1200), (1200, 1200)]],
    )
    layout = generate_tile_layout(target, 1200, 600)
    # Cols 0 and 2 contribute full tiles; col 1 is entirely inside the hole.
    cols_used = {p.col for p in layout.pieces}
    assert cols_used == {0, 2}


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------


def test_no_piece_area_falls_outside_usable_polygon():
    """Sum of actual piece areas == usable floor area (within tolerance).

    L-shape DXF: this is the headline correctness invariant — every mm²
    of usable floor is covered by exactly one piece, and no piece
    extends past the boundary or into a hole.
    """
    geometry = load_target_geometry_from_dxf(EX_L)
    layout = generate_tile_layout(geometry, 1200, 600)
    assert layout.total_actual_area_m2 == pytest.approx(
        geometry.usable_area_m2, abs=0.001,
    )


def test_apartment_with_holes_area_conservation():
    geometry = load_target_geometry_from_dxf(EX_APT)
    layout = generate_tile_layout(geometry, 1200, 600)
    assert layout.total_actual_area_m2 == pytest.approx(
        geometry.usable_area_m2, abs=0.01,
    )
    # Should produce some full tiles and some edge pieces.
    assert layout.full_tile_count > 0
    assert layout.edge_piece_count > 0


def test_total_piece_area_approximately_equals_usable_floor_area_rect():
    geometry = load_target_geometry_from_dxf(EX_RECT)
    layout = generate_tile_layout(geometry, 1200, 600)
    assert layout.total_actual_area_m2 == pytest.approx(
        geometry.usable_area_m2, abs=0.001,
    )


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_zero_or_negative_tile_dimensions_raise():
    target = _rect(1000, 1000)
    with pytest.raises(ValueError):
        generate_tile_layout(target, 0, 600)
    with pytest.raises(ValueError):
        generate_tile_layout(target, 600, -10)


def test_origin_can_be_overridden_to_shift_the_grid():
    """An origin of (-300, 0) shifts the column boundaries by 300 mm,
    so the leftmost column becomes a 900-mm-wide edge piece."""
    target = _rect(2400, 600)
    layout = generate_tile_layout(target, 1200, 600, origin=(-300, 0))
    # Pieces should start with a partial column at the left edge.
    sorted_pieces = sorted(layout.pieces, key=lambda p: p.nominal_x_mm)
    leftmost = sorted_pieces[0]
    assert leftmost.is_edge_piece
    # Visible part of the leftmost tile: x in [0, 900].
    assert leftmost.bounding_width_mm == pytest.approx(900.0)


def test_sliver_pieces_get_sliver_note():
    """A tile that contributes a very thin sliver is flagged ``sliver``.

    Boundary at x=1205 with tile width 1200: the second column's tile
    starts at x=1200 and is clipped to [1200, 1205] → 5 mm slice.
    """
    target = _rect(1205, 600)
    layout = generate_tile_layout(target, 1200, 600)
    slivers = [p for p in layout.pieces if "sliver" in p.notes]
    assert len(slivers) == 1
    assert slivers[0].col == 1
    assert slivers[0].bounding_width_mm == pytest.approx(5.0, abs=0.01)


def test_layout_result_derived_metrics_are_consistent():
    geometry = load_target_geometry_from_dxf(EX_L)
    layout = generate_tile_layout(geometry, 1200, 600)
    d = layout.to_dict()
    assert d["derived"]["piece_count"] == len(layout.pieces)
    assert d["derived"]["full_tile_count"] == layout.full_tile_count
    assert d["derived"]["edge_piece_count"] == layout.edge_piece_count
    assert d["derived"]["coverage_percentage"] == pytest.approx(
        100.0, abs=0.1,  # full polygon coverage expected
    )


# ---------------------------------------------------------------------------
# JSON schema stability
# ---------------------------------------------------------------------------


def test_layout_json_schema_round_trips(tmp_path: Path):
    import json
    layout = generate_tile_layout(_rect(2400, 1200), 1200, 600)
    path = write_layout_json(layout, tmp_path / "layout.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    # Top-level shape.
    assert set(data.keys()) >= {"target", "grid", "pieces", "derived"}
    # Grid metadata.
    assert data["grid"]["tile_width_mm"] == 1200
    assert data["grid"]["tile_height_mm"] == 600
    assert data["grid"]["origin"] == [0.0, 0.0]
    # Piece shape.
    p = data["pieces"][0]
    assert set(p.keys()) >= {
        "piece_id", "row", "col",
        "nominal_x_mm", "nominal_y_mm",
        "nominal_width_mm", "nominal_height_mm",
        "actual_cut_polygon",
        "bounding_width_mm", "bounding_height_mm",
        "actual_area_m2",
        "is_full_tile", "is_edge_piece", "intersects_hole",
        "notes",
    }
    # actual_cut_polygon is a list of [x, y] pairs.
    assert isinstance(p["actual_cut_polygon"], list)
    assert isinstance(p["actual_cut_polygon"][0], list)
