"""Seam detection: find the shared edges between adjacent layout pieces.

A "seam" is the visible joint between two pieces on the finished
floor. Geometrically, it's the LineString (or MultiLineString) you
get when two adjacent piece polygons intersect.

This module is intentionally focused: it computes seams from a list
of layout pieces and returns them as a flat list. Rule-evaluation
consumers (rules.py) decide what to do with each seam (does it cross
a doorway? sit near a column? cut diagonally across a high-visibility
zone?).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shapely.geometry import LineString, MultiLineString, Polygon as ShPolygon
from shapely.geometry.base import BaseGeometry

# Sub-mm Shapely artefacts at piece corners can show up as tiny
# segments — drop anything shorter than this when listing seams.
_MIN_SEAM_LENGTH_MM: float = 1.0


@dataclass
class Seam:
    """One contiguous seam between two pieces.

    ``coords`` is a list of ``(x, y)`` points; for the standard
    axis-aligned case it's a 2-point list (the segment endpoints).
    Curved or polyline seams (none in V1, but the type allows them)
    keep their full vertex list.
    """

    seam_id: str
    piece_a_id: str
    piece_b_id: str
    coords: list[tuple[float, float]]
    length_mm: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "seam_id": self.seam_id,
            "piece_a_id": self.piece_a_id,
            "piece_b_id": self.piece_b_id,
            "coords": [list(pt) for pt in self.coords],
            "length_mm": round(self.length_mm, 3),
        }

    @property
    def is_axis_aligned(self) -> bool:
        """Cheap test: the seam is axis-aligned if its bounding box has
        zero extent in one direction. Useful for "vertical vs.
        horizontal seam" classification later."""
        if len(self.coords) < 2:
            return False
        xs = [x for x, _ in self.coords]
        ys = [y for _, y in self.coords]
        return min(xs) == max(xs) or min(ys) == max(ys)


def detect_seams(pieces: list[dict[str, Any]]) -> list[Seam]:
    """Find every seam between any two of the given layout pieces.

    Pieces are passed as plain dicts (matching the layout JSON shape)
    so this module never has to import the layout package — keeps the
    architectural layer fully decoupled from layout internals.

    The pairwise scan is O(N²); fine for V1 demos (≤ 50 pieces). A
    bbox pre-filter is applied to skip pairs that obviously don't
    touch.
    """
    n = len(pieces)
    if n < 2:
        return []

    polygons: list[ShPolygon] = []
    bboxes: list[tuple[float, float, float, float]] = []
    piece_ids: list[str] = []
    for p in pieces:
        ext = p.get("actual_cut_polygon") or []
        ints = p.get("interior_holes") or []
        if len(ext) < 3:
            polygons.append(None)  # type: ignore[arg-type]
            bboxes.append((0, 0, 0, 0))
            piece_ids.append(str(p.get("piece_id", "")))
            continue
        try:
            shp = ShPolygon(ext, ints)
        except Exception:
            shp = None  # malformed polygon; skip pairings cleanly
        polygons.append(shp)  # type: ignore[arg-type]
        bboxes.append(_bbox(ext))
        piece_ids.append(str(p.get("piece_id", "")))

    seams: list[Seam] = []
    seam_counter = 0
    for i in range(n):
        pi = polygons[i]
        if pi is None or not pi.is_valid:
            continue
        bi = bboxes[i]
        for j in range(i + 1, n):
            pj = polygons[j]
            if pj is None or not pj.is_valid:
                continue
            bj = bboxes[j]
            # bbox pre-filter — pieces whose bboxes don't even touch
            # can't share a seam.
            if not _bboxes_touch(bi, bj):
                continue
            inter = pi.intersection(pj)
            for line in _flatten_lines(inter):
                if line.length < _MIN_SEAM_LENGTH_MM:
                    continue
                seam_counter += 1
                seams.append(Seam(
                    seam_id=f"seam_{seam_counter:03d}",
                    piece_a_id=piece_ids[i],
                    piece_b_id=piece_ids[j],
                    coords=[(float(x), float(y)) for x, y in line.coords],
                    length_mm=float(line.length),
                ))
    return seams


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _bbox(
    coords: list[tuple[float, float]],
) -> tuple[float, float, float, float]:
    xs = [pt[0] for pt in coords]
    ys = [pt[1] for pt in coords]
    return (min(xs), min(ys), max(xs), max(ys))


def _bboxes_touch(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
    tol: float = 0.5,
) -> bool:
    """True iff the two bboxes overlap or touch within tolerance."""
    if a[2] < b[0] - tol or b[2] < a[0] - tol:
        return False
    if a[3] < b[1] - tol or b[3] < a[1] - tol:
        return False
    return True


def _flatten_lines(geom: BaseGeometry) -> list[LineString]:
    """Unwrap a possibly-Multi/GeometryCollection result of
    ``Polygon.intersection`` into a list of LineStrings. Points and
    polygons (which can appear from corner-touch / overlap edge cases)
    are dropped — only edge-sharing produces seams.
    """
    if geom.is_empty:
        return []
    if isinstance(geom, LineString):
        return [geom]
    if isinstance(geom, MultiLineString):
        return list(geom.geoms)
    lines: list[LineString] = []
    for g in getattr(geom, "geoms", ()):
        lines.extend(_flatten_lines(g))
    return lines
