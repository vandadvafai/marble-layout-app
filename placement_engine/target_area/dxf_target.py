"""Bridge between the existing `cad_intake` DXF reader and the V1
TargetArea smoke packer.

The DXF carries richer geometry than a single rectangle can represent
(closed boundary polygon, zero or more holes). For the V1 smoke
placement we keep `TargetArea` rectangle-only and add this
`TargetGeometry` next to it. `TargetGeometry.as_bounding_target_area()`
returns the bbox as a regular `TargetArea` so the smoke packer can run
unchanged.

This module does NOT clip slabs against the irregular boundary. The
smoke preview overlays the boundary and holes on top of a bbox-only
packing — clearly labelled as a smoke test, not a real strategy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from shapely.geometry import Polygon as ShPolygon

from placement_engine.cad_intake.dxf_reader import read_dxf
from placement_engine.cad_intake.input_builder import _extract_boundary_and_holes
from placement_engine.target_area.model import TargetArea

# Polygon coords are list of (x, y) tuples in mm — matches the
# convention the rest of the engine already uses.
Point = tuple[float, float]
PolygonCoords = list[Point]


@dataclass
class TargetGeometry:
    """A DXF-derived target: boundary polygon + holes, in mm at origin (0,0)."""

    target_id: str
    name: str
    boundary: PolygonCoords
    holes: list[PolygonCoords] = field(default_factory=list)
    source_dxf_path: Path | None = None
    # Original (pre-normalization) bbox from the DXF file, so designers
    # can map back to the original drawing coordinates if needed.
    source_bbox: tuple[float, float, float, float] | None = None

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """``(xmin, ymin, xmax, ymax)`` of the boundary after normalization.

        Loaders normalize so ``xmin == ymin == 0``; this property exposes
        the same shape for already-constructed instances regardless of
        whether the caller normalized.
        """
        xs = [p[0] for p in self.boundary]
        ys = [p[1] for p in self.boundary]
        return (min(xs), min(ys), max(xs), max(ys))

    @property
    def width_mm(self) -> float:
        x0, _, x1, _ = self.bbox
        return x1 - x0

    @property
    def height_mm(self) -> float:
        _, y0, _, y1 = self.bbox
        return y1 - y0

    @property
    def boundary_area_m2(self) -> float:
        return ShPolygon(self.boundary).area / 1_000_000.0

    @property
    def holes_area_m2(self) -> float:
        return sum(ShPolygon(h).area for h in self.holes) / 1_000_000.0

    @property
    def usable_area_m2(self) -> float:
        """Boundary area minus total hole area (m²)."""
        return self.boundary_area_m2 - self.holes_area_m2

    def as_bounding_target_area(self) -> TargetArea:
        """Adapt to a rectangular `TargetArea` for the V1 smoke packer.

        Slabs placed into the returned rectangle may visually fall over
        holes or outside the irregular boundary — that's the bbox-only
        smoke caveat the preview makes explicit.
        """
        return TargetArea(
            target_id=self.target_id,
            name=f"{self.name} [bbox of DXF boundary]",
            width_mm=self.width_mm,
            height_mm=self.height_mm,
            required_area_m2=self.usable_area_m2,
        )


def _normalize_polygons(
    boundary: PolygonCoords,
    holes: Sequence[PolygonCoords],
) -> tuple[PolygonCoords, list[PolygonCoords], tuple[float, float, float, float]]:
    """Translate so boundary's bottom-left corner sits at (0, 0).

    Returns ``(boundary, holes, source_bbox)`` where ``source_bbox`` is
    the pre-translation extent so callers can map back to the original
    DXF coordinates.
    """
    xs = [p[0] for p in boundary]
    ys = [p[1] for p in boundary]
    xmin, ymin = min(xs), min(ys)
    source_bbox = (xmin, ymin, max(xs), max(ys))
    if xmin == 0 and ymin == 0:
        # Already at origin — return the inputs as-is to avoid
        # allocating new lists.
        return list(boundary), [list(h) for h in holes], source_bbox
    norm_boundary = [(x - xmin, y - ymin) for x, y in boundary]
    norm_holes = [[(x - xmin, y - ymin) for x, y in h] for h in holes]
    return norm_boundary, norm_holes, source_bbox


def _slug(value: str) -> str:
    out = "".join(c if c.isalnum() else "_" for c in value).strip("_")
    return out.lower() or "target"


def load_target_geometry_from_dxf(
    dxf_path: str | Path,
    *,
    target_id: str | None = None,
    name: str | None = None,
) -> TargetGeometry:
    """Read a standardized DXF and return a `TargetGeometry`.

    The boundary is translated so its bottom-left corner lies at
    (0, 0); the original extent is preserved on `source_bbox`. The
    underlying DXF read is the same `cad_intake` path the OLD engine
    pipeline already uses, so this is non-disruptive.
    """
    dxf_path = Path(dxf_path)
    doc = read_dxf(dxf_path)
    boundary, holes = _extract_boundary_and_holes(doc, dxf_path)
    norm_boundary, norm_holes, source_bbox = _normalize_polygons(boundary, holes)

    derived_name = name or dxf_path.stem.replace("_", " ")
    derived_id = target_id or _slug(dxf_path.stem)

    return TargetGeometry(
        target_id=derived_id,
        name=derived_name,
        boundary=norm_boundary,
        holes=norm_holes,
        source_dxf_path=dxf_path,
        source_bbox=source_bbox,
    )
