"""Normalized placement view + adapters from the three packer result types.

All renderers consume only `PlacementView` — no packer-specific knowledge
leaks into the rendering layer. Likewise the JSON dump is just the view
serialized; downstream consumers never look at the packer dataclasses.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from placement_engine.inventory.blf_pack import BLFPackResult
from placement_engine.inventory.polygon_pack import PolygonPackResult
from placement_engine.inventory.shelf_pack import ShelfPackResult
from placement_engine.target_area.dxf_target import TargetGeometry
from placement_engine.target_area.model import TargetArea

ImageSource = Literal["processed", "original", "placeholder"]


@dataclass
class PlacedSlabView:
    """A single placed slab in the normalized view.

    `display_index` is the sequence number used by renderers for the
    small corner numeral (1, 2, 3...). The full `slab_id` is kept for
    the debug renderer and for downstream consumers.
    """

    slab_id: str
    display_index: int
    x_mm: float
    y_mm: float
    width_mm: float
    height_mm: float
    image_path: str | None
    image_source: ImageSource


@dataclass
class SeamView:
    """A line segment along which two placed slabs share an edge."""

    from_slab_id: str
    to_slab_id: str
    x0_mm: float
    y0_mm: float
    x1_mm: float
    y1_mm: float
    length_mm: float


@dataclass
class RejectedSlabView:
    """A slab the packer chose not to place; carried for the debug renderer."""

    slab_id: str
    width_mm: float
    height_mm: float
    reason: str
    attempted_x_mm: float | None = None
    attempted_y_mm: float | None = None


@dataclass
class TargetView:
    """The target geometry — polygon boundary + holes, axis-aligned mm."""

    target_id: str
    name: str
    boundary: list[tuple[float, float]]
    holes: list[list[tuple[float, float]]]
    bbox: tuple[float, float, float, float]  # xmin, ymin, xmax, ymax
    boundary_area_m2: float
    holes_area_m2: float
    usable_area_m2: float
    source_dxf_path: str | None = None


@dataclass
class PlacementView:
    """The full normalized view: target + placements + seams + rejections.

    This is the JSON contract every renderer reads. Don't add packer-
    specific fields here; put run-specific metadata (packer name,
    runtime, grid step, demo-default flag, ...) into `metadata`.
    """

    target: TargetView
    placements: list[PlacedSlabView]
    seams: list[SeamView] = field(default_factory=list)
    rejected: list[RejectedSlabView] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Derived metrics
    # ------------------------------------------------------------------

    @property
    def placed_count(self) -> int:
        return len(self.placements)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)

    @property
    def placed_area_m2(self) -> float:
        return sum(
            p.width_mm * p.height_mm for p in self.placements
        ) / 1_000_000.0

    @property
    def coverage_percentage(self) -> float:
        usable = self.target.usable_area_m2
        return 100.0 * self.placed_area_m2 / usable if usable > 0 else 0.0

    @property
    def total_seam_length_mm(self) -> float:
        return sum(s.length_mm for s in self.seams)

    # ------------------------------------------------------------------
    # JSON
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Convert tuples-as-lists for stable JSON shape.
        d["target"]["bbox"] = list(self.target.bbox)
        d["target"]["boundary"] = [list(p) for p in self.target.boundary]
        d["target"]["holes"] = [
            [list(p) for p in hole] for hole in self.target.holes
        ]
        # Add derived metrics for downstream consumers.
        d["derived"] = {
            "placed_count": self.placed_count,
            "rejected_count": self.rejected_count,
            "placed_area_m2": round(self.placed_area_m2, 4),
            "coverage_percentage": round(self.coverage_percentage, 2),
            "total_seam_length_mm": round(self.total_seam_length_mm, 2),
        }
        return d


def write_placement_json(view: PlacementView, path: str | Path) -> Path:
    """Serialize a `PlacementView` to a JSON file. Returns the written path."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(view.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return p


# ---------------------------------------------------------------------------
# Adapters — packer result → PlacementView. Free functions so we don't
# need to touch the packer modules.
# ---------------------------------------------------------------------------


def _target_view_from_area(target: TargetArea) -> TargetView:
    """Build a TargetView from a rectangular `TargetArea`."""
    w, h = target.width_mm, target.height_mm
    boundary = [(0.0, 0.0), (w, 0.0), (w, h), (0.0, h)]
    return TargetView(
        target_id=target.target_id,
        name=target.name,
        boundary=boundary,
        holes=[],
        bbox=(0.0, 0.0, w, h),
        boundary_area_m2=w * h / 1_000_000.0,
        holes_area_m2=0.0,
        usable_area_m2=w * h / 1_000_000.0,
        source_dxf_path=None,
    )


