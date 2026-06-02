"""TargetArea dataclass + consistency checks.

V1 target area is a single axis-aligned rectangle in millimetres. The
canonical area is `calculated_area_m2 = width_mm * height_mm / 1e6`.
`required_area_m2` (optional) is the designer-stated usable area for
the room; we cross-check it against the calculated area and warn when
they disagree by more than 5%.

Construction validates dimensions and raises `ValueError` for zero or
negative values — those would silently produce nonsense placements.
The optional area cross-check is non-blocking and lives in
`target_area_warnings()` so callers can choose strictness.
"""

from __future__ import annotations

from dataclasses import dataclass

# Same tolerance the slab-intake layer uses for its area cross-check.
REQUIRED_AREA_RELATIVE_TOLERANCE: float = 0.05


@dataclass
class TargetArea:
    """A simple rectangular client area in millimetres (V1)."""

    target_id: str
    name: str
    width_mm: float
    height_mm: float
    required_area_m2: float | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.width_mm, (int, float)) or self.width_mm <= 0:
            raise ValueError(
                f"TargetArea.width_mm must be > 0, got {self.width_mm!r}"
            )
        if not isinstance(self.height_mm, (int, float)) or self.height_mm <= 0:
            raise ValueError(
                f"TargetArea.height_mm must be > 0, got {self.height_mm!r}"
            )
        if (
            self.required_area_m2 is not None
            and self.required_area_m2 < 0
        ):
            raise ValueError(
                f"TargetArea.required_area_m2 must be >= 0 when set, "
                f"got {self.required_area_m2!r}"
            )

    @property
    def calculated_area_m2(self) -> float:
        """Area of the rectangle in m² (width × height / 1e6)."""
        return self.width_mm * self.height_mm / 1_000_000.0


def target_area_warnings(target: TargetArea) -> list[str]:
    """Return non-blocking warning codes for a target area.

    Currently emits:

        ``required_area_mismatch`` — ``required_area_m2`` is set and
        differs from ``calculated_area_m2`` by more than
        `REQUIRED_AREA_RELATIVE_TOLERANCE`.

    The list is empty when everything checks out. Dimension validation
    happens earlier in ``__post_init__`` and would have raised by now.
    """
    warnings: list[str] = []
    if target.required_area_m2 is None:
        return warnings
    calc = target.calculated_area_m2
    ref = max(target.required_area_m2, calc)
    if ref <= 0:
        return warnings
    relative = abs(target.required_area_m2 - calc) / ref
    if relative > REQUIRED_AREA_RELATIVE_TOLERANCE:
        warnings.append("required_area_mismatch")
    return warnings
