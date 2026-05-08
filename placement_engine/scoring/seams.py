"""Seam detection between adjacent placed pieces.

A *seam* is the shared boundary segment between two pieces that touch
along a real line. Pieces that meet only at a corner — or are
separated entirely (e.g. by a project hole) — do not produce a seam.

Algorithm:
  For every pair of placed pieces, intersect their `boundary`
  LineStrings. The result is one of:
    - empty                          → no contact, skip
    - Point / MultiPoint             → corner-only contact, skip
    - LineString                     → one seam
    - MultiLineString                → one seam per disjoint segment
    - GeometryCollection             → flatten and apply the above

  Each surviving LineString whose length exceeds the configured
  `seam_tolerance` becomes a `Seam` model.

Complexity is O(n²) over placed pieces. Fine at MVP piece counts; an
STRtree can replace the outer pair scan once layouts grow into the
thousands. Pair order is stable (input piece order), so seam IDs and
the output JSON are deterministic.
"""

from __future__ import annotations

from collections.abc import Iterable

from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    Polygon,
)
from shapely.geometry.base import BaseGeometry

from placement_engine.config import LENGTH_EPSILON_MM
from placement_engine.models import PlacedPiece, Seam
from placement_engine.utils.ids import IdSequence


def _extract_linestrings(geom: BaseGeometry) -> list[LineString]:
    """Flatten a Shapely intersection result to a list of LineStrings.

    Drops Points / MultiPoints (corner-only contact), recurses into
    GeometryCollection, and returns nothing for empty or polygonal
    inputs (boundary intersections should never produce polygons).
    """
    if geom.is_empty:
        return []
    if isinstance(geom, LineString):
        return [geom]
    if isinstance(geom, MultiLineString):
        return list(geom.geoms)
    if isinstance(geom, GeometryCollection):
        out: list[LineString] = []
        for part in geom.geoms:
            out.extend(_extract_linestrings(part))
        return out
    # Point, MultiPoint, Polygon, etc. — not a seam.
    return []


def _line_to_coords(line: LineString) -> list[tuple[float, float]]:
    return [(float(x), float(y)) for x, y in line.coords]


def detect_seams(
    pieces: Iterable[PlacedPiece], tolerance: float = LENGTH_EPSILON_MM
) -> list[Seam]:
    """Return all seams between pairs of placed pieces.

    `tolerance` is the minimum seam length in millimetres; intersections
    shorter than this are treated as floating-point artifacts and
    discarded. Pass `Rules.seam_tolerance` from the engine.
    """
    pieces_list = list(pieces)
    polys = [Polygon(p.project_polygon) for p in pieces_list]
    seam_ids = IdSequence("SM")
    seams: list[Seam] = []

    for i in range(len(pieces_list)):
        a_piece, a_poly = pieces_list[i], polys[i]
        a_boundary = a_poly.boundary
        for j in range(i + 1, len(pieces_list)):
            b_piece, b_poly = pieces_list[j], polys[j]
            # Cheap reject: bounding boxes that don't even touch can't
            # share a boundary segment.
            if not a_poly.envelope.intersects(b_poly.envelope):
                continue
            shared = a_boundary.intersection(b_poly.boundary)
            for line in _extract_linestrings(shared):
                length = float(line.length)
                if length <= tolerance:
                    continue
                seams.append(Seam(
                    seam_id=seam_ids.next(),
                    piece_ids=[a_piece.piece_id, b_piece.piece_id],
                    line=_line_to_coords(line),
                    length=length,
                    visibility="medium",
                ))
    return seams


def total_seam_length(seams: Iterable[Seam]) -> float:
    """Convenience: sum of seam lengths in mm."""
    return float(sum(s.length for s in seams))
