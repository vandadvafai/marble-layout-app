"""Unit tests for the slab calibration & standardization module.

Focus on the pure geometry / policy rules so we can regress:
  * 20 mm per side deduction (exactly once)
  * 5 mm inter-piece spacing constant
  * aspect-ratio classifier thresholds
  * corner detection on a synthetic slab image
  * perspective correction produces the requested dimensions
  * missing photo → missing_photo status
  * missing Excel dimensions → rejected status
  * calibration record round-trips through JSON
  * manual corner override flips a record to approved
  * green-boundary image auto-approves
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from placement_engine.calibration import (
    AUTO_APPROVE_ASPECT_LIMIT,
    CalibrationRecord,
    CalibrationStatus,
    CropRectangle,
    EDGE_DEDUCTION_MM,
    EDGE_DEDUCTION_TOTAL_MM,
    FACTORY_POLICY_VERSION,
    INTER_PIECE_SPACING_MM,
    NEEDS_REVIEW_ASPECT_LIMIT,
    SlabCalibrationInput,
    SlabCorners,
    SourceType,
    apply_manual_corners,
    calibrate_batch,
    calibrate_slab,
    classify_aspect_agreement,
    count_by_status,
    migrate_legacy_green_box_records,
    usable_dimensions_mm,
)
from placement_engine.calibration.corners import (
    detect_slab_corners, rectify_to_dims,
)


# ---------------------------------------------------------------------------
# policy constants
# ---------------------------------------------------------------------------


def test_policy_constants_pin_v1_rules():
    assert FACTORY_POLICY_VERSION == "1.0"
    assert EDGE_DEDUCTION_MM == 20.0
    assert EDGE_DEDUCTION_TOTAL_MM == 40.0
    assert INTER_PIECE_SPACING_MM == 5.0
    assert AUTO_APPROVE_ASPECT_LIMIT == 0.02
    assert NEEDS_REVIEW_ASPECT_LIMIT == 0.08


def test_usable_dimensions_subtracts_40_per_axis():
    assert usable_dimensions_mm(1610, 2200) == (1570.0, 2160.0)
    assert usable_dimensions_mm(160, 200) == (120.0, 160.0)


def test_usable_dimensions_returns_non_positive_for_tiny_slabs():
    # Not the pipeline's job to clamp — callers detect the
    # non-positive value and mark the record REJECTED.
    w, h = usable_dimensions_mm(30, 40)
    assert w < 0
    assert h <= 0


# ---------------------------------------------------------------------------
# aspect classifier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("delta_pct, expected", [
    (0.5, "auto"),
    (1.9, "auto"),
    (2.5, "review"),
    (7.5, "review"),
    (8.5, "reject"),
    (25.0, "reject"),
])
def test_aspect_agreement_bucket_thresholds(delta_pct, expected):
    excel = 1.5
    image = excel * (1.0 + delta_pct / 100.0)
    bucket, _ = classify_aspect_agreement(image, excel)
    assert bucket == expected


def test_aspect_agreement_rejects_when_excel_zero():
    bucket, _ = classify_aspect_agreement(1.0, 0.0)
    assert bucket == "reject"


# ---------------------------------------------------------------------------
# corner detection + perspective correction
# ---------------------------------------------------------------------------


def _synthetic_slab(
    tmp_path: Path, filename: str = "slab.jpg",
    *, size: int = 512, margin: int = 60,
    slab_color: tuple[int, int, int] = (215, 190, 155),
    background: tuple[int, int, int] = (30, 30, 30),
) -> Path:
    """Draw a synthetic image with a dark background and a bright
    rectangular slab in the centre. Returns the on-disk path."""
    img = np.zeros((size, size, 3), dtype=np.uint8)
    img[:, :] = background
    cv2.rectangle(
        img,
        (margin, margin), (size - margin, size - margin),
        slab_color, thickness=-1,
    )
    path = tmp_path / filename
    cv2.imwrite(str(path), img)
    return path


def _synthetic_slab_with_green_boundary(
    tmp_path: Path, filename: str = "slab_green.jpg",
    *, size: int = 512, margin: int = 40,
) -> Path:
    img = np.zeros((size, size, 3), dtype=np.uint8)
    img[:, :] = (60, 60, 60)  # dark grey background
    # Green rectangle boundary — bright pure green (BGR).
    cv2.rectangle(
        img,
        (margin, margin), (size - margin, size - margin),
        (0, 255, 0), thickness=4,
    )
    # Fill interior with a slab-ish tone.
    cv2.rectangle(
        img,
        (margin + 4, margin + 4), (size - margin - 4, size - margin - 4),
        (200, 180, 150), thickness=-1,
    )
    path = tmp_path / filename
    cv2.imwrite(str(path), img)
    return path


def test_detect_slab_corners_on_synthetic_slab(tmp_path):
    img_path = _synthetic_slab(tmp_path)
    image = cv2.imread(str(img_path))
    result = detect_slab_corners(image)
    assert result is not None
    # The synthetic slab is roughly (60, 60) to (452, 452). Corners
    # should land within a few pixels of that.
    tl = result.corners.top_left
    br = result.corners.bottom_right
    assert 40 <= tl[0] <= 80 and 40 <= tl[1] <= 80
    assert 430 <= br[0] <= 470 and 430 <= br[1] <= 470
    assert result.confidence >= 0.5
    assert result.rectangularity >= 0.9


def test_rectify_to_dims_produces_target_size(tmp_path):
    img_path = _synthetic_slab(tmp_path)
    image = cv2.imread(str(img_path))
    corners = SlabCorners(
        top_left=(60, 60), top_right=(452, 60),
        bottom_right=(452, 452), bottom_left=(60, 452),
    )
    warped = rectify_to_dims(image, corners, 800, 400)
    assert warped.shape[:2] == (400, 800)


# ---------------------------------------------------------------------------
# calibrate_slab — end-to-end classification
# ---------------------------------------------------------------------------


def test_no_photo_marks_record_missing(tmp_path):
    inp = SlabCalibrationInput(
        slab_id="NP-1", excel_width_mm=1600, excel_height_mm=2000,
        original_image_path=None,
    )
    rec = calibrate_slab(inp, tmp_path / "cal")
    assert rec.calibration_status == CalibrationStatus.MISSING_PHOTO
    assert rec.source_type == SourceType.NO_PHOTO
    assert rec.usable_width_mm == 1560.0
    assert rec.usable_height_mm == 1960.0
    assert rec.calibrated_image_path is None


def test_missing_excel_dims_is_rejected(tmp_path):
    inp = SlabCalibrationInput(
        slab_id="X-1", excel_width_mm=0, excel_height_mm=200,
        original_image_path=None,
    )
    rec = calibrate_slab(inp, tmp_path / "cal")
    assert rec.calibration_status == CalibrationStatus.REJECTED
    assert "missing_excel_dimensions" in rec.warnings


def test_green_boundary_auto_approves(tmp_path):
    img = _synthetic_slab_with_green_boundary(tmp_path)
    # Excel dims chosen so the boundary's ~432 × 432 aspect (~1.0)
    # matches within the auto-approve tolerance.
    inp = SlabCalibrationInput(
        slab_id="GB-1", excel_width_mm=1600, excel_height_mm=1600,
        original_image_path=img,
    )
    rec = calibrate_slab(inp, tmp_path / "cal")
    assert rec.source_type == SourceType.GREEN_BOUNDARY
    assert rec.calibration_status == CalibrationStatus.APPROVED
    assert rec.calibrated_image_path is not None
    assert Path(rec.calibrated_image_path).exists()
    # The calibrated image should be sized to the USABLE dims
    # (1560 × 1560 with the default 1 px/mm).
    warped = cv2.imread(rec.calibrated_image_path)
    assert warped.shape[:2] == (1560, 1560)


def test_raw_photo_success_auto_approves(tmp_path):
    img = _synthetic_slab(tmp_path)
    inp = SlabCalibrationInput(
        # Matching aspect to the roughly-square slab.
        slab_id="RAW-1", excel_width_mm=1600, excel_height_mm=1600,
        original_image_path=img,
    )
    rec = calibrate_slab(inp, tmp_path / "cal")
    # Coverage on this synthetic is ~ (392^2)/(512^2) ≈ 0.59, so we
    # expect the raw-photo bucket (not scanned_crop).
    assert rec.source_type == SourceType.RAW_PHOTO
    assert rec.calibration_status == CalibrationStatus.APPROVED
    assert rec.calibration_confidence is not None
    assert rec.calibration_confidence >= 0.5


def test_raw_photo_aspect_disagreement_lands_in_review(tmp_path):
    # Slab is square in the image but Excel says landscape 4:1.
    img = _synthetic_slab(tmp_path)
    inp = SlabCalibrationInput(
        slab_id="RAW-2", excel_width_mm=2400, excel_height_mm=600,
        original_image_path=img,
    )
    rec = calibrate_slab(inp, tmp_path / "cal")
    assert rec.calibration_status in (
        CalibrationStatus.NEEDS_REVIEW, CalibrationStatus.REJECTED,
    )


def test_batch_calibrates_each_input(tmp_path):
    img1 = _synthetic_slab(tmp_path, "s1.jpg")
    img2 = _synthetic_slab(tmp_path, "s2.jpg")
    inputs = [
        SlabCalibrationInput("S1", 1600, 1600, img1),
        SlabCalibrationInput("S2", 1600, 1600, img2),
        SlabCalibrationInput("S3", 1600, 1600, None),
    ]
    records = calibrate_batch(inputs, tmp_path / "cal")
    assert [r.slab_id for r in records] == ["S1", "S2", "S3"]
    assert records[2].calibration_status == CalibrationStatus.MISSING_PHOTO


# ---------------------------------------------------------------------------
# manual corner override
# ---------------------------------------------------------------------------


def test_apply_manual_corners_flips_to_approved(tmp_path):
    img = _synthetic_slab(tmp_path)
    inp = SlabCalibrationInput("M-1", 1600, 1200, img)  # deliberately mismatched
    rec = calibrate_slab(inp, tmp_path / "cal")
    assert rec.calibration_status in (
        CalibrationStatus.NEEDS_REVIEW, CalibrationStatus.REJECTED,
    )
    manual = SlabCorners(
        top_left=(60, 60), top_right=(452, 60),
        bottom_right=(452, 452), bottom_left=(60, 452),
    )
    rec2 = apply_manual_corners(rec, manual, tmp_path / "cal")
    assert rec2.calibration_status == CalibrationStatus.APPROVED
    assert rec2.confirmed_corners == manual
    assert rec2.approved_by == "anonymous"
    assert rec2.approved_at is not None


# ---------------------------------------------------------------------------
# record round-trip
# ---------------------------------------------------------------------------


def test_calibration_record_round_trips_through_json(tmp_path):
    original = CalibrationRecord(
        slab_id="RT-1",
        source_type=SourceType.GREEN_BOUNDARY,
        excel_width_mm=1610,
        excel_height_mm=2200,
        usable_width_mm=1570,
        usable_height_mm=2160,
        calibration_status=CalibrationStatus.APPROVED,
        factory_policy_version="1.0",
        original_image_path="/tmp/o.jpg",
        calibrated_image_path="/tmp/c.jpg",
        detected_corners=SlabCorners(
            (0, 0), (100, 0), (100, 100), (0, 100),
        ),
        confirmed_corners=SlabCorners(
            (1, 1), (101, 1), (101, 101), (1, 101),
        ),
        crop_coordinates=CropRectangle(0, 0, 1570, 2160),
        calibration_confidence=0.9,
        aspect_delta=0.005,
        approved_at="2026-06-26T00:00:00+00:00",
        approved_by="anonymous",
        warnings=["low_something"],
        notes="hand-approved",
    )
    round_tripped = CalibrationRecord.from_dict(original.to_dict())
    assert round_tripped.to_dict() == original.to_dict()


# ---------------------------------------------------------------------------
# legacy green-box migration (Q10-A)
# ---------------------------------------------------------------------------


def _raw_photo_record(slab_id: str, **overrides) -> CalibrationRecord:
    defaults = dict(
        slab_id=slab_id,
        source_type=SourceType.RAW_PHOTO,
        excel_width_mm=1600,
        excel_height_mm=1600,
        usable_width_mm=1560,
        usable_height_mm=1560,
        calibration_status=CalibrationStatus.NEEDS_REVIEW,
        factory_policy_version="1.0",
        original_image_path="/tmp/o.jpg",
        calibrated_image_path="/tmp/c.jpg",
        detected_corners=SlabCorners((0, 0), (100, 0), (100, 100), (0, 100)),
        calibration_confidence=0.5,
        warnings=["low_confidence"],
    )
    defaults.update(overrides)
    return CalibrationRecord(**defaults)


def _write_legacy_image_metadata(project_root, images: list[dict]) -> None:
    import json
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / "image_metadata.json").write_text(
        json.dumps({"images": images}), encoding="utf-8",
    )


def test_migration_promotes_matching_raw_photo_record(tmp_path):
    _write_legacy_image_metadata(tmp_path, [
        {"slab_id": "LEG-1", "green_box_detected": True},
    ])
    rec = _raw_photo_record("LEG-1")
    [migrated] = migrate_legacy_green_box_records(tmp_path, [rec])
    assert migrated.source_type == SourceType.GREEN_BOUNDARY
    assert migrated.calibration_status == CalibrationStatus.APPROVED
    assert migrated.approved_by == "legacy_migration"
    assert migrated.approved_at is not None
    assert migrated.confirmed_corners == migrated.detected_corners
    assert "low_confidence" not in migrated.warnings
    assert migrated.notes == "migrated_from_legacy_green_box"


def test_migration_ignores_slab_without_green_box_detected(tmp_path):
    _write_legacy_image_metadata(tmp_path, [
        {"slab_id": "LEG-2", "green_box_detected": False},
    ])
    rec = _raw_photo_record("LEG-2")
    [untouched] = migrate_legacy_green_box_records(tmp_path, [rec])
    assert untouched.source_type == SourceType.RAW_PHOTO
    assert untouched.calibration_status == CalibrationStatus.NEEDS_REVIEW


def test_migration_ignores_non_raw_photo_records(tmp_path):
    """Already-approved, missing-photo, and rejected records must
    never be touched even if their slab_id appears in the legacy
    file — only RAW_PHOTO records are eligible for promotion."""
    _write_legacy_image_metadata(tmp_path, [
        {"slab_id": "LEG-3", "green_box_detected": True},
    ])
    approved = _raw_photo_record(
        "LEG-3", source_type=SourceType.GREEN_BOUNDARY,
        calibration_status=CalibrationStatus.APPROVED,
    )
    [untouched] = migrate_legacy_green_box_records(tmp_path, [approved])
    assert untouched is approved
    assert untouched.calibration_status == CalibrationStatus.APPROVED


def test_migration_is_noop_when_no_legacy_file(tmp_path):
    rec = _raw_photo_record("LEG-4")
    [untouched] = migrate_legacy_green_box_records(tmp_path, [rec])
    assert untouched.source_type == SourceType.RAW_PHOTO
    assert untouched.calibration_status == CalibrationStatus.NEEDS_REVIEW


def test_migration_returns_same_list_object(tmp_path):
    """Mirrors ``apply_manual_corners``: mutate in place, don't copy."""
    rec = _raw_photo_record("LEG-5")
    records = [rec]
    result = migrate_legacy_green_box_records(tmp_path, records)
    assert result is records


