"""V1 left-to-right shelf packer — smoke test only.

This is intentionally NOT a real placement strategy. It exists to
exercise the geometry pipeline end-to-end before any optimization
work begins. It does no rotation, no seam scoring, no offcut reuse, no
visual matching. Slabs are placed in the order they appear, one row at
a time, wrapping when they hit the right edge.

A real placement strategy lives under ``placement_engine.strategies``
and consumes the engine's Pydantic `ProjectInput` — not these
`InventorySlab` records directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from placement_engine.inventory.model import InventorySlab


@dataclass
class Placement:
    """One placed slab in project-space coordinates (millimetres).

    The origin is the project's bottom-left corner. ``(x, y)`` is the
    bottom-left corner of the placed rectangle. ``width_mm`` and
    ``height_mm`` keep the orientation the slab arrived in — V1 does
    not rotate.
    """

    slab_id: str
    x: float
    y: float
    width_mm: float
    height_mm: float
    image_path: Path | None
    image_available: bool
    # Cropped/processed photo (e.g. from `image_intake`) when one exists
    # on disk. Preview consumers should prefer this over `image_path`.
    processed_image_path: Path | None = None


@dataclass
class ShelfPackResult:
    placements: list[Placement]
    overflow: list[InventorySlab]
    project_width_mm: float
    project_height_mm: float

    @property
    def project_area_m2(self) -> float:
        return self.project_width_mm * self.project_height_mm / 1_000_000.0

    @property
    def placed_area_m2(self) -> float:
        return sum(
            p.width_mm * p.height_mm for p in self.placements
        ) / 1_000_000.0

    @property
    def uncovered_area_m2(self) -> float:
        return max(self.project_area_m2 - self.placed_area_m2, 0.0)


def shelf_pack(
    slabs: Iterable[InventorySlab],
    project_width_mm: float,
    project_height_mm: float,
) -> ShelfPackResult:
    """Place slabs left-to-right, wrap to a new row when out of width.

    A slab is moved to ``overflow`` if it is wider/taller than the
    project itself, or if the next row would push it past the top edge.
    The first slab on every row is placed even if it exceeds the
    remaining width, because the wrap happens before placement — the
    only way to overflow is to be physically larger than the project.
    """
    if project_width_mm <= 0 or project_height_mm <= 0:
        raise ValueError(
            f"project dimensions must be positive; got "
            f"{project_width_mm}x{project_height_mm}"
        )

    placements: list[Placement] = []
    overflow: list[InventorySlab] = []
    x = 0.0
    y = 0.0
    row_height = 0.0

    for slab in slabs:
        if slab.width_mm > project_width_mm or slab.height_mm > project_height_mm:
            overflow.append(slab)
            continue
        # Wrap to next row if this slab won't fit on the current shelf.
        if x + slab.width_mm > project_width_mm:
            x = 0.0
            y += row_height
            row_height = 0.0
        # New row could push past the top edge.
        if y + slab.height_mm > project_height_mm:
            overflow.append(slab)
            continue
        placements.append(
            Placement(
                slab_id=slab.slab_id,
                x=x,
                y=y,
                width_mm=slab.width_mm,
                height_mm=slab.height_mm,
                image_path=slab.image_path,
                image_available=slab.image_available,
                processed_image_path=slab.processed_image_path,
            )
        )
        x += slab.width_mm
        row_height = max(row_height, slab.height_mm)

    return ShelfPackResult(
        placements=placements,
        overflow=overflow,
        project_width_mm=project_width_mm,
        project_height_mm=project_height_mm,
    )
