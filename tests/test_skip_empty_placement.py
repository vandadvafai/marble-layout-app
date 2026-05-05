"""Regression tests for slab-not-silently-consumed behaviour.

Before this fix, the row-based strategy advanced through inventory in
lock-step with the cursor: a slab whose bbox-aligned position landed in
an empty notch of an irregular project would be "burned" without
contributing any piece. After the fix:

  - the cursor still always advances (so the loop terminates), but
  - the slab pointer only advances when at least one valid clipped
    piece is produced, and
  - an `empty_slab_placement_skipped` review marker is emitted whenever
    a placement attempt produces zero valid pieces.

These tests use a synthetic L-shape that forces a row-1 skip (so the
behaviour is easy to verify in isolation), plus a re-check on the
shipped hole example.
"""
from pathlib import Path

import pytest
from shapely.geometry import Polygon

from placement_engine import engine
from placement_engine.models import ProjectInput

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _l_shape_fixture() -> dict:
    """L-shape with the notch in the BOTTOM-RIGHT.

    Boundary (mm):
        (0,0) → (1000,0) → (1000,1000) → (2000,1000)
              → (2000,2000) → (0,2000) → close

    Three identical 1000×1000 slabs.

    Old behaviour: 2 pieces (S001 row1-left, S003 row2-left).
                   S002 burned at row1-right where the L has no boundary.
    New behaviour: 3 pieces (S001 row1-left, S002 row2-left, S003 row2-right)
                   plus 1 review marker for the failed row1-right attempt.
    """
    return {
        "project_id": "synthetic_l_bottom_right",
        "layout": {
            "boundary": [
                [0, 0], [1000, 0],
                [1000, 1000], [2000, 1000],
                [2000, 2000], [0, 2000],
            ],
        },
        "slabs": [
            {"slab_id": "S001", "width": 1000, "height": 1000, "thickness": 20},
            {"slab_id": "S002", "width": 1000, "height": 1000, "thickness": 20},
            {"slab_id": "S003", "width": 1000, "height": 1000, "thickness": 20},
        ],
        # Loosen min sizes so the synthetic 1000×1000 pieces aren't filtered.
        "rules": {"min_piece_width": 0, "min_piece_height": 0, "min_piece_area": 0},
    }


@pytest.fixture
def l_shape_output():
    pi = ProjectInput.model_validate(_l_shape_fixture())
    return pi, engine.run(pi)


def test_no_slab_silently_lost_in_l_shape(l_shape_output):
    """Inventory of 3 slabs should produce 3 pieces — none consumed for nothing."""
    _, output = l_shape_output
    option = output.layout_options[0]

    # All three slabs must show up; none are silently lost in the notch.
    placed_slab_ids = [p.slab_id for p in option.placed_pieces]
    assert sorted(placed_slab_ids) == ["S001", "S002", "S003"], (
        f"expected every slab to contribute a piece, got {placed_slab_ids}"
    )
    assert option.metrics.piece_count == 3
    assert option.metrics.slabs_used == 3


def test_no_zero_area_pieces(l_shape_output):
    _, output = l_shape_output
    for piece in output.layout_options[0].placed_pieces:
        assert Polygon(piece.project_polygon).area > 0


def test_all_emitted_polygons_are_valid(l_shape_output):
    _, output = l_shape_output
    for piece in output.layout_options[0].placed_pieces:
        proj = Polygon(piece.project_polygon)
        slab = Polygon(piece.slab_polygon)
        assert proj.is_valid
        assert slab.is_valid
        # Each piece must be a single closed ring (no interiors).
        assert len(proj.interiors) == 0


def test_engine_covers_usable_area(l_shape_output):
    """L-shape area = bottom-left 1000×1000 + top 2000×1000 strip
    = 1,000,000 + 2,000,000 = 3,000,000 mm². With 3 unit slabs and one
    skip, the engine still covers the full area."""
    pi, output = l_shape_output
    project_area = 3_000_000.0
    installed = sum(Polygon(p.project_polygon).area for p in output.layout_options[0].placed_pieces)
    assert installed == pytest.approx(project_area, abs=1.0)


def test_skip_marker_is_emitted_for_l_shape(l_shape_output):
    _, output = l_shape_output
    markers = output.layout_options[0].review_markers
    skip_markers = [m for m in markers if m.type == "empty_slab_placement_skipped"]
    assert len(skip_markers) == 1
    m = skip_markers[0]
    assert m.severity == "low"
    # The skipped position is row 1, x∈[1000,2000], y∈[0,1000].
    # Marker location is the centre of that candidate slab rect.
    assert m.location == (1500.0, 500.0)
    assert "did not intersect" in m.message


def test_hole_example_no_longer_loses_s005():
    """Before the fix, the hole example consumed S005 silently and
    placed S006 in its slot. After the fix, S005 fills the L's
    upper-right corner and S006 stays unused in inventory."""
    pi = engine.load_input_from_file(EXAMPLES / "input_floor_with_hole.json")
    output = engine.run(pi)
    placed_slab_ids = {p.slab_id for p in output.layout_options[0].placed_pieces}
    assert "S005" in placed_slab_ids
    # S006 may or may not appear depending on inventory exhaustion — but
    # the key invariant is that S005, which used to be silently burned,
    # now appears in the layout.


def test_hole_example_emits_at_least_one_skip_marker():
    pi = engine.load_input_from_file(EXAMPLES / "input_floor_with_hole.json")
    output = engine.run(pi)
    markers = output.layout_options[0].review_markers
    assert any(m.type == "empty_slab_placement_skipped" for m in markers)
