"""Tests for `placement_engine.inventory.processed_images` — wiring
image_intake's image_metadata.json onto an existing Inventory.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from placement_engine.inventory import (
    attach_processed_images,
    load_inventory,
    load_processed_image_metadata,
)
from placement_engine.target_area import TargetArea


# ---------------------------------------------------------------------------
# helpers (mirrors test_inventory_loader.py's fixtures)
# ---------------------------------------------------------------------------


def _write_clean_slabs(tmp_path: Path, records: list[dict]) -> Path:
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
    path = tmp_path / "clean_slabs.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _record(
    slab_id: str,
    image_path: Path,
    *,
    slab_number: str = "1",
) -> dict:
    return {
        "slab_id": slab_id,
        "serial_number": slab_id,
        "slab_number": slab_number,
        "item_code": "P-1",
        "image_id": image_path.stem,
        "height_cm": 159, "width_cm": 159,
        "height_mm": 1590, "width_mm": 1590,
        "area_m2": 2.528, "calculated_area_m2": 2.5281,
        "dimension_source": "explicit_excel",
        "image_path": str(image_path),
        "image_found": True,
        "image_match_method": "slab_number_suffix",
        "source_excel_row": 2,
        "warnings": [],
    }


def _write_image_metadata(
    tmp_path: Path,
    entries: list[dict],
) -> Path:
    payload = {
        "inventory_source": str(tmp_path / "clean_slabs.json"),
        "output_dir": str(tmp_path / "image_intake"),
        "total_images": len(entries),
        "detected_count": sum(1 for e in entries if e.get("green_box_detected")),
        "warning_counts": {},
        "images": entries,
    }
    path = tmp_path / "image_metadata.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# load_processed_image_metadata
# ---------------------------------------------------------------------------


def test_load_processed_image_metadata_returns_slab_id_to_path(tmp_path: Path):
    processed = tmp_path / "5538-6545-1.jpg"
    processed.write_bytes(b"")
    meta_path = _write_image_metadata(
        tmp_path,
        [
            {
                "slab_id": "S001",
                "slab_number": "1",
                "original_image_path": str(tmp_path / "orig.jpg"),
                "processed_image_path": str(processed),
                "green_box_detected": True,
                "crop_x": 0, "crop_y": 0,
                "crop_width": 100, "crop_height": 100,
                "confidence_score": 1.0,
                "warnings": [],
            }
        ],
    )
    table = load_processed_image_metadata(meta_path)
    assert table == {"S001": processed}


def test_load_processed_image_metadata_skips_undetected_entries(tmp_path: Path):
    meta_path = _write_image_metadata(
        tmp_path,
        [
            {
                "slab_id": "S001",
                "slab_number": "1",
                "original_image_path": "/tmp/orig.jpg",
                "processed_image_path": "/tmp/orig.jpg",  # falls back to original
                "green_box_detected": False,
                "crop_x": None, "crop_y": None,
                "crop_width": None, "crop_height": None,
                "confidence_score": None,
                "warnings": ["green_box_not_found"],
            }
        ],
    )
    assert load_processed_image_metadata(meta_path) == {}


def test_load_processed_image_metadata_file_not_found(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_processed_image_metadata(tmp_path / "ghost.json")


# ---------------------------------------------------------------------------
# attach_processed_images — happy path
# ---------------------------------------------------------------------------


def test_attach_processed_images_sets_field_on_matching_slabs(tmp_path: Path):
    orig = tmp_path / "5538-6545-1.jpg"
    orig.write_bytes(b"")
    processed = tmp_path / "processed_5538-6545-1.jpg"
    processed.write_bytes(b"")

    clean_slabs = _write_clean_slabs(
        tmp_path, [_record("S001", orig, slab_number="1")]
    )
    meta_path = _write_image_metadata(
        tmp_path,
        [
            {
                "slab_id": "S001",
                "slab_number": "1",
                "original_image_path": str(orig),
                "processed_image_path": str(processed),
                "green_box_detected": True,
                "crop_x": 0, "crop_y": 0,
                "crop_width": 100, "crop_height": 100,
                "confidence_score": 1.0,
                "warnings": [],
            }
        ],
    )

    inv = load_inventory(clean_slabs)
    assert inv.slabs[0].processed_image_path is None  # before attach

    attached = attach_processed_images(inv, meta_path)
    assert attached == 1
    assert inv.slabs[0].processed_image_path == processed


def test_attach_processed_images_falls_back_to_slab_number_when_slab_id_misses(
    tmp_path: Path,
):
    """When slab_id doesn't match anything, fall back to matching by slab_number."""
    orig = tmp_path / "5538-6545-1.jpg"
    orig.write_bytes(b"")
    processed = tmp_path / "processed_5538-6545-1.jpg"
    processed.write_bytes(b"")

    clean_slabs = _write_clean_slabs(
        tmp_path, [_record("INVENTORY_ID", orig, slab_number="7")]
    )
    meta_path = _write_image_metadata(
        tmp_path,
        [
            {
                "slab_id": "DIFFERENT_ID",  # doesn't match the inventory's slab_id
                "slab_number": "7",  # but slab_number does
                "original_image_path": str(orig),
                "processed_image_path": str(processed),
                "green_box_detected": True,
                "crop_x": 0, "crop_y": 0,
                "crop_width": 100, "crop_height": 100,
                "confidence_score": 1.0,
                "warnings": [],
            }
        ],
    )

    inv = load_inventory(clean_slabs)
    attached = attach_processed_images(inv, meta_path)
    assert attached == 1
    assert inv.slabs[0].processed_image_path == processed


