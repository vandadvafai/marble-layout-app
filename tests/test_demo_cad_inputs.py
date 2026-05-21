"""Demo CAD inputs — generator + intake round-trip.

These tests don't depend on the committed `examples/cad_inputs/demo/`
DXFs; they re-run the generator into `tmp_path` so the specs in
[`generate_demo_cad_inputs.DEMOS`] are the source of truth. One test
also asserts the committed artifacts exist so a forgotten regeneration
after editing the specs surfaces immediately.
"""
from pathlib import Path

import pytest
from shapely.geometry import Polygon

from generate_demo_cad_inputs import (
    DEMOS,
    DemoSpec,
    write_demo_dxf,
    write_demo_preview,
)
from placement_engine import engine
from placement_engine.cad_intake import (
    build_project_input,
    build_project_input_dict,
)
from placement_engine.cad_intake.inspection import inspect_dxf
from placement_engine.exporters.package import write_package


REPO_ROOT = Path(__file__).resolve().parents[1]
COMMITTED_DXF_DIR = REPO_ROOT / "examples" / "cad_inputs" / "demo"


def _spec_by_name(name: str) -> DemoSpec:
    for s in DEMOS:
        if s.name == name:
            return s
    raise AssertionError(f"DemoSpec {name!r} not in DEMOS")


# ---------------------------------------------------------------------------
# Generator basics
# ---------------------------------------------------------------------------


def test_five_demos_are_defined():
    """Names locked to the milestone spec; reordering or renaming any
    breaks downstream docs and committed examples."""
    assert [d.name for d in DEMOS] == [
        "demo_rectangle_floor",
        "demo_l_shape_floor",
        "demo_floor_with_column",
        "demo_irregular_apartment_floor",
        "demo_long_corridor",
    ]


def test_committed_demo_dxfs_exist():
    """If a spec changes the generator must be re-run before commit."""
    for spec in DEMOS:
        assert (COMMITTED_DXF_DIR / f"{spec.name}.dxf").exists(), (
            f"committed {spec.name}.dxf is missing — re-run "
            f"`python3 generate_demo_cad_inputs.py`"
        )


# ---------------------------------------------------------------------------
# Per-demo intake validation (re-generates into tmp_path each time)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spec", DEMOS, ids=lambda s: s.name)
def test_each_demo_passes_inspection(tmp_path, spec):
    dxf = write_demo_dxf(spec, tmp_path / f"{spec.name}.dxf")
    report = inspect_dxf(dxf)
    assert report.errors == [], (
        f"{spec.name} inspection errors: {report.errors}"
    )
    assert report.boundary_polyline_count == 1
    assert report.hole_polyline_count == len(spec.holes)
    # boundary area should match the spec.
    assert report.boundary_area_mm2 == pytest.approx(
        Polygon(spec.boundary).area
    )


@pytest.mark.parametrize("spec", DEMOS, ids=lambda s: s.name)
def test_each_demo_converts_to_engine_input(tmp_path, spec):
    """Every demo must build a ProjectInput when the default test
    inventory is attached."""
    dxf = write_demo_dxf(spec, tmp_path / f"{spec.name}.dxf")
    project = build_project_input(
        dxf, project_id=f"cad_{spec.name}", project_type="floor"
    )
    assert project.layout.boundary
    assert len(project.layout.holes) == len(spec.holes)
    assert project.slabs, "default test inventory should be attached"


def test_rectangle_demo_round_trips_through_intake(tmp_path):
    """Boundary coordinates from the intake must match the spec exactly."""
    spec = _spec_by_name("demo_rectangle_floor")
    dxf = write_demo_dxf(spec, tmp_path / f"{spec.name}.dxf")
    payload = build_project_input_dict(dxf, project_id="cad_rect_demo")
    assert payload["layout"]["boundary"] == [
        (0.0, 0.0), (6000.0, 0.0), (6000.0, 4000.0), (0.0, 4000.0),
    ]
    assert payload["layout"]["holes"] == []


