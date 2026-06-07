"""Layout decomposition into axis-aligned rectangular zones.

A floor polygon with major step lines (the L-shape's vertical break,
an inverted-L apartment's expansion above a corridor) does NOT want to
be tiled as one continuous grid spanning the bounding box. A bbox-wide
grid bleeds full-tile columns across architectural step lines and
deposits thin slivers wherever the bbox width doesn't divide evenly,
even on edges that have nothing to do with the floor's real geometry.

The fix is a pre-tile decomposition step: split the usable polygon
(boundary - holes) into a set of maximal axis-aligned rectangles
aligned with the polygon's own vertices, then tile each rectangle
independently. Each rectangle becomes a "zone" with its own anchor
selection and its own sliver evaluation.

V1 scope (additive, layout-package-only):

* Only **rectilinear** polygons get decomposed. A polygon with any
  diagonal edge falls back to a single bbox-wide zone — the existing
  pre-zoning behaviour.
* Vertical-slab decomposition: collect every distinct x-coordinate
  from the boundary + holes, slice the usable polygon into vertical
  strips at those x's, then read off each strip's solid y-intervals.
* Horizontal merge pass: consecutive strips with identical y-intervals
  and abutting x-edges fold into one wider rectangle. For the demo
  L-shape this collapses the decomposition from 2 to 2 (nothing to
  merge — different y-intervals) and for a clean rectangle from N
  strips to 1 maximal rectangle.

Out of scope:
  * minimum-rectangle decomposition (NP-hard for polygons with holes)
  * non-rectilinear approximation
  * zone identity propagation back into target_area / DXF export
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from shapely.geometry import MultiPolygon, Polygon as ShPolygon
from shapely.geometry.base import BaseGeometry

# Coordinate tolerance (mm). Two x- or y-coordinates differing by less
# than this are treated as the same vertex. Avoids "1599.9999 ≠ 1600"
# artefacts from Shapely / DXF rounding without masking real geometry.
_COORD_TOL: float = 0.5

# Floor for "real" rectangles in the decomposition. Anything thinner is
# a numerical sliver from Shapely's intersection and gets dropped.
_MIN_RECT_SIDE_MM: float = 1.0


@dataclass
class ExteriorEdges:
    """Which of a zone's 4 axis-aligned edges lie on the parent
    polygon's outer boundary (vs. abut another zone).

    An edge that's exterior is a "safe" place for a leftover sliver to
    land — it touches the floor's real outer boundary where the
    architect already drew an edge. An interior edge sits at a
    zone-to-zone seam; a sliver landing there would be a hairline
    strip between two otherwise-clean rectangles, exactly the
    fabrication / aesthetic problem zoning is meant to avoid.
    """

    left: bool = True
    right: bool = True
    bottom: bool = True
    top: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"left": self.left, "right": self.right,
                "bottom": self.bottom, "top": self.top}


@dataclass
class LayoutZone:
    """One rectangular sub-region of the usable floor.

    A zone is always an axis-aligned rectangle. Its boundary
    (``polygon``) is a closed 5-vertex ring, useful for the renderer
    and for any downstream consumer that wants the zone outline.

    ``interior_holes`` carries any of the floor's interior holes whose
    bbox is fully inside this zone — they're preserved so the per-zone
    tile generator can still clip pieces around columns / vents.

    The anchor + origin + ``candidate_evaluations`` fields are filled
    in by the layout generator after it runs anchor selection on the
    zone. They are ``None`` / empty on a freshly-decomposed zone.
    """

    zone_id: str
    bbox: tuple[float, float, float, float]  # (x0, y0, x1, y1)
    polygon: list[tuple[float, float]]       # closed ring, 5 vertices
    interior_holes: list[list[tuple[float, float]]] = field(default_factory=list)
    # Which edges sit on the parent polygon's outer boundary. Filled
    # in by ``decompose_into_zones``; defaults to all-exterior (the
    # whole-bbox single-zone case).
    exterior_edges: ExteriorEdges = field(default_factory=ExteriorEdges)
    anchor_mode: str | None = None
    origin: tuple[float, float] | None = None
    candidate_evaluations: list = field(default_factory=list)
    piece_count: int = 0

    @property
    def width_mm(self) -> float:
        return self.bbox[2] - self.bbox[0]

    @property
    def height_mm(self) -> float:
        return self.bbox[3] - self.bbox[1]

    @property
    def area_m2(self) -> float:
        return (self.width_mm * self.height_mm) / 1_000_000.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "zone_id": self.zone_id,
            "bbox": list(self.bbox),
            "polygon": [list(pt) for pt in self.polygon],
            "interior_holes": [
                [list(pt) for pt in ring] for ring in self.interior_holes
            ],
            "width_mm": self.width_mm,
            "height_mm": self.height_mm,
            "area_m2": round(self.area_m2, 4),
            "anchor_mode": self.anchor_mode,
            "origin": list(self.origin) if self.origin is not None else None,
            "candidate_evaluations": [
                ev.to_dict() for ev in self.candidate_evaluations
            ],
            "exterior_edges": self.exterior_edges.to_dict(),
            "piece_count": self.piece_count,
        }


def is_rectilinear(geometry: Any) -> bool:
    """True iff every edge of boundary + holes is axis-aligned.

    A rectilinear polygon is the necessary precondition for the
    vertical-slab decomposition: every strip ends up bounded by real
    polygon vertices, so the decomposition produces actual rectangles
    rather than slanted parallelograms.
    """
    if not _ring_is_rectilinear(geometry.boundary):
        return False
    for hole in geometry.holes:
        if not _ring_is_rectilinear(hole):
            return False
    return True


def decompose_into_zones(geometry: Any) -> list[LayoutZone]:
    """Decompose ``geometry`` into a list of axis-aligned rectangles.

    Non-rectilinear polygons fall back to a single zone equal to the
    bbox — preserves the pre-zoning behaviour for any future fixture
    that has diagonal edges.

    Decomposition is driven by **boundary** x-coordinates only — hole
    vertices are *not* considered architectural step lines, so a hole
    inside a clean rectangular room doesn't multiply the zone count.
    Holes that fall inside a zone are carried in
    ``LayoutZone.interior_holes`` so the per-zone tile generator can
    still clip pieces around them.

    The result is sorted bottom-to-top, then left-to-right so the
    zone IDs (``z0``, ``z1``, …) read naturally in the layout JSON.
    """
    bx0, by0, bx1, by1 = geometry.bbox
    if not is_rectilinear(geometry):
        return [_single_bbox_zone(geometry.bbox)]

    # 1. Distinct x-coordinates — boundary only. Holes do not drive
    #    zone splitting (see docstring rationale).
    xs_set: set[float] = set()
    for x, _ in geometry.boundary:
        xs_set.add(_snap(x))
    xs_set.add(_snap(bx0))
    xs_set.add(_snap(bx1))
    xs = sorted(xs_set)

    # 2. Use the boundary polygon directly (no hole punching). This
    #    keeps interior holes inside their containing zone rather than
    #    fragmenting strips into above-and-below-the-hole rectangles.
    boundary_poly = ShPolygon(geometry.boundary)

    # 3. For each strip, intersect with the boundary polygon to get
    #    the vertical span of solid floor in that strip. The result is
    #    a single rectangle per strip (the boundary is rectilinear and
    #    we've split at every boundary x-coord).
    rects: list[tuple[float, float, float, float]] = []
    for i in range(len(xs) - 1):
        x0, x1 = xs[i], xs[i + 1]
        if x1 - x0 < _MIN_RECT_SIDE_MM:
            continue
        strip = ShPolygon([
            (x0, by0 - 1.0), (x1, by0 - 1.0),
            (x1, by1 + 1.0), (x0, by1 + 1.0),
        ])
        inter = boundary_poly.intersection(strip)
        for poly in _flatten_polygons(inter):
            pbx0, pby0, pbx1, pby1 = poly.bounds
            pbx0 = max(pbx0, x0)
            pbx1 = min(pbx1, x1)
            if (pbx1 - pbx0) < _MIN_RECT_SIDE_MM:
                continue
            if (pby1 - pby0) < _MIN_RECT_SIDE_MM:
                continue
            rects.append((pbx0, pby0, pbx1, pby1))

    # 4. Horizontal merge — fold consecutive same-(y0, y1) strips that
    #    abut along x. The L-shape never merges (different y-intervals)
    #    but a plain rectangle collapses from N strips to 1.
    rects = _merge_horizontal(rects)

    # 5. Sort bottom-up then left-to-right and assign zone IDs.
    rects.sort(key=lambda r: (r[1], r[0]))
    zones = [_zone_from_rect(f"z{i}", r) for i, r in enumerate(rects)]

    # 6. Compute exterior-edge classification for each zone. An edge
    #    is interior iff at least one other zone abuts it along that
    #    edge's full extent (or any overlapping segment of it).
    for zone in zones:
        zone.exterior_edges = _compute_exterior_edges(zone, zones)

    # 7. Assign each interior hole to whichever zone contains it.
    #    Containment is by bbox + tiny tolerance — holes that straddle
    #    zone boundaries get assigned to the first zone whose bbox
    #    fully contains the hole's bbox; with the boundary-only
    #    decomposition this matches every real fixture, and the
    #    fallback path leaves the hole on the geometry's overall
    #    boundary (no zone owns it, but the global usable check still
    #    works).
    for hole in geometry.holes:
        hbox = _hole_bbox(hole)
        for zone in zones:
            if _bbox_contains(zone.bbox, hbox):
                zone.interior_holes.append([(float(x), float(y)) for x, y in hole])
                break

    return zones


def _compute_exterior_edges(
    zone: LayoutZone, all_zones: list[LayoutZone],
) -> ExteriorEdges:
    """Return which of zone's 4 edges DO NOT abut another zone.

    A zone edge is interior iff any other zone shares that edge along
    a non-zero overlap. The check is purely geometric (no parent
    polygon reference needed): the union of all zones equals the
    boundary polygon by construction, so anything inside that union
    is the parent's interior.
    """
    zx0, zy0, zx1, zy1 = zone.bbox
    left = right = bottom = top = True
    for other in all_zones:
        if other.zone_id == zone.zone_id:
            continue
        ox0, oy0, ox1, oy1 = other.bbox
        # other.right == self.left and y overlaps
        if abs(ox1 - zx0) < _COORD_TOL and _intervals_overlap(
            zy0, zy1, oy0, oy1,
        ):
            left = False
        # other.left == self.right and y overlaps
        if abs(ox0 - zx1) < _COORD_TOL and _intervals_overlap(
            zy0, zy1, oy0, oy1,
        ):
            right = False
        # other.top == self.bottom and x overlaps
        if abs(oy1 - zy0) < _COORD_TOL and _intervals_overlap(
            zx0, zx1, ox0, ox1,
        ):
            bottom = False
        # other.bottom == self.top and x overlaps
        if abs(oy0 - zy1) < _COORD_TOL and _intervals_overlap(
            zx0, zx1, ox0, ox1,
        ):
            top = False
    return ExteriorEdges(left=left, right=right, bottom=bottom, top=top)


def _intervals_overlap(
    a0: float, a1: float, b0: float, b1: float,
) -> bool:
    """True iff the two closed intervals share a non-zero-length overlap."""
    lo = max(a0, b0)
    hi = min(a1, b1)
    return (hi - lo) > _COORD_TOL


def _hole_bbox(
    hole: list[tuple[float, float]],
) -> tuple[float, float, float, float]:
    xs = [x for x, _ in hole]
    ys = [y for _, y in hole]
    return (min(xs), min(ys), max(xs), max(ys))


def _bbox_contains(
    outer: tuple[float, float, float, float],
    inner: tuple[float, float, float, float],
) -> bool:
    return (
        inner[0] >= outer[0] - _COORD_TOL
        and inner[1] >= outer[1] - _COORD_TOL
        and inner[2] <= outer[2] + _COORD_TOL
        and inner[3] <= outer[3] + _COORD_TOL
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _ring_is_rectilinear(ring: list[tuple[float, float]]) -> bool:
    """True iff every edge in the (open or closed) ring is axis-aligned."""
    n = len(ring)
    if n < 3:
        return False
    # Tolerate both closed (last == first) and open rings.
    closed = (
        abs(ring[0][0] - ring[-1][0]) < _COORD_TOL
        and abs(ring[0][1] - ring[-1][1]) < _COORD_TOL
    )
    edge_count = n - 1 if closed else n
    for i in range(edge_count):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % n]
        dx, dy = abs(x1 - x0), abs(y1 - y0)
        if dx > _COORD_TOL and dy > _COORD_TOL:
            # Both deltas non-zero ⇒ the edge is diagonal.
            return False
    return True


def _snap(v: float) -> float:
    """Round to the coordinate tolerance so near-equal vertices collapse."""
    return round(v / _COORD_TOL) * _COORD_TOL


def _flatten_polygons(geom: BaseGeometry) -> list[ShPolygon]:
    """Unwrap a possibly-Multi geometry into a list of non-empty polygons."""
    if geom.is_empty:
        return []
    if isinstance(geom, ShPolygon):
        return [geom] if geom.area > 0 else []
    if isinstance(geom, MultiPolygon):
        return [g for g in geom.geoms if g.area > 0]
    polys: list[ShPolygon] = []
    for g in getattr(geom, "geoms", ()):
        polys.extend(_flatten_polygons(g))
    return polys


def _merge_horizontal(
    rects: list[tuple[float, float, float, float]],
) -> list[tuple[float, float, float, float]]:
    """Fold horizontally-abutting rectangles that share the same y-range.

    Walk the list in (y0, y1, x0) order; whenever the next rect has
    identical y-range and its left edge equals the current rect's right
    edge, extend the current rect's right edge instead of appending.
    A pass through this is O(n log n) plus O(n); for V1 input sizes
    (the apartment has 8 strips at most) that's plenty.
    """
    if not rects:
        return rects
    sorted_rects = sorted(rects, key=lambda r: (r[1], r[3], r[0]))
    merged: list[tuple[float, float, float, float]] = [sorted_rects[0]]
    for r in sorted_rects[1:]:
        last = merged[-1]
        same_y = (
            abs(last[1] - r[1]) < _COORD_TOL
            and abs(last[3] - r[3]) < _COORD_TOL
        )
        abuts = abs(last[2] - r[0]) < _COORD_TOL
        if same_y and abuts:
            merged[-1] = (last[0], last[1], r[2], last[3])
        else:
            merged.append(r)
    return merged


def _single_bbox_zone(
    bbox: tuple[float, float, float, float],
) -> LayoutZone:
    """The fallback zone for non-rectilinear polygons or trivial bboxes."""
    return _zone_from_rect("z0", bbox)


def _zone_from_rect(
    zone_id: str, bbox: tuple[float, float, float, float],
) -> LayoutZone:
    x0, y0, x1, y1 = bbox
    return LayoutZone(
        zone_id=zone_id,
        bbox=(x0, y0, x1, y1),
        polygon=[(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)],
    )
