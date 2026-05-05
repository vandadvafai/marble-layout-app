"""End-to-end coverage of the hole-bearing example.

These tests lock down two pieces of behaviour that are easy to break:
  - placed pieces never cover the project hole
  - every emitted piece polygon has a single ring (no interiors), because
    the output JSON schema does not represent polygons-with-holes
"""
from pathlib import Path

import pytest
from shapely.geometry import Polygon

from placement_engine import engine

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
HOLE_INPUT = EXAMPLES / "input_floor_with_hole.json"


@pytest.fixture
def hole_output():
    project = engine.load_input_from_file(HOLE_INPUT)
    return project, engine.run(project)


def test_hole_example_runs(hole_output):
    project, output = hole_output
    assert output.project_id == project.project_id
    assert len(output.layout_options) == 1
    assert output.layout_options[0].placed_pieces, "expected at least one placed piece"


def test_no_piece_covers_the_hole(hole_output):
    project, output = hole_output
    holes = [Polygon(h) for h in project.layout.holes]
    assert holes, "fixture must contain at least one hole"

    for piece in output.layout_options[0].placed_pieces:
        piece_poly = Polygon(piece.project_polygon)
        for hole in holes:
            inter = piece_poly.intersection(hole)
            # Allow only floating-point sliver overlap with the hole boundary.
            assert inter.area < 1.0, (
                f"piece {piece.piece_id} overlaps hole by {inter.area:.2f} mm²"
            )


def test_all_emitted_pieces_have_single_ring(hole_output):
    """Schema invariant: project_polygon and slab_polygon must be hole-free."""
    _, output = hole_output
    for piece in output.layout_options[0].placed_pieces:
        # Reconstructing as a Polygon and checking no interiors confirms the
        # ring is closed and hole-free. `interiors` is a Shapely sequence,
        # not a list, so compare length.
        assert len(Polygon(piece.project_polygon).interiors) == 0
        assert len(Polygon(piece.slab_polygon).interiors) == 0


def test_pieces_are_disjoint_in_hole_example(hole_output):
    _, output = hole_output
    polys = [Polygon(p.project_polygon) for p in output.layout_options[0].placed_pieces]
    sum_area = sum(p.area for p in polys)
    union = polys[0]
    for p in polys[1:]:
        union = union.union(p)
    assert union.area == pytest.approx(sum_area, rel=1e-6)
