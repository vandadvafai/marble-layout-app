"""DXF validation suite runner."""
from pathlib import Path

import ezdxf
import pytest

from placement_engine.utils.test_inventory import SlabInventorySpec
from run_dxf_validation_suite import SuiteConfig, run_suite


def _write_standard_dxf(target: Path, boundary, holes=()) -> Path:
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


@pytest.fixture
def one_dxf_dir(tmp_path):
    """A cad-dir holding a single plain 6 m × 4 m rectangle DXF."""
    cad_dir = tmp_path / "cad"
    cad_dir.mkdir()
    _write_standard_dxf(
        cad_dir / "rect.dxf",
        [(0, 0), (6000, 0), (6000, 4000), (0, 4000)],
    )
    return cad_dir


def _default_cfg(cad_dir: Path, out_dir: Path) -> SuiteConfig:
    return SuiteConfig(
        cad_dir=cad_dir,
        out_dir=out_dir,
        strategies=["balanced", "lowest_waste"],
        slab_spec=SlabInventorySpec(count="auto"),
        render_preview=False,
    )


def test_suite_runs_on_a_single_dxf(one_dxf_dir, tmp_path):
    out_dir = tmp_path / "out"
    rows, summary_path = run_suite(_default_cfg(one_dxf_dir, out_dir))
    # One DXF × two strategies → two rows.
    assert len(rows) == 2
    assert {r.strategy for r in rows} == {"balanced", "lowest_waste"}


def test_suite_creates_expected_output_files(one_dxf_dir, tmp_path):
    out_dir = tmp_path / "out"
    run_suite(_default_cfg(one_dxf_dir, out_dir))

    case_dir = out_dir / "rect"
    assert (case_dir / "cad_inspection.md").exists()
    assert (case_dir / "input_generated.json").exists()
    for strategy in ("balanced", "lowest_waste"):
        strat_dir = case_dir / strategy
        assert (strat_dir / "layout.json").exists()
        assert (strat_dir / "layout.dxf").exists()
        assert (strat_dir / "layout_report.md").exists()


def test_suite_writes_validation_summary(one_dxf_dir, tmp_path):
    out_dir = tmp_path / "out"
    _, summary_path = run_suite(_default_cfg(one_dxf_dir, out_dir))
    assert summary_path == out_dir / "validation_summary.md"
    body = summary_path.read_text()
    assert "# DXF Validation Suite — Summary" in body
    assert "rect.dxf" in body
    assert "## Results" in body
    assert "## Per-case detail" in body


def test_plain_rectangle_passes_both_strategies(one_dxf_dir, tmp_path):
    """A plain rectangle fully tiles with both strategies — both PASS."""
    out_dir = tmp_path / "out"
    rows, _ = run_suite(_default_cfg(one_dxf_dir, out_dir))
    for r in rows:
        assert r.passed, f"{r.strategy} unexpectedly failed: {r.notes}"
        assert r.coverage_percentage == pytest.approx(100.0, abs=0.5)
        assert r.layout_status == "complete"
        assert r.inventory_status == "sufficient"


def test_suite_records_pass_fail_with_reasons(tmp_path):
    """An L-shape: balanced (row-based) falls short, lowest_waste reaches
    100 %. The suite must record the per-strategy outcome and a reason."""
    cad_dir = tmp_path / "cad"
    cad_dir.mkdir()
    _write_standard_dxf(
        cad_dir / "lshape.dxf",
        [(0, 0), (8000, 0), (8000, 4000), (4800, 4000),
         (4800, 2600), (0, 2600)],
    )
    out_dir = tmp_path / "out"
    rows, _ = run_suite(_default_cfg(cad_dir, out_dir))
    by_strategy = {r.strategy: r for r in rows}

    assert by_strategy["lowest_waste"].passed
    # balanced may or may not pass depending on slab count, but if it
    # fails the note must explain why (non-empty, FAIL-prefixed).
    bal = by_strategy["balanced"]
    if not bal.passed:
        assert bal.notes.startswith("FAIL:")


def test_suite_raises_on_empty_cad_dir(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(SystemExit, match="No .dxf files"):
        run_suite(_default_cfg(empty, tmp_path / "out"))
