"""Slab Calibration & Standardization module.

Public API:

    from placement_engine.calibration import (
        CalibrationRecord, CalibrationStatus, SourceType,
        SlabCorners, CropRectangle,
        calibrate_slab, calibrate_batch, apply_manual_corners,
        usable_dimensions_mm, FACTORY_POLICY_VERSION,
    )
"""

from placement_engine.calibration.models import (
    CalibrationRecord,
    CalibrationStatus,
    CropRectangle,
    SlabCorners,
    SourceType,
    count_by_status,
)
from placement_engine.calibration.pipeline import (
    SlabCalibrationInput,
    apply_manual_corners,
    calibrate_batch,
    calibrate_slab,
    migrate_legacy_green_box_records,
)
from placement_engine.calibration.policy import (
    AUTO_APPROVE_ASPECT_LIMIT,
    EDGE_DEDUCTION_MM,
    EDGE_DEDUCTION_TOTAL_MM,
    FACTORY_POLICY_VERSION,
    INTER_PIECE_SPACING_MM,
    MIN_AUTO_APPROVE_CONFIDENCE,
    NEEDS_REVIEW_ASPECT_LIMIT,
    classify_aspect_agreement,
    usable_dimensions_mm,
)

__all__ = [
    "AUTO_APPROVE_ASPECT_LIMIT",
    "CalibrationRecord",
    "CalibrationStatus",
    "CropRectangle",
    "EDGE_DEDUCTION_MM",
    "EDGE_DEDUCTION_TOTAL_MM",
    "FACTORY_POLICY_VERSION",
    "INTER_PIECE_SPACING_MM",
    "MIN_AUTO_APPROVE_CONFIDENCE",
    "NEEDS_REVIEW_ASPECT_LIMIT",
    "SlabCalibrationInput",
    "SlabCorners",
    "SourceType",
    "apply_manual_corners",
    "calibrate_batch",
    "calibrate_slab",
    "classify_aspect_agreement",
    "count_by_status",
    "migrate_legacy_green_box_records",
    "usable_dimensions_mm",
]
