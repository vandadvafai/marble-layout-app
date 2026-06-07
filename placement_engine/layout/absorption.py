"""Sliver absorption — fold sub-cuttable strips into their neighbours.

After the zoning + anchor-selection layers, exterior boundaries can
still produce sub-cuttable slivers (a 30 mm column against a wall, a
20 mm strip on the floor's right edge). A fabricator can't safely cut
a 30 mm strip of marble — it shatters. The designer's hand-fix is to
absorb that sliver into the adjacent slab-sized piece, turning a
1590 mm full tile + 30 mm sliver into one 1620 mm wide edge piece.
This module performs that absorption automatically.

V1 rules:

* Only **rectangle ↔ rectangle** merges are performed. Both the sliver
  and the proposed neighbour must be simple axis-aligned rectangles
  (4-corner polygons, no interior holes). This guarantees the union is
  a clean rectangle and avoids accidentally producing L-shapes or
  pieces with cut-outs.
* Sliver + neighbour must share a **full** edge (the neighbour's edge
  facing the sliver is exactly co-located with the sliver's facing
  edge). Anything less and the union would be non-rectangular.
* The pair must live in the same zone — never merge across the
  architectural step lines that zoning carved out in the first place.
* Never merge sliver-into-sliver. The receiving piece must be a real
  cuttable rectangle so the absorbed shape is also cuttable.
* Each absorption gets traced via a ``absorbed_sliver:<id>`` note on
  the receiving piece; the sliver itself is dropped from ``pieces``.

Out of scope (deferred to a later milestone):
  * absorbing slivers into non-rectangular edge pieces (e.g. pieces
    clipped by an L-shaped boundary)
  * absorbing slivers into pieces with interior holes
  * inventory-aware absorption (declining a merge when the result
    can't be cut from any available slab)
  * cascading absorptions (a merged piece becoming the sliver of its
    own neighbour). The current loop only catches absorptions where
    the receiving piece never itself becomes a sliver candidate —
    sufficient for every demo fixture.
"""

from __future__ import annotations

from dataclasses import dataclass

from placement_engine.layout.anchoring import SliverPolicy
from placement_engine.layout.schema import LayoutResult, Piece

# Coordinate tolerance for "edges coincide" checks. Sub-mm slop is
# common after Shapely clipping, so a tenth-mm threshold is safe and
# well below any real cut tolerance.
_EDGE_TOL_MM: float = 0.5

# Area-comparison tolerance — used to confirm the actual piece area
# matches the bbox area (i.e. the piece really is a clean rectangle).
_AREA_TOL_M2: float = 1e-3


