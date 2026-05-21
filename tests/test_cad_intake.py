"""Standardized CAD intake — read DXF, validate, build engine input.

Invalid fixtures are generated programmatically in `tmp_path` rather
than committed to the repo so the failure modes are obvious from
reading the test (no out-of-band DXFs to inspect separately).
"""
from pathlib import Path

import ezdxf
import pytest

from placement_engine import engine
from placement_engine.cad_intake import (
    CADIntakeError,
    build_project_input,
    build_project_input_dict,
)
from placement_engine.cad_intake.inspection import (
    format_report_markdown,
    inspect_dxf,
)
from placement_engine.exporters.package import write_package
from placement_engine.models import ProjectInput

EXAMPLES_CAD = Path(__file__).resolve().parents[1] / "examples" / "cad_inputs"


# ---------------------------------------------------------------------------
# Helpers — build minimal standardized DXFs in tmp_path
# ---------------------------------------------------------------------------


def _new_standard_doc():
    doc = ezdxf.new(dxfversion="R2013", setup=True)
    doc.units = ezdxf.units.MM
    doc.layers.add(name="AI_PROJECT_BOUNDARY", color=7)
    doc.layers.add(name="AI_HOLES_CUTOUTS", color=1)
    return doc


def _write_dxf(doc, tmp_path: Path, name: str) -> Path:
    target = tmp_path / name
    doc.saveas(target)
    return target


def _basic_rectangle(tmp_path) -> Path:
    doc = _new_standard_doc()
    doc.modelspace().add_lwpolyline(
        [(0, 0), (6000, 0), (6000, 4000), (0, 4000)],
        close=True, dxfattribs={"layer": "AI_PROJECT_BOUNDARY"},
    )
    return _write_dxf(doc, tmp_path, "basic.dxf")


def _rectangle_with_hole(tmp_path) -> Path:
    doc = _new_standard_doc()
    doc.modelspace().add_lwpolyline(
        [(0, 0), (6000, 0), (6000, 4000), (0, 4000)],
        close=True, dxfattribs={"layer": "AI_PROJECT_BOUNDARY"},
    )
    doc.modelspace().add_lwpolyline(
        [(2700, 1750), (3300, 1750), (3300, 2250), (2700, 2250)],
        close=True, dxfattribs={"layer": "AI_HOLES_CUTOUTS"},
    )
    return _write_dxf(doc, tmp_path, "with_hole.dxf")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_basic_rectangle_converts(tmp_path):
    dxf = _basic_rectangle(tmp_path)
    payload = build_project_input_dict(
        dxf, project_id="cad_basic_001", include_test_slabs=True
    )
    assert payload["project_id"] == "cad_basic_001"
    assert payload["units"] == "mm"
    assert payload["layout"]["boundary"] == [
        (0.0, 0.0), (6000.0, 0.0), (6000.0, 4000.0), (0.0, 4000.0),
    ]
    assert payload["layout"]["holes"] == []
    assert payload["layout"]["source_file"]["type"] == "dxf"
    # Test inventory attached on request.
    assert len(payload["slabs"]) == 6


def test_rectangle_with_hole_converts(tmp_path):
    dxf = _rectangle_with_hole(tmp_path)
    payload = build_project_input_dict(dxf, project_id="cad_hole_001")
    assert len(payload["layout"]["holes"]) == 1
    hole = payload["layout"]["holes"][0]
    assert hole == [
        (2700.0, 1750.0), (3300.0, 1750.0),
        (3300.0, 2250.0), (2700.0, 2250.0),
    ]


def test_generated_payload_validates_via_pydantic(tmp_path):
    """With test slabs attached, the payload must round-trip through
    `ProjectInput.model_validate` without error."""
    dxf = _rectangle_with_hole(tmp_path)
    payload = build_project_input_dict(
        dxf, project_id="cad_hole_001", include_test_slabs=True
    )
    project = ProjectInput.model_validate(payload)
    assert project.layout.boundary
    assert len(project.layout.holes) == 1


