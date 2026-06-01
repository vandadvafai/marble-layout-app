"""Tests for `placement_engine.inventory` — loader + validation + adapter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from placement_engine.inventory import (
    Inventory,
    load_inventory,
    validate_inventory,
)
from placement_engine.inventory.model import InventorySlab


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_clean_slabs(
    tmp_path: Path,
    records: list[dict],
    *,
    name: str = "clean_slabs.json",
) -> Path:
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
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _record(**overrides) -> dict:
    base = {
        "slab_id": "S001",
        "serial_number": "1202002",
        "item_code": "P-1",
        "image_id": "1202002",
        "height_cm": 120,
        "width_cm": 200,
        "height_mm": 1200,
        "width_mm": 2000,
        "area_m2": 2.4,
        "calculated_area_m2": 2.4,
        "image_path": None,
        "image_found": False,
        "source_excel_row": 2,
        "warnings": [],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# load_inventory
# ---------------------------------------------------------------------------


def test_load_inventory_basic(tmp_path: Path):
    img = tmp_path / "slab_S001.jpg"
    img.write_bytes(b"")
    json_path = _make_clean_slabs(
        tmp_path,
        [_record(image_path=str(img))],
    )
    inv = load_inventory(json_path)
    assert isinstance(inv, Inventory)
    assert len(inv) == 1
    s = inv.slabs[0]
    assert s.slab_id == "S001"
    assert s.serial_number == "1202002"
    assert s.width_mm == 2000.0 and s.height_mm == 1200.0
    assert s.area_m2 == pytest.approx(2.4)
    assert s.image_path == img
    assert s.image_available is True
    assert s.image_placeholder_reason is None
    assert s.ingestion_warnings == []
    assert s.source_excel_row == 2


def test_load_inventory_missing_image_is_flagged_but_loads(tmp_path: Path):
    """image_path set but file does not exist: row keeps loading, just flagged."""
    ghost = tmp_path / "does_not_exist.jpg"
    json_path = _make_clean_slabs(
        tmp_path,
        [_record(image_path=str(ghost))],
    )
    inv = load_inventory(json_path)
    s = inv.slabs[0]
    assert s.image_available is False
    assert s.image_placeholder_reason is not None
    assert "image_file_missing" in s.image_placeholder_reason


def test_load_inventory_no_image_path_uses_placeholder(tmp_path: Path):
    json_path = _make_clean_slabs(
        tmp_path,
        [_record(image_path=None, image_found=False, warnings=["image_not_found"])],
    )
    inv = load_inventory(json_path)
    s = inv.slabs[0]
    assert s.image_path is None
    assert s.image_available is False
    assert s.image_placeholder_reason == "no_image_path_in_clean_slabs"
    assert "image_not_found" in s.ingestion_warnings


def test_load_inventory_skips_records_without_dimensions(tmp_path: Path):
    json_path = _make_clean_slabs(
        tmp_path,
        [
            _record(slab_id="ok", width_mm=2000, height_mm=1200),
            _record(slab_id="bad-w", width_mm=None, height_mm=1200),
            _record(slab_id="bad-h", width_mm=2000, height_mm=None),
            _record(slab_id="zero", width_mm=0, height_mm=1200),
            _record(slab_id="neg", width_mm=-5, height_mm=1200),
        ],
    )
    inv = load_inventory(json_path)
    assert [s.slab_id for s in inv.slabs] == ["ok"]
    assert len(inv.skipped_records) == 4


def test_load_inventory_resolves_relative_image_path_against_json_dir(tmp_path: Path):
    """A relative image_path is resolved against the JSON file's directory
    so the inventory can be moved together with its image folder."""
    nested = tmp_path / "subdir"
    nested.mkdir()
    img = nested / "images" / "slab.jpg"
    img.parent.mkdir()
    img.write_bytes(b"")
    json_path = _make_clean_slabs(
        nested,
        [_record(image_path="images/slab.jpg")],
    )
    inv = load_inventory(json_path)
    s = inv.slabs[0]
    assert s.image_available is True
    assert s.image_path is not None
    assert s.image_path.exists()


def test_load_inventory_missing_records_key_raises(tmp_path: Path):
    bad = tmp_path / "broken.json"
    bad.write_text(json.dumps({"not_records": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="records"):
        load_inventory(bad)


def test_load_inventory_file_not_found(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_inventory(tmp_path / "nope.json")


# ---------------------------------------------------------------------------
# InventorySlab.to_engine_slab adapter
# ---------------------------------------------------------------------------


def test_to_engine_slab_passes_dimensions_and_defaults_thickness(tmp_path: Path):
    img = tmp_path / "img.jpg"
    img.write_bytes(b"")
    s = InventorySlab(
        slab_id="S1",
        serial_number=None,
        slab_number=None,
        item_code=None,
        width_mm=2000.0,
        height_mm=1200.0,
        area_m2=2.4,
        calculated_area_m2=2.4,
        image_path=img,
        image_available=True,
        image_placeholder_reason=None,
        source_excel_row=None,
    )
    eng = s.to_engine_slab()
    assert eng.slab_id == "S1"
    assert eng.width == 2000.0 and eng.height == 1200.0
    # Default thickness (V1 doesn't carry thickness).
    assert eng.thickness == 20.0
    assert eng.image_path == str(img)


def test_to_engine_slab_omits_unavailable_image_path():
    s = InventorySlab(
        slab_id="S1",
        serial_number=None,
        slab_number=None,
        item_code=None,
        width_mm=2000.0,
        height_mm=1200.0,
        area_m2=None,
        calculated_area_m2=None,
        image_path=Path("/nonexistent.jpg"),
        image_available=False,
        image_placeholder_reason="image_file_missing",
        source_excel_row=None,
    )
    eng = s.to_engine_slab()
    assert eng.image_path is None


def test_to_engine_slab_custom_thickness():
    s = InventorySlab(
        slab_id="S1",
        serial_number=None,
        slab_number=None,
        item_code=None,
        width_mm=2000.0,
        height_mm=1200.0,
        area_m2=None,
        calculated_area_m2=None,
        image_path=None,
        image_available=False,
        image_placeholder_reason=None,
        source_excel_row=None,
    )
    eng = s.to_engine_slab(default_thickness_mm=30.0)
    assert eng.thickness == 30.0


# ---------------------------------------------------------------------------
# validate_inventory
# ---------------------------------------------------------------------------


def test_validate_inventory_clean_inventory_no_issues(tmp_path: Path):
    img = tmp_path / "img.jpg"
    img.write_bytes(b"")
    json_path = _make_clean_slabs(
        tmp_path,
        [_record(image_path=str(img))],
    )
    issues = validate_inventory(load_inventory(json_path))
    assert issues == []


def test_validate_inventory_flags_missing_image_file(tmp_path: Path):
    json_path = _make_clean_slabs(
        tmp_path,
        [_record(image_path=str(tmp_path / "ghost.jpg"))],
    )
    issues = validate_inventory(load_inventory(json_path))
    codes = [i.code for i in issues]
    assert "image_file_missing" in codes


def test_validate_inventory_flags_unset_image_path(tmp_path: Path):
    json_path = _make_clean_slabs(
        tmp_path,
        [_record(image_path=None)],
    )
    issues = validate_inventory(load_inventory(json_path))
    codes = [i.code for i in issues]
    assert "image_path_unset" in codes


def test_validate_inventory_flags_area_mismatch(tmp_path: Path):
    img = tmp_path / "img.jpg"
    img.write_bytes(b"")
    json_path = _make_clean_slabs(
        tmp_path,
        [_record(image_path=str(img), area_m2=9.9, calculated_area_m2=2.4)],
    )
    issues = validate_inventory(load_inventory(json_path))
    codes = [i.code for i in issues]
    assert "area_mismatch" in codes


def test_validate_inventory_tolerates_small_area_difference(tmp_path: Path):
    img = tmp_path / "img.jpg"
    img.write_bytes(b"")
    # Within 5% — should be tolerated.
    json_path = _make_clean_slabs(
        tmp_path,
        [_record(image_path=str(img), area_m2=2.42, calculated_area_m2=2.4)],
    )
    issues = validate_inventory(load_inventory(json_path))
    assert all(i.code != "area_mismatch" for i in issues)
