"""Synthetic test slab inventory generation for pipeline validation.

These slabs are **not** the real company slab database. They exist so
the DXF → intake → engine → package pipeline can be exercised
end-to-end with a slab inventory large enough to reach full coverage.
When a real slab database lands, the engine reads from that instead;
this module is a validation aid only.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Any, Literal

DEFAULT_SLAB_WIDTH = 3200.0
DEFAULT_SLAB_HEIGHT = 1800.0
DEFAULT_SLAB_THICKNESS = 20.0
DEFAULT_BUFFER_FACTOR = 1.25


def generate_test_slabs(
    count: int,
    width: float = DEFAULT_SLAB_WIDTH,
    height: float = DEFAULT_SLAB_HEIGHT,
    thickness: float = DEFAULT_SLAB_THICKNESS,
) -> list[dict[str, Any]]:
    """Return `count` synthetic slabs as engine-input dicts.

    IDs are sequential and zero-padded (`S001`, `S002`, …) so the
    output is deterministic and easy to read in reports.
    """
    if count < 1:
        raise ValueError(f"count must be >= 1, got {count}")
    if width <= 0 or height <= 0 or thickness <= 0:
        raise ValueError(
            f"slab dimensions must be positive "
            f"(got {width} x {height} x {thickness})"
        )
    slabs: list[dict[str, Any]] = []
    for i in range(1, count + 1):
        slab_id = f"S{i:03d}"
        slabs.append({
            "slab_id": slab_id,
            "width": width,
            "height": height,
            "thickness": thickness,
            "image_path": f"images/test_slabs/{slab_id}.png",
            "vein_direction": "horizontal",
            "design_notes": "Synthetic test slab generated for pipeline validation.",
        })
    return slabs


def estimate_slab_count(
    project_usable_area: float,
    slab_area: float,
    buffer_factor: float = DEFAULT_BUFFER_FACTOR,
) -> int:
    """Estimate a slab count likely to be sufficient to cover the project.

    `ceil((project_usable_area / slab_area) * buffer_factor)`, floored
    at 1. The buffer covers the material lost to edge clipping and
    awkward fits; 1.25 is a deliberately rough rule of thumb.

    Note: this is an *area-based* estimate. A strategy that wastes
    whole slabs on thin rows (e.g. the row-based `balanced` generator
    on a narrow corridor) may still fall short — that gap is exactly
    what the validation suite surfaces.
    """
    if project_usable_area <= 0:
        raise ValueError(
            f"project_usable_area must be positive, got {project_usable_area}"
        )
    if slab_area <= 0:
        raise ValueError(f"slab_area must be positive, got {slab_area}")
    if buffer_factor < 1.0:
        raise ValueError(
            f"buffer_factor must be >= 1.0, got {buffer_factor}"
        )
    raw = (project_usable_area / slab_area) * buffer_factor
    return max(1, ceil(raw))


@dataclass(frozen=True)
class SlabInventorySpec:
    """Configuration for generating a test slab inventory.

    `count` is either an explicit integer or the literal `"auto"`, in
    which case the count is derived from the project usable area via
    `estimate_slab_count`.
    """

    count: int | Literal["auto"] = "auto"
    width: float = DEFAULT_SLAB_WIDTH
    height: float = DEFAULT_SLAB_HEIGHT
    thickness: float = DEFAULT_SLAB_THICKNESS
    buffer_factor: float = DEFAULT_BUFFER_FACTOR

    @property
    def slab_area(self) -> float:
        return self.width * self.height

    def resolve_count(self, project_usable_area: float) -> int:
        """Return the concrete slab count for a given project area."""
        if self.count == "auto":
            return estimate_slab_count(
                project_usable_area, self.slab_area, self.buffer_factor
            )
        n = int(self.count)
        if n < 1:
            raise ValueError(f"explicit slab count must be >= 1, got {n}")
        return n

    def resolve(self, project_usable_area: float) -> list[dict[str, Any]]:
        """Generate the slab inventory for a given project area."""
        n = self.resolve_count(project_usable_area)
        return generate_test_slabs(n, self.width, self.height, self.thickness)