def test_geometry_only_payload_has_empty_slabs(tmp_path):
    """Without --include-test-slabs the payload is a draft for the
    designer to fill in; it should still be valid JSON-able data."""
    dxf = _basic_rectangle(tmp_path)
    payload = build_project_input_dict(dxf, project_id="cad_draft_001")
    assert payload["slabs"] == []
    # And ProjectInput.model_validate must refuse this (engine needs slabs).
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ProjectInput.model_validate(payload)


def test_default_rules_and_design_requirements_attached(tmp_path):
    dxf = _basic_rectangle(tmp_path)
    payload = build_project_input_dict(dxf, project_id="cad_defaults_001")
    rules = payload["rules"]
    assert rules["min_piece_width"] == 120
    assert rules["min_piece_height"] == 120
    assert rules["min_piece_area"] == 25000
    assert rules["allow_partial_slab_use"] is True

    design = payload["design_requirements"]
    assert design["priority"] == "balanced"
    assert design["avoid_defects"] is True


def test_strategy_flag_populates_options_requested(tmp_path):
    dxf = _basic_rectangle(tmp_path)
    payload = build_project_input_dict(
        dxf, project_id="cad_strategy_001",
        include_test_slabs=True,
        options_requested=["balanced", "lowest_waste"],
    )
    assert payload["options_requested"] == ["balanced", "lowest_waste"]


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


def test_missing_layer_raises(tmp_path):
    """A DXF with no AI_PROJECT_BOUNDARY entities should fail loudly."""
    doc = ezdxf.new(dxfversion="R2013", setup=True)
    # Deliberately no AI_PROJECT_BOUNDARY entity.
    dxf = _write_dxf(doc, tmp_path, "empty.dxf")
    with pytest.raises(CADIntakeError, match="AI_PROJECT_BOUNDARY"):
        build_project_input_dict(dxf, project_id="cad_empty_001")


def test_multiple_boundaries_raises(tmp_path):
    doc = _new_standard_doc()
    doc.modelspace().add_lwpolyline(
        [(0, 0), (1000, 0), (1000, 1000), (0, 1000)],
        close=True, dxfattribs={"layer": "AI_PROJECT_BOUNDARY"},
    )
    doc.modelspace().add_lwpolyline(
        [(2000, 0), (3000, 0), (3000, 1000), (2000, 1000)],
        close=True, dxfattribs={"layer": "AI_PROJECT_BOUNDARY"},
    )
    dxf = _write_dxf(doc, tmp_path, "two_boundaries.dxf")
    with pytest.raises(CADIntakeError, match=r"exactly one closed polyline"):
        build_project_input_dict(dxf, project_id="cad_two_001")


def test_hole_outside_boundary_raises(tmp_path):
    doc = _new_standard_doc()
    doc.modelspace().add_lwpolyline(
        [(0, 0), (1000, 0), (1000, 1000), (0, 1000)],
        close=True, dxfattribs={"layer": "AI_PROJECT_BOUNDARY"},
    )
    doc.modelspace().add_lwpolyline(
        # Sits well outside the boundary.
        [(2000, 2000), (2500, 2000), (2500, 2500), (2000, 2500)],
        close=True, dxfattribs={"layer": "AI_HOLES_CUTOUTS"},
    )
    dxf = _write_dxf(doc, tmp_path, "hole_outside.dxf")
    with pytest.raises(CADIntakeError, match="not fully inside"):
        build_project_input_dict(dxf, project_id="cad_outside_001")


def test_unclosed_boundary_polyline_raises(tmp_path):
    doc = _new_standard_doc()
    doc.modelspace().add_lwpolyline(
        [(0, 0), (1000, 0), (1000, 1000), (0, 1000)],
        close=False,  # ← the designer forgot to close it
        dxfattribs={"layer": "AI_PROJECT_BOUNDARY"},
    )
    dxf = _write_dxf(doc, tmp_path, "unclosed.dxf")
    with pytest.raises(CADIntakeError, match="not closed"):
        build_project_input_dict(dxf, project_id="cad_unclosed_001")


def test_unsupported_entity_on_required_layer_raises(tmp_path):
    """A LINE on AI_PROJECT_BOUNDARY should fail with a designer-actionable hint."""
    doc = _new_standard_doc()
    doc.modelspace().add_line(
        (0, 0), (1000, 0), dxfattribs={"layer": "AI_PROJECT_BOUNDARY"}
    )
    dxf = _write_dxf(doc, tmp_path, "with_line.dxf")
    with pytest.raises(CADIntakeError, match=r"Unsupported entity 'LINE'"):
        build_project_input_dict(dxf, project_id="cad_line_001")


