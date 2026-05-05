"""Conversion helpers between the JSON coordinate format and Shapely.

The JSON schema represents a polygon as a list of `[x, y]` points with the
ring implicitly closed. Shapely uses explicit closed rings inside `Polygon`
objects. Keep all conversions in one place so the rest of the engine never
touches Shapely-vs-JSON impedance directly.
"""

from __future__ import annotations

from collections.abc import Iterable

from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from placement_engine.models import PolygonCoords


def coords_to_polygon(
    boundary: PolygonCoords, holes: Iterable[PolygonCoords] | None = None
) -> Polygon:
    """Build a Shapely Polygon from JSON-style coordinates."""
    holes_list = list(holes) if holes else []
    poly = Polygon(boundary, holes=holes_list)
    if not poly.is_valid:
        # Common cause: self-intersecting boundary or hole. Surface the reason.
        raise ValueError(f"invalid polygon: {poly.is_valid_reason if hasattr(poly, 'is_valid_reason') else 'self-intersecting or degenerate'}")
    return poly


def polygon_to_coords(geom: BaseGeometry) -> PolygonCoords:
    """Extract the exterior ring of a Polygon as JSON-style coordinates.

    Shapely closes the ring by repeating the first point; we drop the
    repeat so the JSON schema matches the input format.
    """
    if isinstance(geom, MultiPolygon):
        raise ValueError(
            "expected a single Polygon; got MultiPolygon. "
            "Caller must split MultiPolygon results before serialising."
        )
    if not isinstance(geom, Polygon):
        raise TypeError(f"expected Polygon, got {type(geom).__name__}")
    coords = list(geom.exterior.coords)
    if len(coords) >= 2 and coords[0] == coords[-1]:
        coords = coords[:-1]
    return [(float(x), float(y)) for x, y in coords]


def rectangle(x: float, y: float, width: float, height: float) -> Polygon:
    """Axis-aligned rectangle with bottom-left at (x, y)."""
    if width <= 0 or height <= 0:
        raise ValueError(f"rectangle dimensions must be positive (got {width}x{height})")
    return Polygon(
        [
            (x, y),
            (x + width, y),
            (x + width, y + height),
            (x, y + height),
        ]
    )


def bbox_dimensions(geom: BaseGeometry) -> tuple[float, float, float, float]:
    """Return (min_x, min_y, max_x, max_y) of a geometry."""
    minx, miny, maxx, maxy = geom.bounds
    return float(minx), float(miny), float(maxx), float(maxy)
