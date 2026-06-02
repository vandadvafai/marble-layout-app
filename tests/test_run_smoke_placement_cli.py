"""CLI-level tests for the smoke-placement script's TargetArea wiring.

The detection / packing logic is exercised separately in
``test_inventory_*.py``. These tests focus on the CLI surface: does
``--target-*`` actually change what gets packed and what shows up in
the placement JSON?
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def smoke_cli():
    """Reload and return the smoke CLI module fresh per test."""
    spec = importlib.util.spec_from_file_location(
        "smoke_cli_under_test",
        PROJECT_ROOT / "scripts" / "run_smoke_placement.py",
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _make_clean_slabs(tmp_path: Path) -> Path:
    records = []
    for i in range(3):
        records.append({
            "slab_id": f"S{i}",
            "serial_number": f"2000100-{i}",
            "slab_number": str(i + 1),
            "item_code": "P-1",
            "image_id": None,
            "height_cm": 100, "width_cm": 200,
            "height_mm": 1000, "width_mm": 2000,
            "area_m2": 2.0, "calculated_area_m2": 2.0,
            "dimension_source": "explicit_excel",
            "image_path": None,
            "image_found": False,
            "image_match_method": "not_found",
            "source_excel_row": i + 2,
            "warnings": [],
        })
    payload = {
        "source_excel": str(tmp_path / "fake.xlsx"),
        "image_dir": str(tmp_path / "images"),
        "sheet_name": "Sheet1",
        "record_count": len(records),
        "warning_counts": {},
        "mapped_columns": {},
        "unmapped_columns": [],
        "records": records,
    }
    p = tmp_path / "clean_slabs.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def _load_placement(out: Path) -> dict:
    return json.loads((out / "placement.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Demo-default behaviour
# ---------------------------------------------------------------------------


def test_no_target_flags_uses_demo_defaults(tmp_path: Path, smoke_cli):
    clean = _make_clean_slabs(tmp_path)
    out = tmp_path / "out"
    rc = smoke_cli.main([
        "--inventory", str(clean),
        "--output", str(out),
        "--no-preview",
    ])
    assert rc == 0
    data = _load_placement(out)
    assert data["metadata"]["is_demo_default"] is True
    bbox = data["target"]["bbox"]
    assert bbox == [0.0, 0.0, 4000.0, 3000.0]
    assert data["target"]["name"].startswith("demo default")


def test_explicit_target_dimensions_disable_demo_flag(tmp_path: Path, smoke_cli):
    clean = _make_clean_slabs(tmp_path)
    out = tmp_path / "out"
    rc = smoke_cli.main([
        "--inventory", str(clean),
        "--target-width-mm", "5000",
        "--target-height-mm", "3000",
        "--target-name", "Test Room",
        "--output", str(out),
        "--no-preview",
    ])
    assert rc == 0
    data = _load_placement(out)
    assert data["metadata"]["is_demo_default"] is False
    assert data["target"]["bbox"] == [0.0, 0.0, 5000.0, 3000.0]
    assert data["target"]["name"] == "Test Room"


def test_one_dimension_without_the_other_is_an_error(tmp_path: Path, smoke_cli):
    clean = _make_clean_slabs(tmp_path)
    out = tmp_path / "out"
    with pytest.raises(SystemExit):
        smoke_cli.main([
            "--inventory", str(clean),
            "--target-width-mm", "5000",
            "--output", str(out),
            "--no-preview",
        ])


# ---------------------------------------------------------------------------
# Target dimensions actually drive packing
# ---------------------------------------------------------------------------


def test_5000x3000_target_packs_more_than_3000x2000(tmp_path: Path, smoke_cli):
    clean = _make_clean_slabs(tmp_path)

    big_out = tmp_path / "big"
    smoke_cli.main([
        "--inventory", str(clean),
        "--target-width-mm", "5000",
        "--target-height-mm", "3000",
        "--target-name", "Big Room",
        "--output", str(big_out),
        "--no-preview",
    ])
    big = _load_placement(big_out)

    small_out = tmp_path / "small"
    smoke_cli.main([
        "--inventory", str(clean),
        "--target-width-mm", "3000",
        "--target-height-mm", "2000",
        "--target-name", "Small Room",
        "--output", str(small_out),
        "--no-preview",
    ])
    small = _load_placement(small_out)

    assert len(big["placements"]) > len(small["placements"])
    assert big["derived"]["coverage_percentage"] != small["derived"]["coverage_percentage"]


def test_target_required_area_round_trip(tmp_path: Path, smoke_cli):
    clean = _make_clean_slabs(tmp_path)
    out = tmp_path / "out"
    rc = smoke_cli.main([
        "--inventory", str(clean),
        "--target-width-mm", "5000",
        "--target-height-mm", "3000",
        "--target-required-area-m2", "30.0",
        "--output", str(out),
        "--no-preview",
    ])
    assert rc == 0
    data = _load_placement(out)
    # 30 m² requested vs 15 m² calculated → mismatch warning expected.
    assert "required_area_mismatch" in data["metadata"]["target_warnings"]


# ---------------------------------------------------------------------------
# Preview files
# ---------------------------------------------------------------------------


def test_default_run_writes_geometric_and_textured_but_not_debug(tmp_path: Path, smoke_cli):
    clean = _make_clean_slabs(tmp_path)
    out = tmp_path / "out"
    smoke_cli.main([
        "--inventory", str(clean),
        "--target-width-mm", "5000",
        "--target-height-mm", "3000",
        "--output", str(out),
    ])
    assert (out / "preview_geometric.png").exists()
    assert (out / "preview_textured.png").exists()
    assert not (out / "preview_debug.png").exists()


def test_include_debug_writes_the_debug_png_too(tmp_path: Path, smoke_cli):
    clean = _make_clean_slabs(tmp_path)
    out = tmp_path / "out"
    smoke_cli.main([
        "--inventory", str(clean),
        "--target-width-mm", "5000",
        "--target-height-mm", "3000",
        "--include-debug",
        "--output", str(out),
    ])
    assert (out / "preview_debug.png").exists()


def test_no_preview_writes_only_json(tmp_path: Path, smoke_cli):
    clean = _make_clean_slabs(tmp_path)
    out = tmp_path / "out"
    smoke_cli.main([
        "--inventory", str(clean),
        "--target-width-mm", "5000",
        "--target-height-mm", "3000",
        "--output", str(out),
        "--no-preview",
    ])
    assert (out / "placement.json").exists()
    assert not (out / "preview_geometric.png").exists()
    assert not (out / "preview_textured.png").exists()