@dataclass
class _Rect:
    """Internal bbox helper. Mutable so callers can stash one per
    piece without recomputing from the polygon every check."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    @property
    def area_m2(self) -> float:
        return (self.w * self.h) / 1_000_000.0


def absorb_slivers(
    layout: LayoutResult, *, policy: SliverPolicy,
) -> LayoutResult:
    """Walk the layout's pieces; absorb every sub-cuttable sliver that
    has a same-zone rectangular neighbour sharing a full edge.

    Returns the same ``LayoutResult`` with ``pieces`` updated in place
    (absorbed slivers removed; receiving pieces re-stamped). Zone
    ``piece_count`` is also refreshed so downstream consumers still
    see consistent metadata.

    Idempotent on layouts with no absorbable slivers.
    """
    if not layout.pieces:
        return layout

    pieces = list(layout.pieces)
    absorbed_ids: set[str] = set()

    # Iterate until a full pass finds nothing more to absorb. In
    # practice one pass is enough for every V1 fixture (slivers and
    # their full-tile neighbours don't overlap each other) but the
    # loop is cheap and guards against future zone shapes.
    changed = True
    while changed:
        changed = False
        # Sort sliver candidates by area ascending — smallest first.
        # Smallest slivers are the most urgent to absorb and tend to
        # have only one good neighbour, so picking them first minimises
        # the chance of stealing a neighbour another sliver might
        # need.
        sliver_indices = [
            i for i, p in enumerate(pieces)
            if p.piece_id not in absorbed_ids
            and _is_undersized(p, policy)
            and _is_simple_rectangle(p)
        ]
        sliver_indices.sort(key=lambda i: pieces[i].actual_area_m2)
        for sliver_idx in sliver_indices:
            sliver = pieces[sliver_idx]
            if sliver.piece_id in absorbed_ids:
                continue  # already absorbed in this pass
            nb_idx = _best_neighbour(pieces, sliver_idx, absorbed_ids, policy)
            if nb_idx is None:
                continue
            pieces[nb_idx] = _merge(sliver, pieces[nb_idx])
            absorbed_ids.add(sliver.piece_id)
            changed = True

    layout.pieces = [p for p in pieces if p.piece_id not in absorbed_ids]
    # Refresh per-zone piece counts so JSON metadata stays consistent.
    by_zone: dict[str, int] = {}
    for p in layout.pieces:
        by_zone[p.zone_id] = by_zone.get(p.zone_id, 0) + 1
    for z in layout.zones:
        z.piece_count = by_zone.get(z.zone_id, 0)
    return layout


# ---------------------------------------------------------------------------
# candidate selection
# ---------------------------------------------------------------------------


def _best_neighbour(
    pieces: list[Piece],
    sliver_idx: int,
    absorbed_ids: set[str],
    policy: SliverPolicy,
) -> int | None:
    """Return the index of the best mergeable neighbour, or None."""
    sliver = pieces[sliver_idx]
    best_idx: int | None = None
    best_edge: float = 0.0
    for nb_idx, nb in enumerate(pieces):
        if nb_idx == sliver_idx or nb.piece_id in absorbed_ids:
            continue
        if nb.zone_id != sliver.zone_id:
            continue
        # Never merge sliver-into-sliver; the receiving piece must
        # itself be a real cuttable rectangle.
        if _is_undersized(nb, policy):
            continue
        if not _is_simple_rectangle(nb):
            continue
        if not _shares_full_edge(sliver, nb):
            continue
        # Longer shared edge → better merge candidate. For rectangles
        # sharing a full edge this equals the sliver's facing edge
        # length, but compute defensively in case future merge rules
        # accept partial shares.
        edge_len = _shared_edge_length(sliver, nb)
        if edge_len > best_edge:
            best_edge = edge_len
            best_idx = nb_idx
    return best_idx


# ---------------------------------------------------------------------------
# geometric predicates
# ---------------------------------------------------------------------------


def _is_undersized(piece: Piece, policy: SliverPolicy) -> bool:
    """A piece is undersized if it's already flagged ``sliver`` by the
    layout layer OR if either of its bbox dimensions falls below the
    policy's minimum cuttable side."""
    if "sliver" in piece.notes:
        return True
    if piece.bounding_width_mm < policy.min_sliver_width_mm:
        return True
    if piece.bounding_height_mm < policy.min_sliver_height_mm:
        return True
    return False


def _piece_bbox(piece: Piece) -> _Rect:
    xs = [x for x, _ in piece.actual_cut_polygon]
    ys = [y for _, y in piece.actual_cut_polygon]
    return _Rect(min(xs), min(ys), max(xs), max(ys))


def _is_simple_rectangle(piece: Piece) -> bool:
    """True iff piece's exterior polygon is a 4-corner axis-aligned
    rectangle with no interior holes."""
    if piece.interior_holes:
        return False
    ring = piece.actual_cut_polygon
    # Accept both closed (5 vertices, last == first) and open (4) rings.
    if len(ring) == 5:
        if ring[0] != ring[-1]:
            return False
        corners = ring[:-1]
    elif len(ring) == 4:
        corners = ring
    else:
        return False
    bbox = _piece_bbox(piece)
    expected = {
        (round(bbox.x0, 3), round(bbox.y0, 3)),
        (round(bbox.x1, 3), round(bbox.y0, 3)),
        (round(bbox.x1, 3), round(bbox.y1, 3)),
        (round(bbox.x0, 3), round(bbox.y1, 3)),
    }
    actual = {(round(x, 3), round(y, 3)) for x, y in corners}
    if actual != expected:
        return False
    # Confirm the piece's recorded area matches the bbox (the polygon
    # could have the right corners but list them in a self-intersecting
    # order — unlikely but cheap to defend against).
    if abs(piece.actual_area_m2 - bbox.area_m2) > _AREA_TOL_M2:
        return False
    return True


