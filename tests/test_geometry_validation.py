"""Project polygon assembly: holes inside, validity checks."""
import pytest

from placement_engine.geometry.validation import (
    GeometryValidationError,
    build_project_polygon,
)
from placement_engine.models import Layout


def _layout(boundary, holes=None):
    return Layout.model_validate({"boundary": boundary, "holes": holes or []})


def test_simple_rectangle_builds():
    poly = build_project_polygon(
        _layout([[0, 0], [1000, 0], [1000, 500], [0, 500]])
    )
    assert poly.area == pytest.approx(500_000.0)


def test_hole_subtracts_from_area():
    poly = build_project_polygon(
        _layout(
            [[0, 0], [1000, 0], [1000, 1000], [0, 1000]],
            holes=[[[200, 200], [400, 200], [400, 400], [200, 400]]],
        )
    )
    assert poly.area == pytest.approx(1_000_000 - 40_000)


def test_hole_outside_boundary_rejected():
    with pytest.raises(GeometryValidationError, match="not fully inside"):
        build_project_polygon(
            _layout(
                [[0, 0], [100, 0], [100, 100], [0, 100]],
                holes=[[[200, 200], [300, 200], [300, 300], [200, 300]]],
            )
        )


def test_self_intersecting_boundary_rejected():
    # Bowtie: classic invalid polygon.
    with pytest.raises(ValueError):
        build_project_polygon(
            _layout([[0, 0], [100, 100], [100, 0], [0, 100]])
        )
