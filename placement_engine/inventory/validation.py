"""Inventory-level consistency checks.

These complement (not replace) the ingestion-layer checks already
recorded in `InventorySlab.ingestion_warnings`. The engine runs these
after loading to catch issues introduced by file moves or hand-edits to
``clean_slabs.json``.

Issue codes:

    image_file_missing      image_path was set but does not resolve on disk
    image_path_unset        no image_path recorded for this slab
    non_positive_dimensions width_mm or height_mm <= 0 (the loader drops
                            these from inventory.slabs already, but this
                            check makes the rule explicit if a record is
                            constructed by hand)
    area_mismatch           area_m2 vs calculated_area_m2 differ by more
                            than `AREA_MISMATCH_RELATIVE_TOLERANCE`
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from placement_engine.inventory.model import Inventory, InventorySlab

# Same threshold as the ingestion layer to stay consistent.
AREA_MISMATCH_RELATIVE_TOLERANCE: float = 0.05

IssueCode = Literal[
    "image_file_missing",
    "image_path_unset",
    "non_positive_dimensions",
    "area_mismatch",
]


@dataclass
class InventoryIssue:
    slab_id: str
    code: IssueCode
    message: str


def _check_one(slab: InventorySlab) -> list[InventoryIssue]:
    issues: list[InventoryIssue] = []

    if slab.width_mm <= 0 or slab.height_mm <= 0:
        issues.append(
            InventoryIssue(
                slab_id=slab.slab_id,
                code="non_positive_dimensions",
                message=(
                    f"width_mm={slab.width_mm}, height_mm={slab.height_mm}"
                ),
            )
        )

    if slab.image_path is None:
        issues.append(
            InventoryIssue(
                slab_id=slab.slab_id,
                code="image_path_unset",
                message="No image_path was recorded for this slab.",
            )
        )
    elif not slab.image_available:
        issues.append(
            InventoryIssue(
                slab_id=slab.slab_id,
                code="image_file_missing",
                message=f"image_path does not resolve on disk: {slab.image_path}",
            )
        )

    if (
        slab.area_m2 is not None
        and slab.calculated_area_m2 is not None
        and slab.calculated_area_m2 > 0
    ):
        ref = max(slab.area_m2, slab.calculated_area_m2)
        rel = abs(slab.area_m2 - slab.calculated_area_m2) / ref
        if rel > AREA_MISMATCH_RELATIVE_TOLERANCE:
            issues.append(
                InventoryIssue(
                    slab_id=slab.slab_id,
                    code="area_mismatch",
                    message=(
                        f"area_m2={slab.area_m2}, "
                        f"calculated_area_m2={slab.calculated_area_m2}, "
                        f"relative_diff={rel:.2%}"
                    ),
                )
            )

    return issues


def validate_inventory(inventory: Inventory) -> list[InventoryIssue]:
    """Run all per-slab checks and return a flat list of issues.

    The inventory itself is never mutated. Issues are advisory: the
    engine decides which (if any) should block a real placement run.
    """
    issues: list[InventoryIssue] = []
    for slab in inventory.slabs:
        issues.extend(_check_one(slab))
    return issues
