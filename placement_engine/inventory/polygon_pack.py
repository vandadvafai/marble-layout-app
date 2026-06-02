"""V1 polygon-aware shelf packer.

Same shelf-walk as ``shelf_pack`` (left-to-right, wrap on row), but
each candidate placement is accepted only if its axis-aligned slab
rectangle is fully contained inside the **usable** region of the
target — defined as ``boundary − Σholes`` via Shapely difference.

This is NOT optimization: there is no rotation, no seam scoring, no
clever search for alternative positions. Slabs that fail the polygon
check are reported with a reason (``intersects_hole`` or
``outside_boundary``) and the cursor advances as if the slab had been
placed, so the walk doesn't deadlock at a single bad position.

The older bbox-only ``shelf_pack`` remains available for comparison.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal

from shapely.geometry import Polygon as ShPolygon
from shapely.geometry.base import BaseGeometry

from placement_engine.inventory.model import InventorySlab
from placement_engine.target_area.dxf_target import TargetGeometry

RejectionReason = Literal[
    "too_large_for_bbox",   # wider/taller than the target bbox itself
    "exceeds_bbox_height",  # the next row would push past the top edge
    "intersects_hole",      # rect overlaps one of the holes
    "outside_boundary",     # rect extends past the irregular boundary
]


@dataclass
class PolygonPlacement:
    """An accepted slab placement (axis-aligned, mm units)."""

    slab_id: str
    x: float
    y: float
    width_mm: float
    height_mm: float
    image_path: Path | None
    image_available: bool
    processed_image_path: Path | None = None


@dataclass
class RejectedSlab:
    """A slab that the polygon packer chose not to place, with diagnosis."""

    slab_id: str
    width_mm: float
    height_mm: float
    reason: RejectionReason
    # The position the packer last tried for this slab. May be None for
    # ``too_large_for_bbox`` (no meaningful attempted position).
    attempted_x: float | None = None
    attempted_y: float | None = None


@dataclass
class PolygonPackResult:
    placements: list[PolygonPlacement]
    rejected: list[RejectedSlab]
    target: TargetGeometry

    # -- counts --------------------------------------------------------

    @property
    def placed_count(self) -> int:
        return len(self.placements)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)

    @property
    def rejected_outside_count(self) -> int:
        return sum(1 for r in self.rejected if r.reason == "outside_boundary")

    @property
    def rejected_holes_count(self) -> int:
        return sum(1 for r in self.rejected if r.reason == "intersects_hole")

    @property
    def rejected_oversize_count(self) -> int:
        return sum(
            1 for r in self.rejected
            if r.reason in ("too_large_for_bbox", "exceeds_bbox_height")
        )

    # -- areas / coverage ---------------------------------------------

    @property
    def usable_area_m2(self) -> float:
        """Boundary area − Σ hole areas (what the slabs can actually cover)."""
        return self.target.usable_area_m2

    @property
    def placed_area_m2(self) -> float:
        return sum(
            p.width_mm * p.height_mm for p in self.placements
        ) / 1_000_000.0

    @property
    def real_coverage_percentage(self) -> float:
        """Placed area / usable area × 100. The honest coverage metric."""
        usable = self.usable_area_m2
        return 100.0 * self.placed_area_m2 / usable if usable > 0 else 0.0


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


def _build_usable_polygon(target: TargetGeometry) -> BaseGeometry:
    """Boundary polygon minus every hole (Shapely geometry).

    The result may be a Polygon or a MultiPolygon depending on whether
    holes split the region; either supports ``.contains()`` consistently.
    """
    usable: BaseGeometry = ShPolygon(target.boundary)
    for hole in target.holes:
        usable = usable.difference(ShPolygon(hole))
    return usable


def _rect_polygon(x: float, y: float, w: float, h: float) -> ShPolygon:
    """Axis-aligned slab rectangle as a Shapely polygon."""
    return ShPolygon([(x, y), (x + w, y), (x + w, y + h), (x, y + h)])


def _classify_rejection(
    rect: ShPolygon,
    target: TargetGeometry,
    boundary: ShPolygon,
) -> RejectionReason:
    """Pick the most informative rejection code for a failed acceptance.

    Hole intersection wins over boundary overflow when both apply,
    because a real hole hit is the more specific and actionable
    diagnosis for a designer.
    """
    for hole in target.holes:
        hole_poly = ShPolygon(hole)
        if rect.intersects(hole_poly) and rect.intersection(hole_poly).area > 0:
            return "intersects_hole"
    if not boundary.contains(rect):
        return "outside_boundary"
    # Fallback (shouldn't happen if `contains` failed for a real reason).
    return "outside_boundary"


# ---------------------------------------------------------------------------
# Top-level packer
# ---------------------------------------------------------------------------


def polygon_pack(
    slabs: Iterable[InventorySlab],
    target: TargetGeometry,
) -> PolygonPackResult:
    """Shelf-walk placement with polygon (boundary + holes) acceptance.

    Cursor advances on accept *and* reject so the walk never gets
    stuck. Rejected slabs are reported with the position the packer
    attempted.
    """
    bbox_x0, bbox_y0, bbox_x1, bbox_y1 = target.bbox
    bbox_w = bbox_x1 - bbox_x0
    bbox_h = bbox_y1 - bbox_y0
    if bbox_w <= 0 or bbox_h <= 0:
        raise ValueError(
            f"target bbox must have positive dimensions; got {bbox_w}×{bbox_h}"
        )

    boundary = ShPolygon(target.boundary)
    usable = _build_usable_polygon(target)

    placements: list[PolygonPlacement] = []
    rejected: list[RejectedSlab] = []
    x = bbox_x0
    y = bbox_y0
    row_height = 0.0

    for slab in slabs:
        # Trivial reject: physically larger than the bbox itself.
        if slab.width_mm > bbox_w or slab.height_mm > bbox_h:
            rejected.append(RejectedSlab(
                slab_id=slab.slab_id,
                width_mm=slab.width_mm,
                height_mm=slab.height_mm,
                reason="too_large_for_bbox",
            ))
            continue

        # Wrap to next row if this slab won't fit on the current shelf.
        if x + slab.width_mm > bbox_x1:
            x = bbox_x0
            y += row_height
            row_height = 0.0

        # New row could push past the top edge.
        if y + slab.height_mm > bbox_y1:
            rejected.append(RejectedSlab(
                slab_id=slab.slab_id,
                width_mm=slab.width_mm,
                height_mm=slab.height_mm,
                reason="exceeds_bbox_height",
                attempted_x=x,
                attempted_y=y,
            ))
            continue

        # Polygon acceptance: rect must lie entirely inside the usable region.
        rect = _rect_polygon(x, y, slab.width_mm, slab.height_mm)
        if usable.contains(rect):
            placements.append(PolygonPlacement(
                slab_id=slab.slab_id,
                x=x,
                y=y,
                width_mm=slab.width_mm,
                height_mm=slab.height_mm,
                image_path=slab.image_path,
                image_available=slab.image_available,
                processed_image_path=slab.processed_image_path,
            ))
        else:
            reason = _classify_rejection(rect, target, boundary)
            rejected.append(RejectedSlab(
                slab_id=slab.slab_id,
                width_mm=slab.width_mm,
                height_mm=slab.height_mm,
                reason=reason,
                attempted_x=x,
                attempted_y=y,
            ))

        # Advance the cursor whether placed or rejected — keeps the
        # walk progressing instead of stalling at a bad position.
        x += slab.width_mm
        row_height = max(row_height, slab.height_mm)

    return PolygonPackResult(
        placements=placements,
        rejected=rejected,
        target=target,
    )
