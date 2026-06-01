"""Tests for `placement_engine.image_intake` — detection + batch driver."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from placement_engine.image_intake import (
    crop_inside_green_box,
    detect_green_box,
    process_inventory,
    write_outputs,
)


# ---------------------------------------------------------------------------
# synthetic image helpers
# ---------------------------------------------------------------------------


# Pure green in BGR (OpenCV native channel order).
BGR_GREEN = (0, 255, 0)
BGR_RED = (0, 0, 255)
BGR_WHITE = (255, 255, 255)


def _make_slab_image(
    width: int = 1000,
    height: int = 800,
    box: tuple[int, int, int, int] | None = (100, 80, 800, 640),
    line_thickness: int = 4,
    with_red_outer: bool = False,
) -> np.ndarray:
    """White background with an optional green rectangle outline.

    ``box`` is ``(x, y, w, h)`` of the green rectangle. ``None`` means
    no green — used for the negative test.
    """
    img = np.full((height, width, 3), 255, dtype=np.uint8)  # white
    if with_red_outer and box is not None:
        x, y, w, h = box
        # Slightly bigger red box around the green one — looks like the
        # real slab images where the red box surrounds the green.
        cv2.rectangle(
            img,
            (max(0, x - 30), max(0, y - 30)),
            (min(width - 1, x + w + 30), min(height - 1, y + h + 30)),
            BGR_RED,
            line_thickness,
        )
    if box is not None:
        x, y, w, h = box
        cv2.rectangle(img, (x, y), (x + w, y + h), BGR_GREEN, line_thickness)
    return img


# ---------------------------------------------------------------------------
# detect_green_box
# ---------------------------------------------------------------------------


def test_detect_green_box_finds_rectangle_in_synthetic_image():
    img = _make_slab_image(box=(100, 80, 800, 640))
    box = detect_green_box(img)
    assert box is not None
    # Tolerance: the morphological close can grow the bbox by a few px.
    assert abs(box.x - 100) <= 4
    assert abs(box.y - 80) <= 4
    assert abs(box.width - 800) <= 8
    assert abs(box.height - 640) <= 8
    # Two thin rectangle sides → confidence should be solid.
    assert box.confidence > 0.5


def test_detect_green_box_ignores_red_outer_rectangle():
    """Red rectangle around green must not change the detected bbox."""
    img = _make_slab_image(box=(100, 80, 800, 640), with_red_outer=True)
    box = detect_green_box(img)
    assert box is not None
    # Still the *green* box, not the slightly-bigger red one.
    assert abs(box.x - 100) <= 4
    assert abs(box.width - 800) <= 8


def test_detect_green_box_returns_none_for_pure_white_image():
    img = np.full((500, 500, 3), 255, dtype=np.uint8)
    assert detect_green_box(img) is None


def test_detect_green_box_returns_none_for_red_only_image():
    img = _make_slab_image(box=None)
    cv2.rectangle(img, (50, 50), (450, 450), BGR_RED, 5)
    assert detect_green_box(img) is None


def test_detect_green_box_rejects_tiny_specks():
    """A few stray green pixels must NOT be promoted to a 'rectangle'."""
    img = np.full((500, 500, 3), 255, dtype=np.uint8)
    # Two green specks far apart — their bbox would span the image but
    # the area-fraction guard should reject it... wait, area would span
    # the whole image. So use the perimeter coverage guard instead by
    # making both specks tiny solid blobs, not a perimeter.
    cv2.circle(img, (50, 50), 3, BGR_GREEN, -1)
    cv2.circle(img, (450, 450), 3, BGR_GREEN, -1)
    box = detect_green_box(img)
    # Each speck is its own component → the largest bbox has area ~ 36
    # px², way below the 5% guard → None.
    assert box is None


def test_detect_green_box_handles_empty_array():
    assert detect_green_box(np.empty((0, 0, 3), dtype=np.uint8)) is None


# ---------------------------------------------------------------------------
# crop_inside_green_box
# ---------------------------------------------------------------------------


def test_crop_strips_the_green_line():
    img = _make_slab_image(box=(100, 80, 800, 640), line_thickness=4)
    box = detect_green_box(img)
    assert box is not None
    cropped = crop_inside_green_box(img, box)
    # Cropped region must be smaller than the bbox on both axes.
    assert cropped.shape[0] < box.height
    assert cropped.shape[1] < box.width
    # The line was bright green; the interior is pure white. Verify
    # there is essentially no green left in the cropped image.
    hsv = cv2.cvtColor(cropped, cv2.COLOR_BGR2HSV)
    green_pixels = cv2.inRange(hsv, np.array((40, 80, 80)), np.array((85, 255, 255)))
    green_fraction = green_pixels.sum() / 255 / green_pixels.size
    assert green_fraction < 0.001, (
        f"crop still contains green pixels: {green_fraction:.4%}"
    )


def test_crop_inset_respects_image_bounds(tmp_path: Path):
    """A box flush against the image edge must not produce negative coords."""
    img = _make_slab_image(box=(0, 0, 500, 400), line_thickness=2)
    box = detect_green_box(img)
    assert box is not None
    cropped = crop_inside_green_box(img, box, inset_min_px=10)
    assert cropped.shape[0] > 0 and cropped.shape[1] > 0


# ---------------------------------------------------------------------------
# end-to-end process_inventory
# ---------------------------------------------------------------------------


def _write_inventory_json(
    tmp_path: Path,
    records: list[dict],
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
    path = tmp_path / "clean_slabs.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _record(
    slab_id: str,
    image_path: Path | None,
    slab_number: str | None = "1",
) -> dict:
    return {
        "slab_id": slab_id,
        "serial_number": slab_id,
        "slab_number": slab_number,
        "item_code": "P-1",
        "image_id": image_path.stem if image_path else None,
        "height_cm": 159, "width_cm": 159,
        "height_mm": 1590, "width_mm": 1590,
        "area_m2": 2.528, "calculated_area_m2": 2.5281,
        "dimension_source": "explicit_excel",
        "image_path": str(image_path) if image_path else None,
        "image_found": image_path is not None,
        "image_match_method": "slab_number_suffix",
        "source_excel_row": 2,
        "warnings": [],
    }


def test_process_inventory_crops_detected_images_and_writes_metadata(tmp_path: Path):
    images_dir = tmp_path / "images"
    images_dir.mkdir()

    # Two slabs with detectable green boxes, one with no green.
    slab1_img = images_dir / "5538-6545-1.jpg"
    cv2.imwrite(str(slab1_img), _make_slab_image(box=(100, 80, 800, 640)))
    slab2_img = images_dir / "5538-6545-2.jpg"
    cv2.imwrite(str(slab2_img), _make_slab_image(box=(50, 50, 600, 500)))
    slab3_img = images_dir / "5538-6545-3.jpg"
    cv2.imwrite(str(slab3_img), _make_slab_image(box=None))  # pure white

    json_path = _write_inventory_json(
        tmp_path,
        [
            _record("S001", slab1_img, slab_number="1"),
            _record("S002", slab2_img, slab_number="2"),
            _record("S003", slab3_img, slab_number="3"),
        ],
    )

    out_dir = tmp_path / "out"
    result = process_inventory(json_path, out_dir)
    paths = write_outputs(result)

    assert len(result.images) == 3
    assert result.detected_count == 2

    by_id = {m.slab_id: m for m in result.images}
    # Detected rows have crop coords and a cropped file on disk.
    assert by_id["S001"].green_box_detected is True
    assert by_id["S001"].crop_x is not None
    assert Path(by_id["S001"].processed_image_path).exists()
    assert by_id["S001"].slab_number == "1"
    # Undetected row preserves the original image_path.
    assert by_id["S003"].green_box_detected is False
    assert "green_box_not_found" in by_id["S003"].warnings
    assert by_id["S003"].processed_image_path == str(slab3_img)

    # Metadata + report files exist.
    assert paths["metadata"].exists()
    assert paths["report"].exists()
    meta = json.loads(paths["metadata"].read_text(encoding="utf-8"))
    assert meta["total_images"] == 3
    assert meta["detected_count"] == 2
    assert {img["slab_id"] for img in meta["images"]} == {"S001", "S002", "S003"}


def test_process_inventory_keeps_slab_id_image_mapping(tmp_path: Path):
    """Each metadata row must point back to its slab_id (no shuffling)."""
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    img_a = images_dir / "5538-6545-1.jpg"
    img_b = images_dir / "5538-6545-2.jpg"
    cv2.imwrite(str(img_a), _make_slab_image(box=(100, 80, 800, 640)))
    cv2.imwrite(str(img_b), _make_slab_image(box=(50, 50, 600, 500)))

    json_path = _write_inventory_json(
        tmp_path,
        [
            _record("SLAB_ALPHA", img_a, slab_number="1"),
            _record("SLAB_BETA", img_b, slab_number="2"),
        ],
    )

    result = process_inventory(json_path, tmp_path / "out")
    by_id = {m.slab_id: m for m in result.images}
    assert by_id["SLAB_ALPHA"].original_image_path == str(img_a)
    assert by_id["SLAB_BETA"].original_image_path == str(img_b)
    # Filenames preserve the image stems → no cross-wiring.
    assert "5538-6545-1" in by_id["SLAB_ALPHA"].processed_image_path
    assert "5538-6545-2" in by_id["SLAB_BETA"].processed_image_path


def test_process_inventory_handles_unreadable_image(tmp_path: Path):
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    bad_img = images_dir / "broken.jpg"
    bad_img.write_bytes(b"this is not a jpeg")
    json_path = _write_inventory_json(tmp_path, [_record("S001", bad_img)])

    result = process_inventory(json_path, tmp_path / "out")
    m = result.images[0]
    assert m.green_box_detected is False
    assert "image_unreadable" in m.warnings


def test_process_inventory_handles_image_path_set_but_missing(tmp_path: Path):
    """clean_slabs.json said image_found=true but the file is gone now."""
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    ghost = images_dir / "missing.jpg"  # never created
    rec = _record("S001", ghost)
    # The loader resolves image_path and notices the file is missing,
    # marking image_available=False internally — but the input
    # clean_slabs.json may still say image_found=true. Either way the
    # processor must not crash.
    json_path = _write_inventory_json(tmp_path, [rec])

    result = process_inventory(json_path, tmp_path / "out")
    assert len(result.images) == 1
    m = result.images[0]
    assert m.green_box_detected is False
    assert m.warnings  # something was flagged
