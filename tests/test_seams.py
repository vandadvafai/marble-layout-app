"""Seam detection.

Direct unit tests on `detect_seams` with hand-built `PlacedPiece`
fixtures, plus end-to-end checks on the shipped examples that the
engine populates `seams`, `metrics.seam_count`, and
`metrics.total_seam_length` consistently.
"""
from pathlib import Path

import pytest
from shapely.geometry import LineString, MultiLineString, Point

from placement_engine import engine
from placement_engine.models import PlacedPiece, TextureTransform
from placement_engine.scoring.seams import (
    _extract_linestrings,
    detect_seams,
    total_seam_length,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _piece(piece_id: str, coords: list[tuple[float, float]]) -> PlacedPiece:
    return PlacedPiece(
        piece_id=piece_id,
        slab_id="S001",
        project_polygon=coords,
        slab_polygon=coords,
        rotation=0.0,
        texture_transform=TextureTransform(
            uv_origin=(0.0, 0.0),
            uv_width=100.0,
            uv_height=100.0,
        ),
    )


def _rect(piece_id: str, x: float, y: float, w: float, h: float) -> PlacedPiece:
    return _piece(piece_id, [(x, y), (x + w, y), (x + w, y + h), (x, y + h)])


# ---------------------------------------------------------------------------
# Direct-unit tests on detect_seams
# ---------------------------------------------------------------------------


def test_adjacent_rectangles_share_vertical_edge():
    a = _rect("P001", 0, 0, 1000, 800)
    b = _rect("P002", 1000, 0, 1000, 800)
    seams = detect_seams([a, b])
    assert len(seams) == 1
    s = seams[0]
    assert sorted(s.piece_ids) == ["P001", "P002"]
    assert s.length == pytest.approx(800.0)


def test_adjacent_rectangles_share_horizontal_edge():
    a = _rect("P001", 0, 0, 1000, 800)
    b = _rect("P002", 0, 800, 1000, 600)
    seams = detect_seams([a, b])
    assert len(seams) == 1
    assert seams[0].length == pytest.approx(1000.0)


def test_corner_only_contact_yields_no_seam():
    """Two rectangles meeting at a single point produce a Point-type
    intersection — must be filtered."""
    a = _rect("P001", 0, 0, 1000, 1000)
    b = _rect("P002", 1000, 1000, 1000, 1000)
    seams = detect_seams([a, b])
    assert seams == []


def test_separated_rectangles_yield_no_seam():
    a = _rect("P001", 0, 0, 1000, 1000)
    b = _rect("P002", 1500, 0, 1000, 1000)  # 500 mm gap
    assert detect_seams([a, b]) == []


def test_pieces_separated_by_a_hole_yield_no_seam():
    """Mimics the column-cutout case: two pieces face each other across
    a hole. Their boundaries don't overlap, so no seam is produced."""
    # Piece below the hole (at y∈[0,900])
    below = _rect("P001", 1200, 0, 600, 900)
    # Piece above the hole (at y∈[1400,2000]); the hole sits at y∈[900,1400]
    above = _rect("P002", 1200, 1400, 600, 600)
    assert detect_seams([below, above]) == []


def test_short_intersection_below_tolerance_is_dropped():
    """A 0.05 mm overlap should be ignored at the default tolerance."""
    a = _rect("P001", 0, 0, 1000, 1000)
    b = _rect("P002", 1000, 999.95, 1000, 1000)
    assert detect_seams([a, b], tolerance=0.1) == []


def test_extract_linestrings_handles_multilinestring():
    """The helper must split a MultiLineString into its parts."""
    mls = MultiLineString([[(0, 0), (1, 0)], [(2, 0), (3, 0)]])
    parts = _extract_linestrings(mls)
    assert len(parts) == 2
    assert all(isinstance(p, LineString) for p in parts)


def test_extract_linestrings_drops_points():
    """Corner-only intersections show up as Points; filter them out."""
    assert _extract_linestrings(Point(0, 0)) == []


def test_three_in_a_row_produces_two_seams():
    a = _rect("P001", 0, 0, 1000, 500)
    b = _rect("P002", 1000, 0, 1000, 500)
    c = _rect("P003", 2000, 0, 1000, 500)
    seams = detect_seams([a, b, c])
    assert len(seams) == 2
    assert {tuple(sorted(s.piece_ids)) for s in seams} == {
        ("P001", "P002"),
        ("P002", "P003"),
    }


def test_seam_ids_are_deterministic_and_sequential():
    a = _rect("P001", 0, 0, 1000, 500)
    b = _rect("P002", 1000, 0, 1000, 500)
    c = _rect("P003", 2000, 0, 1000, 500)
    seams = detect_seams([a, b, c])
    assert [s.seam_id for s in seams] == ["SM001", "SM002"]


def test_total_seam_length_helper():
    a = _rect("P001", 0, 0, 1000, 800)
    b = _rect("P002", 1000, 0, 1000, 800)
    c = _rect("P003", 0, 800, 1000, 600)
    seams = detect_seams([a, b, c])
    assert total_seam_length(seams) == pytest.approx(1800.0)
    assert total_seam_length([]) == 0.0


# ---------------------------------------------------------------------------
# End-to-end on the shipped examples
# ---------------------------------------------------------------------------


def test_simple_example_produces_expected_seams():
    """Layout is a 2×2 grid — four interior seams, no corner-only matches.
    Hand calculation:
      P001-P002   1800 mm  (vertical, full row 1)
      P001-P003   3200 mm  (horizontal, left column)
      P002-P004   2800 mm  (horizontal, right column)
      P003-P004   1800 mm  (vertical, full row 2)
    Total 9600 mm.
    """
    pi = engine.load_input_from_file(EXAMPLES / "input_floor_simple.json")
    output = engine.run(pi)
    option = output.layout_options[0]

    assert len(option.seams) == 4
    assert option.metrics.seam_count == 4
    assert option.metrics.total_seam_length == pytest.approx(9600.0, abs=1.0)


def test_seam_metrics_consistent_with_seam_list():
    """`metrics.seam_count` and `metrics.total_seam_length` must always
    match the contents of `option.seams`."""
    for name in ("input_floor_simple.json", "input_floor_with_hole.json"):
        pi = engine.load_input_from_file(EXAMPLES / name)
        output = engine.run(pi)
        option = output.layout_options[0]
        assert option.metrics.seam_count == len(option.seams), name
        sum_lengths = sum(s.length for s in option.seams)
        assert option.metrics.total_seam_length == pytest.approx(
            sum_lengths, abs=0.01
        ), name


def test_every_seam_references_valid_pieces_and_has_real_geometry():
    for name in ("input_floor_simple.json", "input_floor_with_hole.json"):
        pi = engine.load_input_from_file(EXAMPLES / name)
        output = engine.run(pi)
        option = output.layout_options[0]
        valid_piece_ids = {p.piece_id for p in option.placed_pieces}
        for seam in option.seams:
            for pid in seam.piece_ids:
                assert pid in valid_piece_ids, (
                    f"{name}: seam {seam.seam_id} references unknown piece {pid}"
                )
            assert len(seam.line) >= 2
            assert seam.length > 0


def test_hole_example_emits_seams_around_the_column():
    """The hole-split sub-pieces (P001-P004 from S001) all touch their
    neighbours — at minimum the engine must find some seams there."""
    pi = engine.load_input_from_file(EXAMPLES / "input_floor_with_hole.json")
    output = engine.run(pi)
    option = output.layout_options[0]
    s001_pieces = {p.piece_id for p in option.placed_pieces if p.slab_id == "S001"}
    # Some pair of S001 sub-pieces must share a seam (left↔below, right↔above, …).
    s001_internal_seams = [
        s for s in option.seams
        if all(pid in s001_pieces for pid in s.piece_ids)
    ]
    assert len(s001_internal_seams) >= 1