def _shared_edge_length(a: Piece, b: Piece) -> float:
    """Length of any axis-aligned edge segment shared by a and b's
    bboxes. Zero when they don't touch along an edge."""
    ra, rb = _piece_bbox(a), _piece_bbox(b)
    if abs(ra.x1 - rb.x0) < _EDGE_TOL_MM or abs(ra.x0 - rb.x1) < _EDGE_TOL_MM:
        y_lo = max(ra.y0, rb.y0)
        y_hi = min(ra.y1, rb.y1)
        if y_hi - y_lo > _EDGE_TOL_MM:
            return y_hi - y_lo
    if abs(ra.y1 - rb.y0) < _EDGE_TOL_MM or abs(ra.y0 - rb.y1) < _EDGE_TOL_MM:
        x_lo = max(ra.x0, rb.x0)
        x_hi = min(ra.x1, rb.x1)
        if x_hi - x_lo > _EDGE_TOL_MM:
            return x_hi - x_lo
    return 0.0


def _shares_full_edge(sliver: Piece, nb: Piece) -> bool:
    """True iff the sliver's edge facing nb exactly matches the
    corresponding edge of nb. Guarantees the union is a rectangle.
    """
    ra, rb = _piece_bbox(sliver), _piece_bbox(nb)
    # Vertical share: same y range
    if abs(ra.x1 - rb.x0) < _EDGE_TOL_MM or abs(ra.x0 - rb.x1) < _EDGE_TOL_MM:
        if (
            abs(ra.y0 - rb.y0) < _EDGE_TOL_MM
            and abs(ra.y1 - rb.y1) < _EDGE_TOL_MM
        ):
            return True
    # Horizontal share: same x range
    if abs(ra.y1 - rb.y0) < _EDGE_TOL_MM or abs(ra.y0 - rb.y1) < _EDGE_TOL_MM:
        if (
            abs(ra.x0 - rb.x0) < _EDGE_TOL_MM
            and abs(ra.x1 - rb.x1) < _EDGE_TOL_MM
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------


def _merge(sliver: Piece, nb: Piece) -> Piece:
    """Build the post-merge Piece, taking the neighbour's identity but
    the union's geometry.

    Identity (piece_id, row, col, zone_id, nominal_*) tracks the
    *neighbour* — the receiving piece keeps its name so downstream
    diffs read naturally as "the same piece, now bigger". A trace of
    the absorbed sliver lives in the notes list.
    """
    ra, rb = _piece_bbox(sliver), _piece_bbox(nb)
    x0, y0 = min(ra.x0, rb.x0), min(ra.y0, rb.y0)
    x1, y1 = max(ra.x1, rb.x1), max(ra.y1, rb.y1)
    merged_polygon = [
        (x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0),
    ]
    merged_w = x1 - x0
    merged_h = y1 - y0
    merged_area_m2 = (merged_w * merged_h) / 1_000_000.0
    # Carry over neighbour's notes except ``sliver`` (which can't apply
    # to the merged piece — it's bigger than nominal). Append a trace
    # entry naming the absorbed sliver.
    notes = [n for n in nb.notes if n != "sliver"]
    notes.append(f"absorbed_sliver:{sliver.piece_id}")
    return Piece(
        piece_id=nb.piece_id,
        row=nb.row, col=nb.col,
        nominal_x_mm=nb.nominal_x_mm,
        nominal_y_mm=nb.nominal_y_mm,
        nominal_width_mm=nb.nominal_width_mm,
        nominal_height_mm=nb.nominal_height_mm,
        actual_cut_polygon=merged_polygon,
        bounding_width_mm=merged_w,
        bounding_height_mm=merged_h,
        actual_area_m2=merged_area_m2,
        # Post-merge the piece is no longer the nominal tile size, so
        # it's an edge piece by definition.
        is_full_tile=False,
        is_edge_piece=True,
        intersects_hole=nb.intersects_hole,
        interior_holes=list(nb.interior_holes),
        notes=notes,
        zone_id=nb.zone_id,
    )
