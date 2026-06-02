"""Tests for `placement_engine.cut_list.builder` — build + classify + JSON I/O."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from placement_engine.cut_list import (
    CLASSIFICATION_EDGE,
    CLASSIFICATION_FULL,
    CLASSIFICATION_HOLE,
    CLASSIFICATION_SLIVER,
    CutList,
    CutListPiece,
    build_cut_list,
    write_cut_list_json,
    write_summary_json,
)
from placement_engine.layout import (
    generate_tile_layout,
    generate_tile_layout_from_inventory,
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
# fixtures
# ---------------------------------------------------------------------------


def _rect_target(w: float, h: float) -> TargetGeometry:
    return TargetGeometry(
        target_id="rect", name="rect",
        boundary=[(0, 0), (w, 0), (w, h), (0, h)],
    )


def _real_inventory_slabs():
    """7 synthetic slabs matching data/raw_test (sizes only)."""
    from dataclasses import dataclass

    @dataclass
    class _S:
        width_mm: float
        height_mm: float

    return [
        _S(1590, 1590), _S(1590, 1980), _S(1550, 2040),
        _S(1590, 2200), _S(1570, 2320),
        _S(1600, 2500), _S(1610, 2620),
    ]


def _layout_for(dxf: Path, tmp_path: Path) -> Path:
    """Generate an inventory-median layout, dump to JSON, return the path."""
    geometry = load_target_geometry_from_dxf(dxf)
    layout = generate_tile_layout_from_inventory(geometry, _real_inventory_slabs())
    return write_layout_json(layout, tmp_path / "layout.json")


# ---------------------------------------------------------------------------
# Piece-count consistency vs the source layout
# ---------------------------------------------------------------------------


def test_rectangle_cut_list_matches_layout_piece_count(tmp_path: Path):
    layout_path = _layout_for(EX_RECT, tmp_path)
    layout_dict = json.loads(layout_path.read_text(encoding="utf-8"))
    cl = build_cut_list(layout_path)
    assert isinstance(cl, CutList)
    assert len(cl.pieces) == len(layout_dict["pieces"])
    assert cl.summary.total_pieces == len(cl.pieces)


def test_l_shape_cut_list_matches_layout_piece_count(tmp_path: Path):
    layout_path = _layout_for(EX_L, tmp_path)
    layout_dict = json.loads(layout_path.read_text(encoding="utf-8"))
    cl = build_cut_list(layout_path)
    assert len(cl.pieces) == len(layout_dict["pieces"])


def test_apartment_cut_list_matches_layout_piece_count(tmp_path: Path):
    layout_path = _layout_for(EX_APT, tmp_path)
    layout_dict = json.loads(layout_path.read_text(encoding="utf-8"))
    cl = build_cut_list(layout_path)
    assert len(cl.pieces) == len(layout_dict["pieces"])


# ---------------------------------------------------------------------------
# Area conservation
# ---------------------------------------------------------------------------


def test_rectangle_area_conservation(tmp_path: Path):
    """Sum of cut-list piece areas == usable area within numerical noise."""
    layout_path = _layout_for(EX_RECT, tmp_path)
    cl = build_cut_list(layout_path)
    geometry = load_target_geometry_from_dxf(EX_RECT)
    assert cl.summary.total_area_m2 == pytest.approx(geometry.usable_area_m2, abs=0.01)


def test_l_shape_area_conservation(tmp_path: Path):
    layout_path = _layout_for(EX_L, tmp_path)
    cl = build_cut_list(layout_path)
    geometry = load_target_geometry_from_dxf(EX_L)
    assert cl.summary.total_area_m2 == pytest.approx(geometry.usable_area_m2, abs=0.01)


def test_apartment_area_conservation(tmp_path: Path):
    layout_path = _layout_for(EX_APT, tmp_path)
    cl = build_cut_list(layout_path)
    geometry = load_target_geometry_from_dxf(EX_APT)
    assert cl.summary.total_area_m2 == pytest.approx(geometry.usable_area_m2, abs=0.01)


# ---------------------------------------------------------------------------
# Classification — full / edge / hole / sliver
# ---------------------------------------------------------------------------


def test_full_piece_classification_on_clean_rectangle_grid(tmp_path: Path):
    """A 6000×4000 mm rectangle with an integer-divisor tile → only full
    pieces, no edges, no holes, no slivers."""
    target = _rect_target(6000, 4000)
    layout = generate_tile_layout(target, 1500, 1000)
    layout_path = write_layout_json(layout, tmp_path / "layout.json")
    cl = build_cut_list(layout_path)
    # 4×4 = 16 pieces, all full.
    assert cl.summary.full_pieces == cl.summary.total_pieces
    assert cl.summary.edge_pieces == 0
    assert cl.summary.hole_pieces == 0
    assert cl.summary.sliver_pieces == 0
    assert all(p.classification == CLASSIFICATION_FULL for p in cl.pieces)


def test_edge_piece_classification_when_clipped_by_boundary(tmp_path: Path):
    """6000×4000 with 1200×600 tiles → some edge pieces (no holes)."""
    target = _rect_target(6000, 4000)
    layout = generate_tile_layout(target, 1200, 600)
    layout_path = write_layout_json(layout, tmp_path / "layout.json")
    cl = build_cut_list(layout_path)
    assert cl.summary.edge_pieces > 0
    assert cl.summary.hole_pieces == 0
    # No piece has interior cuts on a hole-free target.
    assert cl.summary.pieces_with_internal_cuts == 0
    for piece in cl.pieces:
        if piece.classification == CLASSIFICATION_EDGE:
            assert piece.is_edge_piece is True
            assert piece.requires_internal_cut is False


def test_hole_classification_when_hole_is_interior_to_a_tile(tmp_path: Path):
    """A small hole strictly inside ONE tile → one hole piece with
    ``requires_internal_cut == True`` and a non-empty interior ring.

    Layout has two 1500×2000 tiles; the hole sits comfortably inside
    the right tile (x in [1700, 2300], y in [800, 1200]) and does NOT
    touch the seam at x=1500.
    """
    target = TargetGeometry(
        target_id="t", name="t",
        boundary=[(0, 0), (3000, 0), (3000, 2000), (0, 2000)],
        holes=[[(1700, 800), (2300, 800), (2300, 1200), (1700, 1200)]],
    )
    layout = generate_tile_layout(target, 1500, 2000)
    layout_path = write_layout_json(layout, tmp_path / "layout.json")
    cl = build_cut_list(layout_path)
    # Exactly one piece should have an interior cut.
    holes = [p for p in cl.pieces if p.classification == CLASSIFICATION_HOLE]
    assert len(holes) == 1
    hp = holes[0]
    assert hp.requires_internal_cut is True
    assert hp.intersects_hole is True
    assert len(hp.cut_polygon_interiors) == 1
    # The other piece is a full tile (no hole touches it).
    others = [p for p in cl.pieces if p is not hp]
    assert all(not p.requires_internal_cut for p in others)


def test_edge_touching_hole_does_not_classify_as_hole(tmp_path: Path):
    """A hole flush with a tile edge clips the perimeter — no internal
    cut required, so the piece is ``edge``, not ``hole``."""
    target = TargetGeometry(
        target_id="t", name="t",
        boundary=[(0, 0), (3000, 0), (3000, 2000), (0, 2000)],
        # Hole at x=1500..2000 spanning full height — flush with the
        # right edge of the first 1500-wide tile.
        holes=[[(1500, 0), (2000, 0), (2000, 2000), (1500, 2000)]],
    )
    layout = generate_tile_layout(target, 1500, 2000)
    layout_path = write_layout_json(layout, tmp_path / "layout.json")
    cl = build_cut_list(layout_path)
    # No piece should be classified as 'hole' — the hole is flush with
    # the tile boundary, requiring only perimeter clipping.
    assert all(p.classification != CLASSIFICATION_HOLE for p in cl.pieces)
    assert all(not p.requires_internal_cut for p in cl.pieces)


def test_sliver_classification_when_layout_flags_a_sliver(tmp_path: Path):
    """A boundary at x=1205 with 1200-wide tiles → 5 mm sliver in column 1."""
    target = _rect_target(1205, 600)
    layout = generate_tile_layout(target, 1200, 600)
    layout_path = write_layout_json(layout, tmp_path / "layout.json")
    cl = build_cut_list(layout_path)
    slivers = [p for p in cl.pieces if p.classification == CLASSIFICATION_SLIVER]
    assert len(slivers) == 1
    # Sliver still has area_m2 > 0 and is exposed in the summary count.
    assert cl.summary.sliver_pieces == 1
    assert slivers[0].area_m2 > 0


def test_sliver_wins_over_edge_when_both_apply(tmp_path: Path):
    """A clipped sliver piece is both an edge piece AND a sliver — the
    primary classification must be 'sliver' so it stays visible."""
    target = _rect_target(1205, 600)
    layout = generate_tile_layout(target, 1200, 600)
    layout_path = write_layout_json(layout, tmp_path / "layout.json")
    cl = build_cut_list(layout_path)
    slivers = [p for p in cl.pieces if p.classification == CLASSIFICATION_SLIVER]
    assert all(p.is_edge_piece for p in slivers)  # also an edge piece
    assert all(p.classification == CLASSIFICATION_SLIVER for p in slivers)


# ---------------------------------------------------------------------------
# JSON round-trip
# ---------------------------------------------------------------------------


def test_cut_list_json_round_trip(tmp_path: Path):
    layout_path = _layout_for(EX_L, tmp_path)
    cl = build_cut_list(layout_path)
    out = write_cut_list_json(cl, tmp_path / "cut_list.json")
    data = json.loads(out.read_text(encoding="utf-8"))
    # Top-level shape.
    assert set(data.keys()) == {"source_layout_path", "target", "grid", "pieces", "summary"}
    # Piece keys.
    piece = data["pieces"][0]
    assert set(piece.keys()) >= {
        "piece_id", "source_layout_piece_id",
        "nominal_width_mm", "nominal_height_mm",
        "bounding_width_mm", "bounding_height_mm",
        "area_m2", "classification",
        "is_full_piece", "is_edge_piece",
        "intersects_hole", "requires_internal_cut",
        "cut_polygon_exterior", "cut_polygon_interiors",
        "notes",
    }
    # piece_id is P001-style.
    assert piece["piece_id"].startswith("P")
    assert piece["piece_id"][1:].isdigit()


def test_summary_json_has_expected_shape(tmp_path: Path):
    layout_path = _layout_for(EX_APT, tmp_path)
    cl = build_cut_list(layout_path)
    out = write_summary_json(cl, tmp_path / "summary.json")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert set(data.keys()) == {
        "total_pieces", "full_pieces", "edge_pieces",
        "hole_pieces", "pieces_with_internal_cuts",
        "sliver_pieces", "total_area_m2",
    }
    assert data["total_pieces"] == cl.summary.total_pieces
    assert data["total_area_m2"] == pytest.approx(cl.summary.total_area_m2, abs=0.01)


def test_build_cut_list_accepts_dict_directly():
    """Builder must accept an already-loaded layout dict, not only paths."""
    layout = {
        "target": {"target_id": "t", "name": "t"},
        "grid": {"tile_width_mm": 1500, "tile_height_mm": 2000},
        "pieces": [
            {
                "piece_id": "tile_r0_c0",
                "actual_cut_polygon": [[0, 0], [1500, 0], [1500, 2000], [0, 2000], [0, 0]],
                "interior_holes": [],
                "nominal_width_mm": 1500, "nominal_height_mm": 2000,
                "bounding_width_mm": 1500, "bounding_height_mm": 2000,
                "actual_area_m2": 3.0,
                "is_full_tile": True, "is_edge_piece": False,
                "intersects_hole": False, "notes": [],
            }
        ],
    }
    cl = build_cut_list(layout)
    assert cl.pieces[0].classification == CLASSIFICATION_FULL
    assert cl.pieces[0].piece_id == "P001"
    assert cl.pieces[0].source_layout_piece_id == "tile_r0_c0"


def test_build_cut_list_raises_on_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        build_cut_list(tmp_path / "nope.json")
