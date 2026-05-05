"""Basic waste/area metrics."""
import pytest

from placement_engine.geometry.polygons import coords_to_polygon
from placement_engine.models import PlacedPiece, Slab, TextureTransform
from placement_engine.scoring.waste import compute_basic_metrics


def _piece(piece_id, slab_id, poly):
    return PlacedPiece(
        piece_id=piece_id,
        slab_id=slab_id,
        project_polygon=poly,
        slab_polygon=poly,
        rotation=0.0,
        texture_transform=TextureTransform(
            uv_origin=(0, 0), uv_width=100, uv_height=100
        ),
    )


def test_full_coverage_no_waste():
    project = coords_to_polygon([[0, 0], [1000, 0], [1000, 1000], [0, 1000]])
    slabs = [Slab(slab_id="S1", width=1000, height=1000, thickness=20)]
    pieces = [_piece("P1", "S1", [[0, 0], [1000, 0], [1000, 1000], [0, 1000]])]
    m = compute_basic_metrics(project, pieces, slabs)
    assert m.installed_area == pytest.approx(1_000_000.0)
    assert m.total_slab_area_used == pytest.approx(1_000_000.0)
    assert m.waste_area == pytest.approx(0.0)
    assert m.waste_percentage == pytest.approx(0.0)
    assert m.piece_count == 1
    assert m.slabs_used == 1


def test_partial_use_creates_waste():
    project = coords_to_polygon([[0, 0], [500, 0], [500, 500], [0, 500]])
    slabs = [Slab(slab_id="S1", width=1000, height=1000, thickness=20)]
    pieces = [_piece("P1", "S1", [[0, 0], [500, 0], [500, 500], [0, 500]])]
    m = compute_basic_metrics(project, pieces, slabs)
    assert m.installed_area == pytest.approx(250_000.0)
    assert m.total_slab_area_used == pytest.approx(1_000_000.0)
    assert m.waste_area == pytest.approx(750_000.0)
    assert m.waste_percentage == pytest.approx(75.0)


def test_slab_used_once_even_with_multiple_pieces():
    project = coords_to_polygon([[0, 0], [1000, 0], [1000, 500], [0, 500]])
    slabs = [Slab(slab_id="S1", width=1000, height=1000, thickness=20)]
    pieces = [
        _piece("P1", "S1", [[0, 0], [500, 0], [500, 500], [0, 500]]),
        _piece("P2", "S1", [[500, 0], [1000, 0], [1000, 500], [500, 500]]),
    ]
    m = compute_basic_metrics(project, pieces, slabs)
    assert m.piece_count == 2
    assert m.slabs_used == 1