def test_self_intersecting_boundary_raises(tmp_path):
    doc = _new_standard_doc()
    # Bowtie — classic self-intersecting polygon.
    doc.modelspace().add_lwpolyline(
        [(0, 0), (1000, 1000), (1000, 0), (0, 1000)],
        close=True, dxfattribs={"layer": "AI_PROJECT_BOUNDARY"},
    )
    dxf = _write_dxf(doc, tmp_path, "bowtie.dxf")
    with pytest.raises(CADIntakeError, match="self-intersecting"):
        build_project_input_dict(dxf, project_id="cad_bowtie_001")


def test_missing_file_raises_clear_error(tmp_path):
    with pytest.raises(CADIntakeError, match="does not exist"):
        build_project_input_dict(
            tmp_path / "nope.dxf", project_id="cad_missing_001"
        )


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------


def test_inspection_reports_boundary_area_and_hole_count(tmp_path):
    dxf = _rectangle_with_hole(tmp_path)
    report = inspect_dxf(dxf)
    assert report.boundary_polyline_count == 1
    assert report.hole_polyline_count == 1
    assert report.boundary_area_mm2 == pytest.approx(6000 * 4000)
    assert report.boundary_bbox == (0.0, 0.0, 6000.0, 4000.0)
    assert report.hole_areas_mm2 == [pytest.approx(600 * 500)]
    assert report.errors == []


def test_inspection_reports_errors_without_raising(tmp_path):
    """Bad DXF: no boundary at all. inspect_dxf should populate
    `errors` rather than raise — the inspect CLI is meant for diagnosing
    broken files."""
    doc = ezdxf.new(dxfversion="R2013", setup=True)
    dxf = _write_dxf(doc, tmp_path, "empty.dxf")
    report = inspect_dxf(dxf)
    assert report.errors
    assert any("AI_PROJECT_BOUNDARY" in e for e in report.errors)


def test_inspection_markdown_renders(tmp_path):
    dxf = _rectangle_with_hole(tmp_path)
    md = format_report_markdown(inspect_dxf(dxf))
    assert "# CAD Intake Inspection" in md
    assert "AI_PROJECT_BOUNDARY" in md
    assert "Project boundary" in md
    assert "Holes / cutouts" in md


# ---------------------------------------------------------------------------
# Bundled examples + end-to-end integration
# ---------------------------------------------------------------------------


def test_bundled_basic_floor_example_converts():
    """The committed example DXF must keep working as the intake evolves."""
    payload = build_project_input_dict(
        EXAMPLES_CAD / "basic_floor_standardized.dxf",
        project_id="cad_basic_floor_001",
        include_test_slabs=True,
    )
    project = ProjectInput.model_validate(payload)
    assert project.layout.boundary
    assert len(project.layout.holes) == 0


def test_end_to_end_dxf_to_package(tmp_path):
    """Full MVP workflow:

        standardized DXF
        → build_project_input
        → engine.run
        → write_package (DXF + report + JSON + preview)

    Asserting that every step produces the expected files makes the
    whole pipeline a single test we can rely on.
    """
    project = build_project_input(
        EXAMPLES_CAD / "floor_with_hole_standardized.dxf",
        project_id="cad_e2e_001",
        options_requested=["balanced", "lowest_waste"],
    )
    output = engine.run(project)

    written = write_package(project, output, tmp_path, render_preview=False)
    # Both strategies must produce a 3-file set.
    assert set(written.keys()) == {"balanced", "lowest_waste"}
    for strategy in ("balanced", "lowest_waste"):
        for ext in ("json", "dxf"):
            assert (tmp_path / f"layout_{strategy}.{ext}").exists()
        assert (tmp_path / f"layout_{strategy}_report.md").exists()

    # Coverage on this floor should be 100 % (6 × 5.76 M slab area vs
    # 24 M − 0.3 M project usable area). Both strategies should reach it.
    for opt in output.layout_options:
        assert opt.metrics.coverage_percentage == pytest.approx(100.0, abs=0.5)