def _target_view_from_geometry(geom: TargetGeometry) -> TargetView:
    """Build a TargetView from a DXF `TargetGeometry`."""
    return TargetView(
        target_id=geom.target_id,
        name=geom.name,
        boundary=[(x, y) for x, y in geom.boundary],
        holes=[[(x, y) for x, y in hole] for hole in geom.holes],
        bbox=tuple(geom.bbox),  # type: ignore[arg-type]
        boundary_area_m2=geom.boundary_area_m2,
        holes_area_m2=geom.holes_area_m2,
        usable_area_m2=geom.usable_area_m2,
        source_dxf_path=str(geom.source_dxf_path) if geom.source_dxf_path else None,
    )


def _resolve_image(
    image_path: Path | None,
    image_available: bool,
    processed_image_path: Path | None,
) -> tuple[str | None, ImageSource]:
    """Pick which photo to show + label its source.

    Matches the rule the CLIs already used: processed crop wins over
    the raw photo wins over a placeholder.
    """
    if processed_image_path is not None and processed_image_path.exists():
        return str(processed_image_path), "processed"
    if image_available and image_path is not None and image_path.exists():
        return str(image_path), "original"
    return None, "placeholder"


def _placements_from_iterable(
    raw_placements,
    *,
    has_processed: bool = True,
) -> list[PlacedSlabView]:
    """Build PlacedSlabView list, assigning 1-based display indices."""
    out: list[PlacedSlabView] = []
    for i, p in enumerate(raw_placements, start=1):
        processed = (
            getattr(p, "processed_image_path", None) if has_processed else None
        )
        path, source = _resolve_image(
            getattr(p, "image_path", None),
            getattr(p, "image_available", False),
            processed,
        )
        out.append(PlacedSlabView(
            slab_id=p.slab_id,
            display_index=i,
            x_mm=float(p.x),
            y_mm=float(p.y),
            width_mm=float(p.width_mm),
            height_mm=float(p.height_mm),
            image_path=path,
            image_source=source,
        ))
    return out


# Each adapter delegates seam detection to keep the schema module pure
# (no algorithmic logic here). We import lazily to avoid a circular dep
# between schema.py and seam_detect.py.

def _detect(placements: list[PlacedSlabView]) -> list[SeamView]:
    from placement_engine.preview.seam_detect import detect_seams
    return detect_seams(placements)


def view_from_shelf_pack_result(
    result: ShelfPackResult,
    *,
    metadata: dict[str, Any] | None = None,
) -> PlacementView:
    """Adapter: ``ShelfPackResult`` → ``PlacementView``.

    Shelf-pack works against a rectangular ``TargetArea``; its overflow
    list becomes ``rejected`` entries with reason ``"overflow"``.
    """
    placements = _placements_from_iterable(result.placements)
    rejected = [
        RejectedSlabView(
            slab_id=s.slab_id,
            width_mm=float(s.width_mm),
            height_mm=float(s.height_mm),
            reason="overflow",
        )
        for s in result.overflow
    ]
    meta = {"packer": "shelf_pack", "smoke_mode": "bbox_only"}
    if metadata:
        meta.update(metadata)
    return PlacementView(
        target=_target_view_from_area(result.target),
        placements=placements,
        seams=_detect(placements),
        rejected=rejected,
        metadata=meta,
    )


def view_from_polygon_pack_result(
    result: PolygonPackResult,
    *,
    metadata: dict[str, Any] | None = None,
) -> PlacementView:
    """Adapter: ``PolygonPackResult`` → ``PlacementView``."""
    placements = _placements_from_iterable(result.placements)
    rejected = [
        RejectedSlabView(
            slab_id=r.slab_id,
            width_mm=float(r.width_mm),
            height_mm=float(r.height_mm),
            reason=r.reason,
            attempted_x_mm=r.attempted_x,
            attempted_y_mm=r.attempted_y,
        )
        for r in result.rejected
    ]
    meta = {"packer": "polygon_pack"}
    if metadata:
        meta.update(metadata)
    return PlacementView(
        target=_target_view_from_geometry(result.target),
        placements=placements,
        seams=_detect(placements),
        rejected=rejected,
        metadata=meta,
    )


def view_from_blf_pack_result(
    result: BLFPackResult,
    *,
    metadata: dict[str, Any] | None = None,
) -> PlacementView:
    """Adapter: ``BLFPackResult`` → ``PlacementView``."""
    placements = _placements_from_iterable(result.placements)
    rejected = [
        RejectedSlabView(
            slab_id=r.slab_id,
            width_mm=float(r.width_mm),
            height_mm=float(r.height_mm),
            reason=r.reason,
        )
        for r in result.rejected
    ]
    meta = {
        "packer": "blf_pack",
        "grid_step_mm": result.grid_step_mm,
        "runtime_seconds": round(result.runtime_seconds, 4),
    }
    if metadata:
        meta.update(metadata)
    return PlacementView(
        target=_target_view_from_geometry(result.target),
        placements=placements,
        seams=_detect(placements),
        rejected=rejected,
        metadata=meta,
    )
