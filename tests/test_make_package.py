"""One-command package workflow — `make_package.py`.

Drives `make_package.main(argv)` directly so the whole pipeline
(CAD intake → engine → per-strategy package) is exercised end to end.
"""
from pathlib import Path

import ezdxf
import pytest

from make_package import main

EXAMPLES_CAD = Path(__file__).resolve().parents[1] / "examples" / "cad_inputs"
DEMO_DXF = EXAMPLES_CAD / "demo" / "demo_floor_with_column.dxf"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _standard_dxf(target: Path, boundary, holes=()) -> Path:
    doc = ezdxf.new(dxfversion="R2013", setup=True)
    doc.units = ezdxf.units.MM
    doc.layers.add(name="AI_PROJECT_BOUNDARY", color=7)
    doc.layers.add(name="AI_HOLES_CUTOUTS", color=1)
    msp = doc.modelspace()
    msp.add_lwpolyline(boundary, close=True,
                       dxfattribs={"layer": "AI_PROJECT_BOUNDARY"})
    for hole in holes:
        msp.add_lwpolyline(hole, close=True,
                           dxfattribs={"layer": "AI_HOLES_CUTOUTS"})
    doc.saveas(target)
    return target


def _run(out_dir: Path, *, cad: Path = DEMO_DXF, extra: list[str] | None = None) -> int:
    argv = [
        "--cad", str(cad),
        "--project-id", "test_pkg_001",
        "--out", str(out_dir),
        "--strategies", "balanced", "lowest_waste",
        "--include-test-slabs", "--test-slab-count", "auto",
    ]
    if extra:
        argv += extra
    return main(argv)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_make_package_succeeds_on_standardized_dxf(tmp_path):
    out = tmp_path / "pkg"
    assert _run(out) == 0
    assert out.is_dir()


def test_make_package_writes_root_artifacts(tmp_path):
    out = tmp_path / "pkg"
    _run(out)
    assert (out / "cad_inspection.md").is_file()
    assert (out / "generated_engine_input.json").is_file()


def test_make_package_writes_per_strategy_subfolders(tmp_path):
    out = tmp_path / "pkg"
    _run(out)
    for strategy in ("balanced", "lowest_waste"):
        strat_dir = out / strategy
        assert strat_dir.is_dir(), f"missing {strategy}/ subfolder"
        assert (strat_dir / "layout.json").is_file()
        assert (strat_dir / "layout.dxf").is_file()
        assert (strat_dir / "layout_report.md").is_file()
        assert (strat_dir / "layout_report.pdf").is_file()
        assert (strat_dir / "preview.png").is_file()
        # PDF magic bytes — confirms reportlab actually wrote a PDF.
        assert (strat_dir / "layout_report.pdf").read_bytes().startswith(b"%PDF-")


def test_make_package_per_strategy_json_contains_only_that_option(tmp_path):
    import json

    out = tmp_path / "pkg"
    _run(out)
    bal = json.loads((out / "balanced" / "layout.json").read_text())
    low = json.loads((out / "lowest_waste" / "layout.json").read_text())
    assert [o["strategy"] for o in bal["layout_options"]] == ["balanced"]
    assert [o["strategy"] for o in low["layout_options"]] == ["lowest_waste"]


def test_no_preview_flag_skips_png(tmp_path):
    out = tmp_path / "pkg"
    _run(out, extra=["--no-preview"])
    assert not (out / "balanced" / "preview.png").exists()
    assert not (out / "lowest_waste" / "preview.png").exists()
    # The other artifacts are still written.
    assert (out / "balanced" / "layout.dxf").is_file()


def test_keep_intermediate_writes_internal_folder(tmp_path):
    out = tmp_path / "pkg"
    _run(out, extra=["--keep-intermediate"])
    assert (out / "internal" / "full_engine_output.json").is_file()


def test_internal_folder_absent_without_keep_intermediate(tmp_path):
    out = tmp_path / "pkg"
    _run(out)
    assert not (out / "internal").exists()


def test_clean_output_removes_stale_files(tmp_path):
    out = tmp_path / "pkg"
    out.mkdir()
    stale = out / "stale_file.txt"
    stale.write_text("left over from a previous run")
    _run(out, extra=["--clean-output"])
    assert not stale.exists()
    assert (out / "cad_inspection.md").is_file()


# ---------------------------------------------------------------------------
# Terminal summary
# ---------------------------------------------------------------------------


def test_terminal_summary_reports_key_metrics(tmp_path, capsys):
    out = tmp_path / "pkg"
    _run(out)
    summary = capsys.readouterr().out
    assert "Package created:" in summary
    assert "CAD intake:" in summary
    assert "project usable area:" in summary
    # Both strategies named, with their status + coverage lines.
    assert "balanced" in summary
    assert "lowest_waste" in summary
    assert "coverage_percentage:" in summary
    assert "layout_status:" in summary
    assert "seams:" in summary


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_missing_cad_file_gives_clear_error(tmp_path, capsys):
    out = tmp_path / "pkg"
    rc = main([
        "--cad", str(tmp_path / "does_not_exist.dxf"),
        "--project-id", "x", "--out", str(out),
        "--include-test-slabs",
    ])
    assert rc == 2
    assert "CAD file not found" in capsys.readouterr().err


def test_missing_boundary_layer_gives_clear_error(tmp_path, capsys):
    # A DXF whose AI_PROJECT_BOUNDARY layer has no entities.
    doc = ezdxf.new(dxfversion="R2013", setup=True)
    bad = tmp_path / "no_boundary.dxf"
    doc.saveas(bad)
    rc = main([
        "--cad", str(bad), "--project-id", "x", "--out", str(tmp_path / "pkg"),
        "--include-test-slabs",
    ])
    assert rc == 2
    assert "AI_PROJECT_BOUNDARY" in capsys.readouterr().err


def test_multiple_boundaries_gives_clear_error(tmp_path, capsys):
    bad = tmp_path / "two.dxf"
    doc = ezdxf.new(dxfversion="R2013", setup=True)
    doc.layers.add(name="AI_PROJECT_BOUNDARY", color=7)
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (1000, 0), (1000, 1000), (0, 1000)],
                       close=True, dxfattribs={"layer": "AI_PROJECT_BOUNDARY"})
    msp.add_lwpolyline([(2000, 0), (3000, 0), (3000, 1000), (2000, 1000)],
                       close=True, dxfattribs={"layer": "AI_PROJECT_BOUNDARY"})
    doc.saveas(bad)
    rc = main([
        "--cad", str(bad), "--project-id", "x", "--out", str(tmp_path / "pkg"),
        "--include-test-slabs",
    ])
    assert rc == 2
    assert "exactly one closed polyline" in capsys.readouterr().err


def test_missing_test_slabs_flag_gives_clear_error(tmp_path, capsys):
    rc = main([
        "--cad", str(DEMO_DXF), "--project-id", "x",
        "--out", str(tmp_path / "pkg"),
        # deliberately no --include-test-slabs
    ])
    assert rc == 2
    assert "slab inventory" in capsys.readouterr().err.lower()


# ---------------------------------------------------------------------------
# Coverage outcome sanity (the floor-with-column case)
# ---------------------------------------------------------------------------


def test_lowest_waste_reaches_full_coverage_on_column_floor(tmp_path):
    import json

    out = tmp_path / "pkg"
    _run(out)
    low = json.loads((out / "lowest_waste" / "layout.json").read_text())
    metrics = low["layout_options"][0]["metrics"]
    assert metrics["coverage_percentage"] == pytest.approx(100.0, abs=0.5)
    assert metrics["layout_status"] == "complete"
