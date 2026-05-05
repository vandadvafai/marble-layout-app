"""End-to-end engine smoke tests using the example input."""
import json
from pathlib import Path

import pytest

from placement_engine import engine
from placement_engine.exporters.json_exporter import write_output
from placement_engine.models import EngineOutput

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.fixture
def simple_output() -> EngineOutput:
    project = engine.load_input_from_file(EXAMPLES / "input_floor_simple.json")
    return engine.run(project)


def test_engine_runs_and_returns_output(simple_output):
    assert simple_output.engine_version
    assert simple_output.project_id == "marble_floor_simple_001"
    assert len(simple_output.layout_options) == 1


def test_every_piece_has_required_fields(simple_output):
    for piece in simple_output.layout_options[0].placed_pieces:
        assert piece.piece_id
        assert piece.slab_id
        assert len(piece.project_polygon) >= 3
        assert len(piece.slab_polygon) >= 3
        assert piece.texture_transform is not None


def test_every_piece_references_known_slab(simple_output):
    project = engine.load_input_from_file(EXAMPLES / "input_floor_simple.json")
    valid_ids = {s.slab_id for s in project.slabs}
    for piece in simple_output.layout_options[0].placed_pieces:
        assert piece.slab_id in valid_ids


def test_pieces_are_geometrically_valid(simple_output):
    """Engine.run() runs validators internally; if we got here, geometry is valid.

    This test also re-checks that no two pieces overlap by re-computing
    their union area equals the sum of their areas.
    """
    from shapely.geometry import Polygon

    polys = [Polygon(p.project_polygon) for p in simple_output.layout_options[0].placed_pieces]
    sum_area = sum(p.area for p in polys)
    union_area = polys[0]
    for p in polys[1:]:
        union_area = union_area.union(p)
    assert union_area.area == pytest.approx(sum_area, rel=1e-6)


def test_waste_metrics_consistent(simple_output):
    m = simple_output.layout_options[0].metrics
    if m.total_slab_area_used > 0:
        expected_pct = m.waste_area / m.total_slab_area_used * 100.0
        assert m.waste_percentage == pytest.approx(expected_pct, abs=0.1)
    assert m.installed_area + m.waste_area == pytest.approx(m.total_slab_area_used, abs=1.0)


def test_output_writes_and_round_trips(simple_output, tmp_path):
    out_path = tmp_path / "out.json"
    write_output(simple_output, out_path)
    loaded = json.loads(out_path.read_text())
    assert loaded["project_id"] == simple_output.project_id
    assert loaded["layout_options"][0]["placed_pieces"]


def test_engine_is_deterministic():
    """Same input must produce the same pieces and metrics on every run."""
    p1 = engine.load_input_from_file(EXAMPLES / "input_floor_simple.json")
    p2 = engine.load_input_from_file(EXAMPLES / "input_floor_simple.json")
    o1 = engine.run(p1)
    o2 = engine.run(p2)

    pieces1 = o1.layout_options[0].placed_pieces
    pieces2 = o2.layout_options[0].placed_pieces
    assert len(pieces1) == len(pieces2)
    for a, b in zip(pieces1, pieces2):
        assert a.piece_id == b.piece_id
        assert a.slab_id == b.slab_id
        assert a.project_polygon == b.project_polygon
