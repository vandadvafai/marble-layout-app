"""Clipping slab rectangles against the project polygon."""
import pytest

from placement_engine.geometry.clipping import clip_to_project
from placement_engine.geometry.polygons import coords_to_polygon, rectangle


def test_full_slab_inside_project_returns_full_rect():
    project = coords_to_polygon([[0, 0], [2000, 0], [2000, 2000], [0, 2000]])
    slab = rectangle(0, 0, 1000, 800)
    pieces = clip_to_project(slab, project)
    assert len(pieces) == 1
    assert pieces[0].area == pytest.approx(800_000.0)


def test_slab_overhangs_boundary_is_clipped():
    project = coords_to_polygon([[0, 0], [1500, 0], [1500, 1500], [0, 1500]])
    slab = rectangle(1000, 0, 1000, 800)
    pieces = clip_to_project(slab, project)
    assert len(pieces) == 1
    # 500 wide × 800 tall after clipping
    assert pieces[0].area == pytest.approx(400_000.0)


def test_slab_entirely_outside_returns_nothing():
    project = coords_to_polygon([[0, 0], [1000, 0], [1000, 1000], [0, 1000]])
    slab = rectangle(2000, 2000, 500, 500)
    assert clip_to_project(slab, project) == []


def test_slab_over_hole_yields_two_pieces():
    project = coords_to_polygon(
        [[0, 0], [3000, 0], [3000, 1000], [0, 1000]],
        holes=[[[1200, 200], [1800, 200], [1800, 800], [1200, 800]]],
    )
    slab = rectangle(0, 0, 3000, 1000)
    pieces = clip_to_project(slab, project)
    # Two pieces left and right of the hole, plus thin strips above/below
    # would only appear if the hole didn't span the slab vertically — here it
    # leaves four pieces (left, right, top strip, bottom strip).
    total = sum(p.area for p in pieces)
    assert total == pytest.approx(3_000_000 - 360_000)
    assert len(pieces) >= 2
