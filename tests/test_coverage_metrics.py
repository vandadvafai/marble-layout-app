"""Coverage and inventory metrics.

These tests pin down the *business-honest* behaviour:

  - A layout with low slab waste but poor project coverage must NOT
    look successful. `coverage_percentage`, `layout_status`, and
    `inventory_status` exist precisely so a caller can see this without
    having to compare numbers themselves.

  - When `uncovered_area > 0`, the engine emits an
    `incomplete_coverage` review marker. When the inventory was also
    fully consumed, a second `insufficient_inventory` marker fires.
"""
import pytest

from placement_engine import engine
from placement_engine.models import ProjectInput

EXAMPLES = __import__("pathlib").Path(__file__).resolve().parents[1] / "examples"


# ---------------------------------------------------------------------------
# Bundled examples: both should now report complete coverage.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "input_name",
    ["input_floor_simple.json", "input_floor_with_hole.json"],
)
def test_shipped_examples_are_completely_covered(input_name):
    pi = engine.load_input_from_file(EXAMPLES / input_name)
    output = engine.run(pi)
    m = output.layout_options[0].metrics

    assert m.layout_status == "complete"
    assert m.inventory_status == "sufficient"
    assert m.coverage_percentage == pytest.approx(100.0, abs=0.01)
    assert m.uncovered_area == pytest.approx(0.0, abs=1.0)
    # And: no incomplete_coverage / insufficient_inventory markers.
    types = {mk.type for mk in output.layout_options[0].review_markers}
    assert "incomplete_coverage" not in types
    assert "insufficient_inventory" not in types


# ---------------------------------------------------------------------------
# Insufficient inventory: only enough slab area to cover part of the floor.
# ---------------------------------------------------------------------------


def _insufficient_fixture() -> dict:
    """6 m × 4 m floor (24 000 000 mm²) but only one 3.2 m × 1.8 m slab
    (5 760 000 mm²). Coverage cannot exceed 24 %, and the single slab
    is consumed."""
    return {
        "project_id": "insufficient_demo",
        "layout": {
            "boundary": [[0, 0], [6000, 0], [6000, 4000], [0, 4000]],
        },
        "slabs": [
            {"slab_id": "S001", "width": 3200, "height": 1800, "thickness": 20},
        ],
        "rules": {
            "min_piece_width": 0, "min_piece_height": 0, "min_piece_area": 0,
        },
    }


@pytest.fixture
def insufficient_output():
    pi = ProjectInput.model_validate(_insufficient_fixture())
    return pi, engine.run(pi)


def test_insufficient_inventory_reports_partial_coverage(insufficient_output):
    _, output = insufficient_output
    m = output.layout_options[0].metrics

    assert m.project_usable_area == pytest.approx(24_000_000.0)
    assert m.installed_area == pytest.approx(5_760_000.0, abs=1.0)
    assert m.uncovered_area == pytest.approx(18_240_000.0, abs=1.0)
    assert m.coverage_percentage == pytest.approx(24.0, abs=0.1)
    assert m.layout_status == "partial"
    assert m.inventory_status == "insufficient"


def test_insufficient_inventory_emits_both_review_markers(insufficient_output):
    _, output = insufficient_output
    types = {mk.type for mk in output.layout_options[0].review_markers}
    assert "incomplete_coverage" in types
    assert "insufficient_inventory" in types
    # Both should be high severity — incomplete coverage usually blocks
    # acceptance of the layout.
    coverage_marker = next(
        mk for mk in output.layout_options[0].review_markers
        if mk.type == "incomplete_coverage"
    )
    assert coverage_marker.severity == "high"
    # Layout-level markers carry no specific location.
    assert coverage_marker.location is None