# ---------------------------------------------------------------------------
# count_by_status (M5 — one shared tally instead of four ad-hoc copies)
# ---------------------------------------------------------------------------


def test_count_by_status_tallies_each_status():
    records = [
        _raw_photo_record("A", calibration_status=CalibrationStatus.APPROVED),
        _raw_photo_record("B", calibration_status=CalibrationStatus.APPROVED),
        _raw_photo_record("C", calibration_status=CalibrationStatus.NEEDS_REVIEW),
        _raw_photo_record("D", calibration_status=CalibrationStatus.MISSING_PHOTO),
        _raw_photo_record("E", calibration_status=CalibrationStatus.REJECTED),
    ]
    assert count_by_status(records) == {
        "approved": 2, "needs_review": 1, "missing_photo": 1, "rejected": 1,
    }


def test_count_by_status_empty_list_is_all_zero():
    assert count_by_status([]) == {
        "approved": 0, "needs_review": 0, "missing_photo": 0, "rejected": 0,
    }


def test_calibrated_image_removes_the_20mm_border(tmp_path):
    """Regression: the calibrated usable image must be the INNER
    region after the 20 mm/side deduction — not the full slab scaled
    down. Build a green-boundary slab whose outer 20 mm band is red
    and inner region blue; after calibration the image edges must be
    blue (inner), proving the border was cropped, not merely scaled.
    """
    import numpy as np
    import cv2
    from placement_engine.calibration.pipeline import (
        calibrate_slab, SlabCalibrationInput,
    )

    ppm = 2
    w_mm, h_mm = 200, 160          # usable 160 x 120
    sw, sh = w_mm * ppm, h_mm * ppm
    pad = 40
    img = np.full((sh + 2 * pad, sw + 2 * pad, 3), 255, np.uint8)
    x0, y0 = pad, pad
    img[y0:y0 + sh, x0:x0 + sw] = (0, 0, 255)           # full slab: red band
    inset = 20 * ppm
    img[y0 + inset:y0 + sh - inset, x0 + inset:x0 + sw - inset] = (255, 0, 0)  # blue inner
    cv2.rectangle(img, (x0 - 1, y0 - 1), (x0 + sw, y0 + sh), (0, 255, 0), 6)

    src = tmp_path / "slab.png"
    cv2.imwrite(str(src), img)
    rec = calibrate_slab(
        SlabCalibrationInput("BORDER-1", w_mm, h_mm, src), tmp_path / "cal",
    )
    out = cv2.imread(rec.calibrated_image_path)
    # Output is the usable raster size (unchanged contract).
    assert (out.shape[1], out.shape[0]) == (
        int(rec.usable_width_mm), int(rec.usable_height_mm),
    )
    h, w = out.shape[:2]
    # Sample a small ring just inside every edge (5 px in, past any
    # interpolation fringe). All must be blue (inner), none red/green.
    def is_blue(px):
        b, g, r = int(px[0]), int(px[1]), int(px[2])
        return b > 150 and r < 100 and g < 120
    ring = [
        out[5, w // 2], out[h - 6, w // 2],
        out[h // 2, 5], out[h // 2, w - 6],
    ]
    assert all(is_blue(px) for px in ring), [tuple(int(c) for c in p) for p in ring]


def test_orientation_mismatch_still_approves(tmp_path):
    """Regression: a portrait slab photographed landscape (or vice
    versa) must APPROVE. Orientation is irrelevant to usability — the
    approval gate validates the slab AFTER orientation is resolved, so
    a 90 deg rotation is not an aspect mismatch. Before the gate fix
    this slab was wrongly REJECTED with aspect_ratio_mismatch.
    """
    # Landscape green boundary: interior ~740 x 480 (aspect ~1.54).
    w_px, h_px, margin = 820, 560, 40
    img = np.full((h_px, w_px, 3), (60, 60, 60), np.uint8)
    cv2.rectangle(img, (margin, margin), (w_px - margin, h_px - margin),
                  (0, 255, 0), 4)
    cv2.rectangle(img, (margin + 4, margin + 4),
                  (w_px - margin - 4, h_px - margin - 4), (200, 180, 150), -1)
    p = tmp_path / "slab_landscape.jpg"
    cv2.imwrite(str(p), img)

    # Excel is PORTRAIT (width < height), matching the slab transposed.
    inp = SlabCalibrationInput(
        slab_id="ORIENT-1", excel_width_mm=1550, excel_height_mm=2400,
        original_image_path=p,
    )
    rec = calibrate_slab(inp, tmp_path / "cal")
    assert rec.source_type == SourceType.GREEN_BOUNDARY
    assert rec.calibration_status == CalibrationStatus.APPROVED
    assert "aspect_ratio_mismatch" not in rec.warnings
    assert abs(rec.aspect_delta) < 0.02
    # Usable image in Excel (portrait) orientation, border cropped:
    # (H, W) = (2400 - 40, 1550 - 40).
    warped = cv2.imread(rec.calibrated_image_path)
    assert warped.shape[:2] == (2360, 1510)


def test_genuine_proportion_mismatch_still_flagged(tmp_path):
    """The gate fix must NOT mask a real shape error. A near-square
    green boundary against a strongly elongated Excel spec is a genuine
    proportion mismatch (not just a rotation) and must NOT auto-approve.
    """
    img = _synthetic_slab_with_green_boundary(tmp_path)  # ~square boundary
    inp = SlabCalibrationInput(
        slab_id="BADSHAPE-1", excel_width_mm=1000, excel_height_mm=2500,
        original_image_path=img,
    )
    rec = calibrate_slab(inp, tmp_path / "cal")
    assert rec.calibration_status != CalibrationStatus.APPROVED
    assert abs(rec.aspect_delta) > 0.08
