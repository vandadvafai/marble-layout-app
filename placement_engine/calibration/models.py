"""Standardized slab calibration record + supporting types.

The whole downstream stack (matcher, fit checker, DXF writer, client
PNG) reads ONLY ``CalibrationRecord`` for approved slabs. The
original photo path lives on the record for traceability and the
manual-review UI, but no consumer reads it directly for placement or
export.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SourceType(str, Enum):
    """How the slab image was captured.

    * ``green_boundary`` — factory-annotated photo. A bright green
      rectangle encloses the physical slab. The existing green-box
      detector locates the boundary.
    * ``scanned_crop`` — image was pre-cropped/scanned so the slab
      occupies most of the frame with a plain background. Detection
      is a background-vs-slab split.
    * ``raw_photo`` — uncalibrated photograph. Requires four-corner
      detection + perspective correction.
    * ``no_photo`` — the Excel row has no linked image. Layout Helper
      cannot use this slab until a photo is uploaded.
    """

    GREEN_BOUNDARY = "green_boundary"
    SCANNED_CROP = "scanned_crop"
    RAW_PHOTO = "raw_photo"
    NO_PHOTO = "no_photo"


class CalibrationStatus(str, Enum):
    """Where a calibration record sits in the approval workflow.

    * ``approved`` — the record is factory-usable. The matcher, fit
      checker and DXF writer only see records in this state.
    * ``needs_review`` — the detector didn't reach the auto-approve
      threshold. The operator must open the calibration modal and
      confirm / adjust the four corners.
    * ``rejected`` — the operator (or the detector) flagged this
      slab as unusable. Excluded from Layout Helper.
    * ``missing_photo`` — the Excel row has no image. Also excluded
      from Layout Helper until a photo is provided.
    """

    APPROVED = "approved"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"
    MISSING_PHOTO = "missing_photo"


@dataclass(frozen=True)
class SlabCorners:
    """Four slab corners in image pixel coordinates.

    Order is ``(top-left, top-right, bottom-right, bottom-left)``
    following the canvas convention (y grows downward). All values
    are floats so sub-pixel manual adjustments are preserved.
    """

    top_left: tuple[float, float]
    top_right: tuple[float, float]
    bottom_right: tuple[float, float]
    bottom_left: tuple[float, float]

    def as_list(self) -> list[list[float]]:
        return [
            list(self.top_left),
            list(self.top_right),
            list(self.bottom_right),
            list(self.bottom_left),
        ]

    @classmethod
    def from_iterable(cls, points: list) -> "SlabCorners":
        """Build a ``SlabCorners`` from any 4-length sequence of
        2-length sequences. Raises ``ValueError`` on malformed
        input so a garbage manual-review payload fails loudly."""
        if len(points) != 4:
            raise ValueError(f"expected 4 corners, got {len(points)}")
        pts: list[tuple[float, float]] = []
        for i, p in enumerate(points):
            if len(p) != 2:
                raise ValueError(f"corner {i} needs 2 coords, got {p}")
            pts.append((float(p[0]), float(p[1])))
        return cls(*pts)


@dataclass(frozen=True)
class CropRectangle:
    """Axis-aligned bbox that describes the crop applied when
    producing the ``calibrated_image``. Pixel units on the
    calibrated (perspective-corrected) image."""

    x: float
    y: float
    width: float
    height: float

    def as_dict(self) -> dict[str, float]:
        return {
            "x": self.x, "y": self.y,
            "width": self.width, "height": self.height,
        }


@dataclass
class CalibrationRecord:
    """One row of the standardized slab table.

    Every approved record satisfies:
      * ``usable_width_mm == excel_width_mm - 40`` (policy v1)
      * ``usable_height_mm == excel_height_mm - 40``
      * ``calibrated_image_path`` points at an image sized to
        ``usable_*`` pixel-for-mm at the export resolution
      * ``confirmed_corners`` reflects whatever the operator saw
        when they clicked Approve

    Mutability
    ----------
    We keep this a ``dataclass`` (not frozen) because the
    calibration UI mutates fields as the operator adjusts corners
    and re-approves. ``storage.save_records`` serialises every
    change to disk immediately.
    """

    slab_id: str
    source_type: SourceType
    excel_width_mm: float
    excel_height_mm: float
    usable_width_mm: float
    usable_height_mm: float
    calibration_status: CalibrationStatus
    factory_policy_version: str
    original_image_path: str | None = None
    calibrated_image_path: str | None = None
    detected_corners: SlabCorners | None = None
    confirmed_corners: SlabCorners | None = None
    crop_coordinates: CropRectangle | None = None
    calibration_confidence: float | None = None
    aspect_delta: float | None = None
    approved_at: str | None = None
    approved_by: str | None = None
    warnings: list[str] = field(default_factory=list)
    notes: str | None = None

    @property
    def is_approved(self) -> bool:
        return self.calibration_status == CalibrationStatus.APPROVED

    def to_dict(self) -> dict[str, Any]:
        return {
            "slab_id": self.slab_id,
            "source_type": self.source_type.value,
            "excel_width_mm": self.excel_width_mm,
            "excel_height_mm": self.excel_height_mm,
            "usable_width_mm": self.usable_width_mm,
            "usable_height_mm": self.usable_height_mm,
            "calibration_status": self.calibration_status.value,
            "factory_policy_version": self.factory_policy_version,
            "original_image_path": self.original_image_path,
            "calibrated_image_path": self.calibrated_image_path,
            "detected_corners": (
                self.detected_corners.as_list()
                if self.detected_corners else None
            ),
            "confirmed_corners": (
                self.confirmed_corners.as_list()
                if self.confirmed_corners else None
            ),
            "crop_coordinates": (
                self.crop_coordinates.as_dict()
                if self.crop_coordinates else None
            ),
            "calibration_confidence": self.calibration_confidence,
            "aspect_delta": self.aspect_delta,
            "approved_at": self.approved_at,
            "approved_by": self.approved_by,
            "warnings": list(self.warnings),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CalibrationRecord":
        def _corners(v):
            if v is None:
                return None
            return SlabCorners.from_iterable(v)
        crop = data.get("crop_coordinates")
        return cls(
            slab_id=data["slab_id"],
            source_type=SourceType(data["source_type"]),
            excel_width_mm=float(data["excel_width_mm"]),
            excel_height_mm=float(data["excel_height_mm"]),
            usable_width_mm=float(data["usable_width_mm"]),
            usable_height_mm=float(data["usable_height_mm"]),
            calibration_status=CalibrationStatus(data["calibration_status"]),
            factory_policy_version=data["factory_policy_version"],
            original_image_path=data.get("original_image_path"),
            calibrated_image_path=data.get("calibrated_image_path"),
            detected_corners=_corners(data.get("detected_corners")),
            confirmed_corners=_corners(data.get("confirmed_corners")),
            crop_coordinates=(
                CropRectangle(**crop) if crop is not None else None
            ),
            calibration_confidence=data.get("calibration_confidence"),
            aspect_delta=data.get("aspect_delta"),
            approved_at=data.get("approved_at"),
            approved_by=data.get("approved_by"),
            warnings=list(data.get("warnings") or []),
            notes=data.get("notes"),
        )


def count_by_status(records: list["CalibrationRecord"]) -> dict[str, int]:
    """Per-status tally, keyed by ``CalibrationStatus.value``.

    The Step-3 upload response, the persisted session summary, and
    the ``/api/calibration/records`` endpoint all report the same
    four counts (approved / needs_review / missing_photo /
    rejected). Compute them here, once, so the three call sites
    can't drift out of sync with each other."""
    counts = {status.value: 0 for status in CalibrationStatus}
    for r in records:
        counts[r.calibration_status.value] += 1
    return counts