def test_zero_slab_waste_does_not_imply_success(insufficient_output):
    """The flagship business case: one 3200×1800 slab is fully used, so
    `waste_percentage` is 0 — but the layout is still partial and the
    inventory is insufficient. The status fields make this obvious."""
    _, output = insufficient_output
    m = output.layout_options[0].metrics
    assert m.waste_percentage == pytest.approx(0.0, abs=0.01)
    # …yet the project is far from finished.
    assert m.layout_status != "complete"
    assert m.inventory_status != "sufficient"


# ---------------------------------------------------------------------------
# Long corridor that the strategy can't fully cover (tall slabs, narrow strip).
# ---------------------------------------------------------------------------


def _corridor_fixture() -> dict:
    """4 000 mm × 100 mm corridor (400 000 mm²). Three 1000×1000 slabs
    are stacked left-to-right; each gets clipped to 1000×100 = 100 000.
    Three of them cover only 300 000 mm² → 75 %. The 4th 1000-mm column
    has no slab, so 100 000 mm² remain uncovered and inventory is
    consumed."""
    return {
        "project_id": "corridor_demo",
        "layout": {
            "boundary": [[0, 0], [4000, 0], [4000, 100], [0, 100]],
        },
        "slabs": [
            {"slab_id": "S001", "width": 1000, "height": 1000, "thickness": 20},
            {"slab_id": "S002", "width": 1000, "height": 1000, "thickness": 20},
            {"slab_id": "S003", "width": 1000, "height": 1000, "thickness": 20},
        ],
        "rules": {
            "min_piece_width": 0, "min_piece_height": 0, "min_piece_area": 0,
        },
    }


def test_corridor_with_uncovered_strip():
    pi = ProjectInput.model_validate(_corridor_fixture())
    output = engine.run(pi)
    m = output.layout_options[0].metrics

    assert m.project_usable_area == pytest.approx(400_000.0)
    assert m.installed_area == pytest.approx(300_000.0, abs=1.0)
    assert m.coverage_percentage == pytest.approx(75.0, abs=0.1)
    assert m.layout_status == "partial"
    assert m.inventory_status == "insufficient"


# ---------------------------------------------------------------------------
# Schema sanity: the new fields exist alongside the old ones.
# ---------------------------------------------------------------------------


def test_metrics_schema_includes_new_and_legacy_fields():
    pi = engine.load_input_from_file(EXAMPLES / "input_floor_simple.json")
    output = engine.run(pi)
    raw = output.to_json_dict()["layout_options"][0]["metrics"]

    # New fields
    for field in (
        "project_usable_area",
        "uncovered_area",
        "coverage_percentage",
        "layout_status",
        "inventory_status",
    ):
        assert field in raw, f"missing new metric field: {field}"

    # Existing fields still present
    for field in (
        "installed_area",
        "total_slab_area_used",
        "waste_area",
        "waste_percentage",
        "reusable_offcut_area",
        "non_reusable_waste_area",
        "piece_count",
        "slabs_used",
        "cut_count_estimate",
        "seam_count",
        "total_seam_length",
        "small_piece_count",
        "cutting_complexity_score",
        "estimated_production_difficulty",
    ):
        assert field in raw, f"missing legacy metric field: {field}"


def test_layout_status_is_failed_when_no_pieces_placed(monkeypatch):
    """If the strategy returns zero pieces, layout_status should be
    'failed' and inventory_status 'unknown' (slabs remain unused)."""
    from placement_engine.scoring.waste import compute_basic_metrics
    from placement_engine.geometry.polygons import coords_to_polygon
    from placement_engine.models import Slab

    project = coords_to_polygon([[0, 0], [1000, 0], [1000, 1000], [0, 1000]])
    slabs = [
        Slab(slab_id="S001", width=500, height=500, thickness=20),
        Slab(slab_id="S002", width=500, height=500, thickness=20),
    ]
    metrics = compute_basic_metrics(project, [], slabs)
    assert metrics.layout_status == "failed"
    assert metrics.inventory_status == "unknown"
    assert metrics.coverage_percentage == 0.0
    assert metrics.uncovered_area == pytest.approx(1_000_000.0)
