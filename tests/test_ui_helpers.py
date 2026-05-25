"""Shared UI/CLI orchestration helpers (`placement_engine.ui.app_helpers`).

These cover the reusable logic the Streamlit app depends on, so the UI
itself only needs a light smoke test (`test_streamlit_app.py`).
"""
import io
import zipfile
from pathlib import Path

import ezdxf
import pytest

from placement_engine.cad_intake.dxf_reader import CADIntakeError
from placement_engine.ui.app_helpers import (
    PackageResult,
    build_package_zip,
    generate_layout_package,
    headline_metrics,
    split_review_markers,
)

EXAMPLES_CAD = Path(__file__).resolve().parents[1] / "examples" / "cad_inputs"
DEMO_DXF = EXAMPLES_CAD / "demo" / "demo_floor_with_column.dxf"


# ---------------------------------------------------------------------------
# generate_layout_package — happy path
# ---------------------------------------------------------------------------


@pytest.fixture
def package(tmp_path) -> PackageResult:
    return generate_layout_package(
        DEMO_DXF,
        project_id="ui_test_001",
        output_dir=tmp_path / "run",
        strategies=["balanced", "lowest_waste"],
    )


def test_generate_returns_package_result(package):
    assert isinstance(package, PackageResult)
    assert package.project_id == "ui_test_001"
    assert package.strategies == ["balanced", "lowest_waste"]


def test_generate_writes_root_and_strategy_files(package):
    assert package.cad_inspection_path.is_file()
    assert package.generated_input_path.is_file()
    for strategy in ("balanced", "lowest_waste"):
        files = package.per_strategy_files[strategy]
        for kind in ("json", "dxf", "report", "preview", "pdf"):
            assert files[kind].is_file(), f"{strategy}/{kind} missing"
        # PDF is a real PDF (magic bytes + non-empty).
        assert files["pdf"].read_bytes().startswith(b"%PDF-")


def test_generate_without_preview_skips_png(tmp_path):
    result = generate_layout_package(
        DEMO_DXF, project_id="np_001", output_dir=tmp_path / "run",
        generate_preview=False,
    )
    for strategy in result.strategies:
        assert "preview" not in result.per_strategy_files[strategy]


def test_clean_output_clears_stale_files_before_regeneration(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    stale = run_dir / "stale.txt"
    stale.write_text("from a previous run")
    generate_layout_package(
        DEMO_DXF, project_id="clean_001", output_dir=run_dir,
        clean_output=True,
    )
    assert not stale.exists()
    assert (run_dir / "cad_inspection.md").is_file()


def test_missing_cad_file_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError, match="CAD file not found"):
        generate_layout_package(
            tmp_path / "nope.dxf", project_id="x",
            output_dir=tmp_path / "run",
        )


def test_invalid_dxf_missing_boundary_raises_cad_intake_error(tmp_path):
    """A DXF with no AI_PROJECT_BOUNDARY must surface a CADIntakeError
    the UI can translate into a readable message."""
    doc = ezdxf.new(dxfversion="R2013", setup=True)
    bad = tmp_path / "no_boundary.dxf"
    doc.saveas(bad)
    with pytest.raises(CADIntakeError, match="AI_PROJECT_BOUNDARY"):
        generate_layout_package(
            bad, project_id="bad_001", output_dir=tmp_path / "run",
        )


def test_no_slab_inventory_raises_value_error(tmp_path):
    with pytest.raises(ValueError, match="slab inventory"):
        generate_layout_package(
            DEMO_DXF, project_id="x", output_dir=tmp_path / "run",
            include_test_slabs=False,
        )


# ---------------------------------------------------------------------------
# build_package_zip
# ---------------------------------------------------------------------------


def test_zip_contains_expected_files(package):
    data = build_package_zip(package.output_dir)
    names = set(zipfile.ZipFile(io.BytesIO(data)).namelist())
    assert "cad_inspection.md" in names
    assert "generated_engine_input.json" in names
    for strategy in ("balanced", "lowest_waste"):
        assert f"{strategy}/layout.json" in names
        assert f"{strategy}/layout.dxf" in names
        assert f"{strategy}/layout_report.md" in names
        assert f"{strategy}/layout_report.pdf" in names
        assert f"{strategy}/preview.png" in names


def test_zip_excludes_nested_zip_files(package):
    # Writing the zip into the folder then re-zipping must not nest it.
    build_package_zip(package.output_dir,
                      zip_path=package.output_dir / "layout_package.zip")
    data = build_package_zip(package.output_dir)
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert not any(n.endswith(".zip") for n in names)


def test_zip_written_to_disk_when_path_given(package, tmp_path):
    target = tmp_path / "pkg.zip"
    data = build_package_zip(package.output_dir, zip_path=target)
    assert target.is_file()
    assert target.read_bytes() == data


# ---------------------------------------------------------------------------
# headline_metrics
# ---------------------------------------------------------------------------


def test_headline_metrics_keys_and_values(package):
    metrics = headline_metrics(package.option("lowest_waste"))
    assert set(metrics) == {
        "layout_status", "inventory_status", "coverage_percentage",
        "waste_percentage", "slabs_used", "piece_count",
        "seam_count", "total_seam_length",
    }
    # lowest_waste reaches full coverage on this known fixture.
    assert metrics["coverage_percentage"] == pytest.approx(100.0, abs=0.5)
    assert metrics["layout_status"] == "complete"


def test_headline_metrics_match_engine_output(package):
    option = package.option("balanced")
    metrics = headline_metrics(option)
    assert metrics["piece_count"] == option.metrics.piece_count
    assert metrics["coverage_percentage"] == option.metrics.coverage_percentage


# ---------------------------------------------------------------------------
# split_review_markers
# ---------------------------------------------------------------------------


def test_split_review_markers_partitions_technical_from_primary(package):
    """`empty_slab_placement_skipped` is routine bookkeeping → technical;
    coverage/inventory markers → primary."""
    for strategy in package.strategies:
        option = package.option(strategy)
        primary, technical = split_review_markers(option)
        # Partition is exhaustive and disjoint.
        assert len(primary) + len(technical) == len(option.review_markers)
        for m in technical:
            assert m.type == "empty_slab_placement_skipped"
        for m in primary:
            assert m.type != "empty_slab_placement_skipped"


# ---------------------------------------------------------------------------
# Report Markdown can be loaded for the UI preview
# ---------------------------------------------------------------------------


def test_report_markdown_is_readable(package):
    report = package.per_strategy_files["balanced"]["report"]
    text = report.read_text()
    assert text.startswith("# Marble Layout Report")
    assert "## Metrics" in text
