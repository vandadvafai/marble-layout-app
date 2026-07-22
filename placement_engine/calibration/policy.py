"""Factory policy constants for the slab calibration + standardization
milestone.

All numbers here are the confirmed rules the operator signed off on.
Bumping any value is a policy change — bump ``FACTORY_POLICY_VERSION``
at the same time so the calibration records track which version they
were approved under.

Layout Helper reads these constants ONLY through calibration output
(``usable_width_mm`` / ``usable_height_mm``). The downstream fit
checker and DXF writer must never re-apply the edge deduction — see
``placement_engine/api/factory_layout.py``.
"""

from __future__ import annotations

from typing import Final


# Version tag stored on every calibration record. Increment when any
# rule below changes (or when new rules are added). Reads through
# every JSON persisted, so downstream tools can tell whether a
# calibration is still current or needs re-approval.
FACTORY_POLICY_VERSION: Final[str] = "1.0"

# 20 mm per side, deducted ONCE inside calibration. Applied to the
# Excel-authoritative width and height to produce ``usable_*``.
EDGE_DEDUCTION_MM: Final[float] = 20.0

# Total deduction across the full width / height (both sides).
EDGE_DEDUCTION_TOTAL_MM: Final[float] = 2.0 * EDGE_DEDUCTION_MM

# Factory-standard gap between neighbouring cut pieces on the same
# slab. This is the ENTIRE spacing — no blade kerf is added on top.
INTER_PIECE_SPACING_MM: Final[float] = 5.0

# Auto-approval band on the ratio ``|image_aspect / excel_aspect - 1|``.
# Values inside AUTO_APPROVE_LIMIT auto-approve when the detector's
# other checks (confidence + rectangularity) also pass.
AUTO_APPROVE_ASPECT_LIMIT: Final[float] = 0.02  # 2 %
# Above this, we refuse auto-approval outright — the image and the
# Excel dimensions disagree enough that the operator needs to look.
NEEDS_REVIEW_ASPECT_LIMIT: Final[float] = 0.08  # 8 %

# Minimum detection confidence for automatic approval. Below this, the
# record lands in Needs Review even if aspect + rectangularity pass.
MIN_AUTO_APPROVE_CONFIDENCE: Final[float] = 0.7

# Approved-by default until we add real auth. See the milestone brief.
DEFAULT_APPROVED_BY: Final[str] = "anonymous"


def usable_dimensions_mm(
    excel_width_mm: float, excel_height_mm: float,
) -> tuple[float, float]:
    """Apply the V1 factory policy: 20 mm off every side.

    Callers get back ``(usable_width_mm, usable_height_mm)``. Any
    negative or zero result is a validation failure and must be
    handled by the caller — this function does NOT clamp or raise.
    """
    return (
        excel_width_mm - EDGE_DEDUCTION_TOTAL_MM,
        excel_height_mm - EDGE_DEDUCTION_TOTAL_MM,
    )


def classify_aspect_agreement(
    image_aspect: float, excel_aspect: float,
) -> tuple[str, float]:
    """Return ``(bucket, delta)`` where ``bucket`` is one of
    ``"auto"`` | ``"review"`` | ``"reject"``.

    ``delta`` is the signed ratio deviation
    ``image_aspect / excel_aspect - 1``. Callers can log the raw
    value even when the bucket says the sample failed.
    """
    if excel_aspect <= 0:
        return "reject", float("inf")
    delta = image_aspect / excel_aspect - 1.0
    magnitude = abs(delta)
    if magnitude <= AUTO_APPROVE_ASPECT_LIMIT:
        return "auto", delta
    if magnitude <= NEEDS_REVIEW_ASPECT_LIMIT:
        return "review", delta
    return "reject", delta
