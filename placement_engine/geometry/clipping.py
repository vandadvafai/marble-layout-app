"""Clip slab rectangles against the project polygon."""

from __future__ import annotations

from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from placement_engine.config import AREA_EPSILON_MM2


def clip_to_project(slab_rect: Polygon, project: Polygon) -> list[Polygon]:
    """Intersect a slab rectangle with the project polygon.

    Returns a list of hole-free Polygon pieces. If the intersection produces
    a polygon that contains an interior ring (because the slab spans a
    project hole), that polygon is split into hole-free sub-polygons by
    cutting along the hole's bounding box. The output schema doesn't
    support polygons with holes — every emitted piece must have a single
    ring — so the split happens here.
    """
    result = slab_rect.intersection(project)
    polys = _flatten_polygons(result)
    out: list[Polygon] = []
    for p in polys:
        out.extend(_split_holes(p))
    return out


def _flatten_polygons(geom: BaseGeometry) -> list[Polygon]:
    if geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom] if geom.area > AREA_EPSILON_MM2 else []
    if isinstance(geom, MultiPolygon):
        return [p for p in geom.geoms if p.area > AREA_EPSILON_MM2]
    if isinstance(geom, GeometryCollection):
        out: list[Polygon] = []
        for part in geom.geoms:
            out.extend(_flatten_polygons(part))
        return out
    # LineString, Point, etc. — slab only touches the boundary, no real piece.
    return []


def _split_holes(poly: Polygon) -> list[Polygon]:
    """If `poly` has interior rings, return disjoint hole-free sub-polygons.

    Strategy: process holes one at a time. For each hole, slice the
    current pieces into four bands relative to the hole's bounding box
    (left of hole, right of hole, below hole within x-range, above hole
    within x-range) by intersecting with each band rectangle separately.
    Each band rectangle is hole-free and disjoint from the others, so
    the resulting pieces are also hole-free and disjoint.

    Sufficient for MVP. Proper polygon partitioning (which would yield
    fewer, larger pieces) can replace this later.
    """
    if not poly.interiors:
        return [poly]

    pieces: list[Polygon] = [poly]
    for ring in poly.interiors:
        hxmin, hymin, hxmax, hymax = ring.bounds
        next_pieces: list[Polygon] = []
        for p in pieces:
            pminx, pminy, pmaxx, pmaxy = p.bounds
            bands = [
                # left of the hole, full piece height
                Polygon([
                    (pminx, pminy), (hxmin, pminy),
                    (hxmin, pmaxy), (pminx, pmaxy),
                ]),
                # right of the hole, full piece height
                Polygon([
                    (hxmax, pminy), (pmaxx, pminy),
                    (pmaxx, pmaxy), (hxmax, pmaxy),
                ]),
                # below the hole, only within hole's x-range
                Polygon([
                    (hxmin, pminy), (hxmax, pminy),
                    (hxmax, hymin), (hxmin, hymin),
                ]),
                # above the hole, only within hole's x-range
                Polygon([
                    (hxmin, hymax), (hxmax, hymax),
                    (hxmax, pmaxy), (hxmin, pmaxy),
                ]),
            ]
            for band in bands:
                if band.area <= AREA_EPSILON_MM2:
                    continue
                next_pieces.extend(_flatten_polygons(p.intersection(band)))
        pieces = next_pieces

    return [p for p in pieces if p.area > AREA_EPSILON_MM2 and not p.interiors]
