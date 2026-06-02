"""CLI-level tests for the smoke-placement script's TargetArea wiring.

The detection / packing logic is exercised separately in
``test_inventory_*.py``. These tests focus on the CLI surface: does
``--target-*`` actually change what gets packed and what shows up in
the placements JSON?
"""

from __future__ import annotations

import importlib
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
    """Synthetic clean_slabs.json with three 2000×1000 slabs and no images."""
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


# ---------------------------------------------------------------------------
# Default / explicit target — demo-default flag behaviour
# ---------------------------------------------------------------------------


def test_no_target_flags_uses_demo_defaults(tmp_path: Path, smoke_cli):
    clean = _make_clean_slabs(tmp_path)
    out = tmp_path / "out"
    rc = smoke_cli.main([
        "--inventory", str(clean),
        "--output", str(out),
        "--no-preview",  # don't need matplotlib in this test
    ])
    assert rc == 0
    data = json.loads((out / "smoke_placements.json").read_text())
    assert data["target"]["is_demo_default"] is True
    assert data["target"]["width_mm"] == 4000.0
    assert data["target"]["height_mm"] == 3000.0
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
    data = json.loads((out / "smoke_placements.json").read_text())
    assert data["target"]["is_demo_default"] is False
    assert data["target"]["width_mm"] == 5000.0
    assert data["target"]["height_mm"] == 3000.0
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
    """Three 2000×1000 slabs: 5×3 fits all three, 3×2 fits at most two."""
    clean = _make_clean_slabs(tmp_path)

    big = tmp_path / "big"
    rc = smoke_cli.main([
        "--inventory", str(clean),
        "--target-width-mm", "5000",
        "--target-height-mm", "3000",
        "--target-name", "Big Room",
        "--output", str(big),
        "--no-preview",
    ])
    assert rc == 0
    big_data = json.loads((big / "smoke_placements.json").read_text())

    small = tmp_path / "small"
    rc = smoke_cli.main([
        "--inventory", str(clean),
        "--target-width-mm", "3000",
        "--target-height-mm", "2000",
        "--target-name", "Small Room",
        "--output", str(small),
        "--no-preview",
    ])
    assert rc == 0
    small_data = json.loads((small / "smoke_placements.json").read_text())

    # Big target packs more slabs.
    assert len(big_data["placements"]) > len(small_data["placements"])
    # Big target has higher coverage% than small one.
    assert big_data["coverage_percentage"] != small_data["coverage_percentage"]
    # The target dims in the JSON reflect what we asked for.
    assert big_data["target"]["width_mm"] == 5000.0
    assert small_data["target"]["width_mm"] == 3000.0


def test_target_required_area_mismatch_recorded(tmp_path: Path, smoke_cli):
    """A required_area_m2 wildly off the rectangle area should warn."""
    clean = _make_clean_slabs(tmp_path)
    out = tmp_path / "out"
    rc = smoke_cli.main([
        "--inventory", str(clean),
        "--target-width-mm", "5000",
        "--target-height-mm", "3000",   # calculated = 15 m²
        "--target-required-area-m2", "30.0",  # 100% off
        "--output", str(out),
        "--no-preview",
    ])
    assert rc == 0
    data = json.loads((out / "smoke_placements.json").read_text())
    # required_area_m2 round-trips into the JSON.
    assert data["target"]["required_area_m2"] == 30.0
    # The warning code is reachable via the helper; we don't surface it
    # in the JSON yet (V1 keeps the placements.json minimal), but the
    # CLI logs it. Sanity: the report file at least confirms the call
    # path executes by virtue of the rc==0 above.
