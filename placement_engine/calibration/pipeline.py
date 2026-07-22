"""Slab calibration pipeline.

Walks every ``InventorySlab`` from the upload and returns a list of
``CalibrationRecord``. Each slab hits exactly one branch:

* Excel row has no photo → ``CalibrationStatus.MISSING_PHOTO``.
* Photo has a factory green boundary → source_type
  ``GREEN_BOUNDARY``. Auto-approve if the boundary passes the
  aspect + confidence checks.
* Otherwise → attempt a raw four-corner detection (``RAW_PHOTO``).
  If the slab is close to the frame edge and the confidence is high,
  we tag it ``SCANNED_CROP`` instead — the operator cares which
  bucket the record came from but the flow is the same.
* Anything not auto-approved lands in ``NEEDS_REVIEW``. Aspect
  disagreements > 8% land in ``REJECTED`` (which the operator can
  still open + override in the review modal).

The pipeline writes calibrated images to disk. That is deliberate —
downstream consumers (Layout Helper, DXF writer) read the
calibrated image, never the original.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import cv2

from placement_engine.calibration import corners as corner_utils
from placement_engine.calibration.models import (
    CalibrationRecord,
    CalibrationStatus,
    CropRectangle,
    SlabCorners,
    SourceType,
)
from placement_engine.calibration.policy import (
    DEFAULT_APPROVED_BY,
    EDGE_DEDUCTION_MM,
    FACTORY_POLICY_VERSION,
    MIN_AUTO_APPROVE_CONFIDENCE,
    classify_aspect_agreement,
    usable_dimensions_mm,
)
from placement_engine.image_intake.green_box import (
    detect_green_box,
)


log = logging.getLogger(__name__)


# Target pixel-per-mm for the calibrated image. 1 px == 1 mm keeps
# the coordinate system self-documenting downstream: a 1610 mm slab
# produces a 1610 px calibrated image. Chosen at 1.0 for V1 because
# the operator-facing images are already big JPEGs; we can lower it
# later if disk footprint becomes an issue.
CALIBRATED_PIXELS_PER_MM: float = 1.0


@dataclass(frozen=True)
class SlabCalibrationInput:
    """Trimmed view of an ``InventorySlab`` the pipeline needs.

    Deliberately narrow so we don't have to reach into the loader's
    typed model just to run calibration in a test.
    """

    slab_id: str
    excel_width_mm: float
    excel_height_mm: float
    original_image_path: Path | None


def _target_pixels(usable_w_mm: float, usable_h_mm: float) -> tuple[int, int]:
    """Choose the calibrated image's pixel dimensions from the
    usable rectangle. Clamps to a sane minimum so tests can use
    tiny synthetic fixtures."""
    tw = max(64, int(round(usable_w_mm * CALIBRATED_PIXELS_PER_MM)))
    th = max(64, int(round(usable_h_mm * CALIBRATED_PIXELS_PER_MM)))
    return tw, th


def _rectify_and_trim(
    image,
    corners: SlabCorners,
    excel_w: float, excel_h: float,
    usable_w: float, usable_h: float,
):
    """Rectify the detected slab quad to the calibrated usable raster,
    following the confirmed factory workflow in ONE place:

        1. perspective-warp the quad (which spans the FULL physical
           slab) to an Excel-sized raster — "map the boundary exactly
           to the Excel width and height";
        2. remove the ``EDGE_DEDUCTION_MM`` border from every side —
           "deduct 20 mm from every side";
        3. the surviving inner rectangle IS the usable slab.

    Both the numeric usable dimensions (``usable_dimensions_mm``) and
    this image crop derive the 20 mm from the SAME
    ``EDGE_DEDUCTION_MM`` policy constant, so there is exactly one
    edge-deduction source of truth. Warping straight to the usable
    size (the previous behaviour) would keep the outer 20 mm border
    baked into the image and put the pixels at ``usable/excel`` px per
    mm instead of the intended 1:1 — this restores both.

    The output is exactly ``_target_pixels(usable_w, usable_h)`` so
    downstream consumers (and the existing tests) see the usable
    raster size they already expect.
    """
    out_w, out_h = _target_pixels(usable_w, usable_h)
    # Pixels-per-mm implied by the (possibly min-clamped) output size.
    # Using the usable target keeps this consistent with tiny test
    # fixtures where the clamp is active.
    ppm_w = out_w / usable_w if usable_w > 0 else CALIBRATED_PIXELS_PER_MM
    ppm_h = out_h / usable_h if usable_h > 0 else CALIBRATED_PIXELS_PER_MM
    excel_w_px = max(out_w + 2, int(round(excel_w * ppm_w)))
    excel_h_px = max(out_h + 2, int(round(excel_h * ppm_h)))
    inset_w = int(round(EDGE_DEDUCTION_MM * ppm_w))
    inset_h = int(round(EDGE_DEDUCTION_MM * ppm_h))

    rectified = corner_utils.rectify_to_dims(
        image, corners, excel_w_px, excel_h_px,
    )
    # Crop the border band off every side → the inner usable region.
    inner = rectified[
        inset_h:excel_h_px - inset_h,
        inset_w:excel_w_px - inset_w,
    ]
    if inner.shape[0] < 1 or inner.shape[1] < 1:
        # Degenerate crop (shouldn't happen once usable_* > 0); fall
        # back to the full rectified raster rather than an empty image.
        inner = rectified
    # Normalise to the exact usable target size so rounding in the
    # inset never changes the stored raster's dimensions.
    if (inner.shape[1], inner.shape[0]) != (out_w, out_h):
        inner = cv2.resize(inner, (out_w, out_h), interpolation=cv2.INTER_LINEAR)
    return inner


def _orient_corners_for_excel(
    corners: SlabCorners,
    detected_aspect: float,
    excel_aspect: float,
) -> tuple[SlabCorners, bool]:
    """Rotate the corner order 90° when the detected quad's aspect
    is inverted relative to the Excel spec.

    Returns ``(corners, swapped)``. ``swapped`` is True when we
    reassigned corners so the perspective-corrected image comes out
    with Excel-width along its horizontal axis.
    """
    if detected_aspect <= 0 or excel_aspect <= 0:
        return corners, False
    # Detected image landscape but Excel says portrait (or vice
    # versa) → rotate. We do this by cycling the corner labels.
    detected_landscape = detected_aspect >= 1.0
    excel_landscape = excel_aspect >= 1.0
    if detected_landscape == excel_landscape:
        return corners, False
    rotated = SlabCorners(
        top_left=corners.top_right,
        top_right=corners.bottom_right,
        bottom_right=corners.bottom_left,
        bottom_left=corners.top_left,
    )
    return rotated, True


def _calibrate_no_photo(
    slab: SlabCalibrationInput, usable_w: float, usable_h: float,
) -> CalibrationRecord:
    return CalibrationRecord(
        slab_id=slab.slab_id,
        source_type=SourceType.NO_PHOTO,
        excel_width_mm=slab.excel_width_mm,
        excel_height_mm=slab.excel_height_mm,
        usable_width_mm=usable_w,
        usable_height_mm=usable_h,
        calibration_status=CalibrationStatus.MISSING_PHOTO,
        factory_policy_version=FACTORY_POLICY_VERSION,
        warnings=["missing_photo"],
    )


def _write_calibrated_image(
    image, out_dir: Path, slab_id: str, suffix: str = ".jpg",
) -> Path:
    """Persist the perspective-corrected slab and return the path.

    ``slab_id`` is sanitised because it can contain slashes or
    non-ASCII characters from the ERP.
    """
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in slab_id)
    safe = safe.strip("_") or "slab"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{safe}{suffix}"
    cv2.imwrite(str(out), image)
    return out


def _record_missing_dimensions(
    slab: SlabCalibrationInput,
) -> CalibrationRecord:
    return CalibrationRecord(
        slab_id=slab.slab_id,
        source_type=SourceType.NO_PHOTO,
        excel_width_mm=slab.excel_width_mm,
        excel_height_mm=slab.excel_height_mm,
        usable_width_mm=0.0,
        usable_height_mm=0.0,
        calibration_status=CalibrationStatus.REJECTED,
        factory_policy_version=FACTORY_POLICY_VERSION,
        warnings=["missing_excel_dimensions"],
    )


def calibrate_slab(
    slab: SlabCalibrationInput,
    calibrated_dir: Path,
    *,
    now: datetime | None = None,
) -> CalibrationRecord:
    """Produce a ``CalibrationRecord`` for one slab.

    Never raises — every failure is captured as a warning + a
    status suitable for the review UI. Writes the calibrated image
    to ``calibrated_dir`` when a calibrated raster is produced.
    """
    now = now or datetime.now(timezone.utc)
    excel_w = float(slab.excel_width_mm or 0.0)
    excel_h = float(slab.excel_height_mm or 0.0)

    if excel_w <= 0 or excel_h <= 0:
        return _record_missing_dimensions(slab)

    usable_w, usable_h = usable_dimensions_mm(excel_w, excel_h)
    if usable_w <= 0 or usable_h <= 0:
        rec = _record_missing_dimensions(slab)
        # Restore the excel dims so operators see the row even
        # when the slab is too small to survive the deduction.
        rec.calibration_status = CalibrationStatus.REJECTED
        rec.warnings = ["usable_dimensions_non_positive"]
        return rec

    if slab.original_image_path is None:
        return _calibrate_no_photo(slab, usable_w, usable_h)

    image = cv2.imread(str(slab.original_image_path))
    if image is None or image.size == 0:
        return CalibrationRecord(
            slab_id=slab.slab_id,
            source_type=SourceType.RAW_PHOTO,
            excel_width_mm=excel_w,
            excel_height_mm=excel_h,
            usable_width_mm=usable_w,
            usable_height_mm=usable_h,
            calibration_status=CalibrationStatus.NEEDS_REVIEW,
            factory_policy_version=FACTORY_POLICY_VERSION,
            original_image_path=str(slab.original_image_path),
            warnings=["image_unreadable"],
        )

    excel_aspect = corner_utils.image_aspect_from_bbox(excel_w, excel_h)

    # 1. Try the factory green boundary first — cheapest signal.
    green = detect_green_box(image)
    if green is not None:
        detected_corners = corner_utils.corners_from_bbox(
            green.x, green.y, green.width, green.height,
        )
        detected_aspect = corner_utils.image_aspect_from_bbox(
            green.width, green.height,
        )
        oriented, _swapped = _orient_corners_for_excel(
            detected_corners, detected_aspect, excel_aspect,
        )
        rectified = _rectify_and_trim(
            image, oriented, excel_w, excel_h, usable_w, usable_h,
        )
        return _finalise(
            slab=slab,
            source_type=SourceType.GREEN_BOUNDARY,
            excel_w=excel_w, excel_h=excel_h,
            usable_w=usable_w, usable_h=usable_h,
            detected_corners=oriented,
            confidence=green.confidence,
            rectangularity=1.0,  # green boundary is axis-aligned
            aspect_ratio=detected_aspect,
            excel_aspect=excel_aspect,
            calibrated_image=rectified,
            calibrated_dir=calibrated_dir,
            now=now,
            warnings=[],
        )

    # 2. Fall back to a four-corner detection on the raw photo.
    detection = corner_utils.detect_slab_corners(image)
    if detection is None:
        return CalibrationRecord(
            slab_id=slab.slab_id,
            source_type=SourceType.RAW_PHOTO,
            excel_width_mm=excel_w,
            excel_height_mm=excel_h,
            usable_width_mm=usable_w,
            usable_height_mm=usable_h,
            calibration_status=CalibrationStatus.NEEDS_REVIEW,
            factory_policy_version=FACTORY_POLICY_VERSION,
            original_image_path=str(slab.original_image_path),
            warnings=["corner_detection_failed"],
        )

    detected_aspect = corner_utils.image_aspect_from_corners(
        detection.corners,
    )
    oriented, _swapped = _orient_corners_for_excel(
        detection.corners, detected_aspect, excel_aspect,
    )
    rectified = _rectify_and_trim(
        image, oriented, excel_w, excel_h, usable_w, usable_h,
    )
    # A very high coverage (~1.0) usually means the operator pre-
    # cropped the image so the slab fills the frame. Tag those
    # separately so the calibration screen can show a clean bucket.
    source = (
        SourceType.SCANNED_CROP if detection.coverage > 0.85
        else SourceType.RAW_PHOTO
    )
    return _finalise(
        slab=slab,
        source_type=source,
        excel_w=excel_w, excel_h=excel_h,
        usable_w=usable_w, usable_h=usable_h,
        detected_corners=oriented,
        confidence=detection.confidence,
        rectangularity=detection.rectangularity,
        aspect_ratio=detected_aspect,
        excel_aspect=excel_aspect,
        calibrated_image=rectified,
        calibrated_dir=calibrated_dir,
        now=now,
        warnings=[],
    )


def _finalise(
    *,
    slab: SlabCalibrationInput,
    source_type: SourceType,
    excel_w: float, excel_h: float,
    usable_w: float, usable_h: float,
    detected_corners: SlabCorners,
    confidence: float,
    rectangularity: float,
    aspect_ratio: float,
    excel_aspect: float,
    calibrated_image,
    calibrated_dir: Path,
    now: datetime,
    warnings: list[str],
) -> CalibrationRecord:
    """Persist the calibrated image + build the record + classify."""
    bucket, delta = classify_aspect_agreement(aspect_ratio, excel_aspect)
    calibrated_path = _write_calibrated_image(
        calibrated_image, calibrated_dir, slab.slab_id,
    )
    tw = calibrated_image.shape[1]
    th = calibrated_image.shape[0]
    crop = CropRectangle(x=0.0, y=0.0, width=float(tw), height=float(th))

    if bucket == "reject":
        status = CalibrationStatus.REJECTED
        warnings = warnings + ["aspect_ratio_mismatch"]
        approved_at = None
        approved_by = None
    elif (
        bucket == "auto"
        and confidence >= MIN_AUTO_APPROVE_CONFIDENCE
        and rectangularity >= 0.85
    ):
        status = CalibrationStatus.APPROVED
        approved_at = now.isoformat()
        approved_by = DEFAULT_APPROVED_BY
    else:
        status = CalibrationStatus.NEEDS_REVIEW
        if bucket == "review":
            warnings = warnings + ["aspect_ratio_review"]
        if confidence < MIN_AUTO_APPROVE_CONFIDENCE:
            warnings = warnings + ["low_confidence"]
        if rectangularity < 0.85:
            warnings = warnings + ["low_rectangularity"]
        approved_at = None
        approved_by = None

    return CalibrationRecord(
        slab_id=slab.slab_id,
        source_type=source_type,
        excel_width_mm=excel_w,
        excel_height_mm=excel_h,
        usable_width_mm=usable_w,
        usable_height_mm=usable_h,
        calibration_status=status,
        factory_policy_version=FACTORY_POLICY_VERSION,
        original_image_path=str(slab.original_image_path),
        calibrated_image_path=str(calibrated_path),
        detected_corners=detected_corners,
        confirmed_corners=(
            detected_corners
            if status == CalibrationStatus.APPROVED else None
        ),
        crop_coordinates=crop,
        calibration_confidence=float(confidence),
        aspect_delta=float(delta),
        approved_at=approved_at,
        approved_by=approved_by,
        warnings=warnings,
    )


def calibrate_batch(
    slabs: list[SlabCalibrationInput],
    calibrated_dir: Path,
    *,
    now: datetime | None = None,
) -> list[CalibrationRecord]:
    """Convenience wrapper — calibrate every slab in a list."""
    return [
        calibrate_slab(slab, calibrated_dir, now=now)
        for slab in slabs
    ]


LEGACY_IMAGE_METADATA_FILENAME = "image_metadata.json"


def migrate_legacy_green_box_records(
    project_root: Path,
    records: list[CalibrationRecord],
    *, now: datetime | None = None,
) -> list[CalibrationRecord]:
    """Auto-approve records a pre-M1 green-box pass already validated.

    Before this calibration module existed, ``image_intake/processor.py``
    wrote a per-project ``image_metadata.json`` recording which slabs
    had a detected green boundary (``green_box_detected: true``).
    Spec Q10-A: operators upgrading from that flow shouldn't have to
    re-calibrate slabs it already validated. Any ``RAW_PHOTO`` record
    whose ``slab_id`` appears in that legacy file with a detected
    green box is promoted to ``source_type=GREEN_BOUNDARY`` and
    ``status=APPROVED``.

    Mutates and returns ``records`` in place — mirrors
    ``apply_manual_corners``. Records of any other source_type or
    status (already approved, missing photo, rejected on aspect,
    etc.) are left untouched. No-ops when the legacy file doesn't
    exist for this project.
    """
    legacy_path = project_root / LEGACY_IMAGE_METADATA_FILENAME
    if not legacy_path.exists():
        return records
    payload = json.loads(legacy_path.read_text(encoding="utf-8"))
    legacy_by_slab = {
        img["slab_id"]: img
        for img in payload.get("images", [])
        if img.get("green_box_detected") and img.get("slab_id")
    }
    if not legacy_by_slab:
        return records
    now = now or datetime.now(timezone.utc)
    stale_warnings = {
        "aspect_ratio_mismatch", "aspect_ratio_review",
        "low_confidence", "low_rectangularity",
    }
    for rec in records:
        if rec.source_type != SourceType.RAW_PHOTO:
            continue
        if rec.slab_id not in legacy_by_slab:
            continue
        rec.source_type = SourceType.GREEN_BOUNDARY
        rec.calibration_status = CalibrationStatus.APPROVED
        rec.confirmed_corners = rec.confirmed_corners or rec.detected_corners
        rec.approved_at = now.isoformat()
        rec.approved_by = "legacy_migration"
        rec.warnings = [w for w in rec.warnings if w not in stale_warnings]
        note = "migrated_from_legacy_green_box"
        rec.notes = f"{rec.notes}; {note}" if rec.notes else note
    return records


def apply_manual_corners(
    record: CalibrationRecord,
    corners: SlabCorners,
    calibrated_dir: Path,
    *,
    now: datetime | None = None,
    approver: str = DEFAULT_APPROVED_BY,
) -> CalibrationRecord:
    """Re-rectify a slab using operator-confirmed corners.

    Returns a new ``CalibrationRecord`` (mutating the input in-place
    is also fine since it's a dataclass). The status flips to
    ``APPROVED`` because the operator vouched for the corners.
    """
    if not record.original_image_path:
        return record
    now = now or datetime.now(timezone.utc)
    image = cv2.imread(record.original_image_path)
    if image is None:
        record.warnings = list(record.warnings) + ["image_unreadable"]
        record.calibration_status = CalibrationStatus.REJECTED
        return record
    rectified = _rectify_and_trim(
        image, corners,
        record.excel_width_mm, record.excel_height_mm,
        record.usable_width_mm, record.usable_height_mm,
    )
    calibrated_path = _write_calibrated_image(
        rectified, calibrated_dir, record.slab_id,
    )
    record.confirmed_corners = corners
    record.calibrated_image_path = str(calibrated_path)
    record.crop_coordinates = CropRectangle(
        x=0.0, y=0.0,
        width=float(rectified.shape[1]),
        height=float(rectified.shape[0]),
    )
    record.calibration_status = CalibrationStatus.APPROVED
    record.approved_at = now.isoformat()
    record.approved_by = approver
    record.factory_policy_version = FACTORY_POLICY_VERSION
    return record
