"""Bottom-Left Fill packer with grid scan + polygon acceptance.

V1 Strategy A — the smallest meaningful step up from ``polygon_pack``.

For each slab (in **descending-area** order):

    * Scan candidate (x, y) positions on a coarse grid inside the
      target bounding box.
    * Pick the **bottom-most, then left-most** position that:
        - lies fully inside the usable polygon (boundary − Σ holes), AND
        - does not AABB-overlap any previously placed slab.
    * If no such position exists, reject the slab with a diagnostic
      reason.

Compared with ``polygon_pack``:

    * Fixes the shelf-walk row blind spots — every column gets a
      chance because the scan covers the entire bbox at the grid
      resolution, not just one cursor row.
    * Sorts by descending area so tall/wide slabs anchor row heights
      first, leaving the smaller slabs to fill the remaining slots.
    * Still no rotation, no seam scoring, no offcut reuse — those
      belong to later strategies.

Shelf-pack and polygon-pack are intentionally left untouched and
remain runnable for comparison.

Default grid step is 50 mm; configurable via ``grid_step_mm``. At
50 mm on a 12 × 8 m bbox that's ~38k positions per slab, with a
cheap AABB pre-filter against placed slabs and a single Shapely
``contains`` call per surviving position. Runtime stays sub-second
for V1-scale inventories on the example DXFs.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal

from shapely.geometry import Polygon as ShPolygon
from shapely.geometry.base import BaseGeometry

from placement_engine.inventory.model import InventorySlab
from placement_engine.target_area.dxf_target import TargetGeometry

# A 50 mm grid is fine enough to find tight placements between slabs
# (slab widths are 1500-2700 mm so 50 mm = ~2% of a slab side) and
# coarse enough to keep the scan well under a second for V1 inventories.
DEFAULT_GRID_STEP_MM: float = 50.0

# Floating-point fudge for the bbox-bounds comparison in the scan loop
# (slabs whose dimensions sum to exactly the bbox edge should still be
# accepted at the boundary).
_BBOX_EPS: float = 1e-6

# Rejection codes. Kept narrow on purpose — anything more specific
# requires reproducing the search, and a designer doesn't need it.
RejectionReason = Literal[
    "too_large_for_bbox",   # slab itself exceeds the target bbox
    "no_valid_position",    # no grid cell yielded a valid placement
]


@dataclass
class BLFPlacement:
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
class BLFRejected:
    """A slab the BLF packer could not place anywhere."""

    slab_id: str
    width_mm: float
    height_mm: float
    reason: RejectionReason


@dataclass
class BLFPackResult:
    placements: list[BLFPlacement]
    rejected: list[BLFRejected]
    target: TargetGeometry
    grid_step_mm: float
    runtime_seconds: float

    @property
    def placed_count(self) -> int:
        return len(self.placements)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)

    @property
    def usable_area_m2(self) -> float:
        return self.target.usable_area_m2

    @property
    def placed_area_m2(self) -> float:
        return sum(
            p.width_mm * p.height_mm for p in self.placements
        ) / 1_000_000.0

    @property
    def real_coverage_percentage(self) -> float:
        usable = self.usable_area_m2
        return 100.0 * self.placed_area_m2 / usable if usable > 0 else 0.0


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _build_usable_polygon(target: TargetGeometry) -> BaseGeometry:
    """Boundary polygon minus every hole (Shapely)."""
    usable: BaseGeometry = ShPolygon(target.boundary)
    for hole in target.holes:
        usable = usable.difference(ShPolygon(hole))
    return usable


def _aabb_overlaps_any(
    x: float, y: float, w: float, h: float,
    placed: list[BLFPlacement],
) -> bool:
    """Axis-aligned bbox overlap check against previously placed slabs.

    Touching edges (one slab's right == next slab's left) is NOT an
    overlap — strict inequalities make flush adjacent placements
    legal, which matches what `shelf_pack` already produces.
    """
    x1 = x + w
    y1 = y + h
    for p in placed:
        if (
            x < p.x + p.width_mm
            and x1 > p.x
            and y < p.y + p.height_mm
            and y1 > p.y
        ):
            return True
    return False


def _grid_positions(
    bbox_w: float, bbox_h: float,
    slab_w: float, slab_h: float,
    step: float,
) -> Iterable[tuple[float, float]]:
    """Yield (x, y) candidates in bottom-left-first order.

    The outer loop is y (bottom first), the inner loop is x (left
    first), so the first valid position the caller finds is the
    bottom-most then left-most — the BL invariant.

    The last y / x is always tested at the **maximum legal position**
    (bbox_max − slab side) even if it's not on the grid, so a slab
    that exactly fills a remaining strip isn't missed by step rounding.
    """
    max_y = bbox_h - slab_h
    max_x = bbox_w - slab_w
    if max_y < -_BBOX_EPS or max_x < -_BBOX_EPS:
        return  # slab won't fit at all

    def _axis(max_v: float) -> list[float]:
        if max_v <= 0:
            return [0.0]
        out: list[float] = []
        v = 0.0
        while v < max_v - _BBOX_EPS:
            out.append(v)
            v += step
        # Always include the snap-to-edge end so a slab exactly the
        # width of the remaining bbox strip still gets tested.
        out.append(max_v)
        return out

    ys = _axis(max_y)
    xs = _axis(max_x)
    for y in ys:
        for x in xs:
            yield x, y


# ---------------------------------------------------------------------------
# top-level packer
# ---------------------------------------------------------------------------


def blf_pack(
    slabs: Iterable[InventorySlab],
    target: TargetGeometry,
    *,
    grid_step_mm: float = DEFAULT_GRID_STEP_MM,
) -> BLFPackResult:
    """Bottom-Left Fill placement with grid scan + polygon acceptance."""
    if grid_step_mm <= 0:
        raise ValueError(
            f"grid_step_mm must be > 0; got {grid_step_mm}"
        )

    bx0, by0, bx1, by1 = target.bbox
    bbox_w = bx1 - bx0
    bbox_h = by1 - by0
    if bbox_w <= 0 or bbox_h <= 0:
        raise ValueError(
            f"target bbox must have positive dimensions; got {bbox_w}×{bbox_h}"
        )

    # Sort by descending area — anchor row heights with the largest
    # slabs first so smaller slabs slot into the remaining gaps.
    inventory = sorted(
        slabs,
        key=lambda s: -(s.width_mm * s.height_mm),
    )

    usable = _build_usable_polygon(target)
    placed: list[BLFPlacement] = []
    rejected: list[BLFRejected] = []

    started = time.perf_counter()
    for slab in inventory:
        if slab.width_mm > bbox_w + _BBOX_EPS or slab.height_mm > bbox_h + _BBOX_EPS:
            rejected.append(BLFRejected(
                slab_id=slab.slab_id,
                width_mm=slab.width_mm,
                height_mm=slab.height_mm,
                reason="too_large_for_bbox",
            ))
            continue

        chosen: tuple[float, float] | None = None
        for x, y in _grid_positions(
            bbox_w, bbox_h, slab.width_mm, slab.height_mm, grid_step_mm,
        ):
            # Cheap AABB pre-filter — skips the expensive Shapely
            # contains call whenever the candidate clearly overlaps
            # another placement.
            if _aabb_overlaps_any(x, y, slab.width_mm, slab.height_mm, placed):
                continue
            rect = ShPolygon([
                (x, y), (x + slab.width_mm, y),
                (x + slab.width_mm, y + slab.height_mm),
                (x, y + slab.height_mm),
            ])
            if usable.contains(rect):
                chosen = (x, y)
                break

        if chosen is None:
            rejected.append(BLFRejected(
                slab_id=slab.slab_id,
                width_mm=slab.width_mm,
                height_mm=slab.height_mm,
                reason="no_valid_position",
            ))
            continue

        cx, cy = chosen
        placed.append(BLFPlacement(
            slab_id=slab.slab_id,
            x=cx, y=cy,
            width_mm=slab.width_mm,
            height_mm=slab.height_mm,
            image_path=slab.image_path,
            image_available=slab.image_available,
            processed_image_path=slab.processed_image_path,
        ))

    runtime = time.perf_counter() - started

    return BLFPackResult(
        placements=placed,
        rejected=rejected,
        target=target,
        grid_step_mm=grid_step_mm,
        runtime_seconds=runtime,
    )