def test_l_shape_demo_preserves_six_vertices(tmp_path):
    spec = _spec_by_name("demo_l_shape_floor")
    dxf = write_demo_dxf(spec, tmp_path / f"{spec.name}.dxf")
    payload = build_project_input_dict(dxf, project_id="cad_l_demo")
    assert len(payload["layout"]["boundary"]) == 6
    assert payload["layout"]["holes"] == []


def test_floor_with_column_demo_extracts_one_boundary_and_one_hole(tmp_path):
    spec = _spec_by_name("demo_floor_with_column")
    dxf = write_demo_dxf(spec, tmp_path / f"{spec.name}.dxf")
    payload = build_project_input_dict(dxf, project_id="cad_col_demo")
    assert len(payload["layout"]["boundary"]) == 4
    assert len(payload["layout"]["holes"]) == 1
    # Spec puts the hole around the floor centre.
    hole = Polygon(payload["layout"]["holes"][0])
    assert hole.area == pytest.approx(600 * 600)


def test_irregular_apartment_floor_extracts_three_holes(tmp_path):
    spec = _spec_by_name("demo_irregular_apartment_floor")
    dxf = write_demo_dxf(spec, tmp_path / f"{spec.name}.dxf")
    payload = build_project_input_dict(dxf, project_id="cad_apt_demo")
    assert len(payload["layout"]["boundary"]) == 6
    assert len(payload["layout"]["holes"]) == 3
    # All three holes must sit strictly inside the irregular boundary.
    boundary = Polygon(payload["layout"]["boundary"])
    for hole_coords in payload["layout"]["holes"]:
        assert boundary.contains(Polygon(hole_coords))


# ---------------------------------------------------------------------------
# End-to-end: long corridor through the engine
# ---------------------------------------------------------------------------


def test_long_corridor_demo_runs_through_engine(tmp_path):
    """Generate the DXF, build a ProjectInput, run the engine."""
    spec = _spec_by_name("demo_long_corridor")
    dxf = write_demo_dxf(spec, tmp_path / f"{spec.name}.dxf")
    project = build_project_input(
        dxf, project_id="cad_corridor_demo",
        options_requested=["balanced", "lowest_waste"],
    )
    output = engine.run(project)
    assert len(output.layout_options) == 2
    for opt in output.layout_options:
        # Balanced will leave the top 200 mm strip uncovered; lowest_waste
        # reuses S006's leftover and improves on it. Both should produce
        # >= 1 placed piece.
        assert opt.placed_pieces
        assert opt.metrics.coverage_percentage > 80.0


def test_floor_with_column_runs_dxf_to_package(tmp_path):
    """Full flagship workflow:

        demo_floor_with_column.dxf
        → build_project_input
        → engine.run
        → write_package
    """
    spec = _spec_by_name("demo_floor_with_column")
    dxf = write_demo_dxf(spec, tmp_path / "input.dxf")
    project = build_project_input(
        dxf, project_id="cad_floor_with_column_demo",
        options_requested=["balanced", "lowest_waste"],
    )
    output = engine.run(project)

    pkg_dir = tmp_path / "pkg"
    written = write_package(project, output, pkg_dir, render_preview=False)
    assert set(written.keys()) == {"balanced", "lowest_waste"}
    for strategy in ("balanced", "lowest_waste"):
        assert (pkg_dir / f"layout_{strategy}.dxf").exists()
        assert (pkg_dir / f"layout_{strategy}_report.md").exists()
        assert (pkg_dir / f"layout_{strategy}.json").exists()


# ---------------------------------------------------------------------------
# Preview generator smoke test (no visual check, just file produced)
# ---------------------------------------------------------------------------


def test_preview_png_is_generated(tmp_path):
    spec = _spec_by_name("demo_irregular_apartment_floor")
    target = tmp_path / "preview.png"
    written = write_demo_preview(spec, target)
    assert written == target
    assert written.exists()
    assert written.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