def test_attach_processed_images_skips_when_processed_file_missing(tmp_path: Path):
    """A metadata record pointing at a non-existent processed file is ignored."""
    orig = tmp_path / "5538-6545-1.jpg"
    orig.write_bytes(b"")
    ghost_processed = tmp_path / "does_not_exist.jpg"  # never written

    clean_slabs = _write_clean_slabs(tmp_path, [_record("S001", orig)])
    meta_path = _write_image_metadata(
        tmp_path,
        [
            {
                "slab_id": "S001",
                "slab_number": "1",
                "original_image_path": str(orig),
                "processed_image_path": str(ghost_processed),
                "green_box_detected": True,
                "crop_x": 0, "crop_y": 0,
                "crop_width": 100, "crop_height": 100,
                "confidence_score": 1.0,
                "warnings": [],
            }
        ],
    )
    inv = load_inventory(clean_slabs)
    attached = attach_processed_images(inv, meta_path)
    assert attached == 0
    assert inv.slabs[0].processed_image_path is None


def test_attach_processed_images_skips_undetected_records(tmp_path: Path):
    """green_box_detected=False entries must NOT attach a processed_image_path."""
    orig = tmp_path / "5538-6545-1.jpg"
    orig.write_bytes(b"")

    clean_slabs = _write_clean_slabs(tmp_path, [_record("S001", orig)])
    meta_path = _write_image_metadata(
        tmp_path,
        [
            {
                "slab_id": "S001",
                "slab_number": "1",
                "original_image_path": str(orig),
                "processed_image_path": str(orig),  # processor falls back to orig
                "green_box_detected": False,
                "crop_x": None, "crop_y": None,
                "crop_width": None, "crop_height": None,
                "confidence_score": None,
                "warnings": ["green_box_not_found"],
            }
        ],
    )
    inv = load_inventory(clean_slabs)
    attached = attach_processed_images(inv, meta_path)
    assert attached == 0
    assert inv.slabs[0].processed_image_path is None


# ---------------------------------------------------------------------------
# shelf_pack propagation
# ---------------------------------------------------------------------------


def test_placement_carries_processed_image_path(tmp_path: Path):
    """When the inventory has processed_image_path set, the Placement
    produced by shelf_pack must carry it through."""
    from placement_engine.inventory.shelf_pack import shelf_pack

    orig = tmp_path / "orig.jpg"
    orig.write_bytes(b"")
    processed = tmp_path / "processed.jpg"
    processed.write_bytes(b"")

    clean_slabs = _write_clean_slabs(tmp_path, [_record("S001", orig)])
    meta_path = _write_image_metadata(
        tmp_path,
        [
            {
                "slab_id": "S001",
                "slab_number": "1",
                "original_image_path": str(orig),
                "processed_image_path": str(processed),
                "green_box_detected": True,
                "crop_x": 0, "crop_y": 0,
                "crop_width": 100, "crop_height": 100,
                "confidence_score": 1.0,
                "warnings": [],
            }
        ],
    )
    inv = load_inventory(clean_slabs)
    attach_processed_images(inv, meta_path)
    target = TargetArea(target_id="t", name="t", width_mm=4000, height_mm=3000)
    result = shelf_pack(inv.slabs, target)
    assert result.placements[0].processed_image_path == processed


def test_loader_extracts_slab_number_for_attach_matching(tmp_path: Path):
    """The loader now reads slab_number from clean_slabs.json so the
    slab_number fallback match works."""
    orig = tmp_path / "orig.jpg"
    orig.write_bytes(b"")
    clean_slabs = _write_clean_slabs(
        tmp_path, [_record("S001", orig, slab_number="42")]
    )
    inv = load_inventory(clean_slabs)
    assert inv.slabs[0].slab_number == "42"
