"""Validators that operate on already-parsed geometry."""

from __future__ import annotations

from shapely.geometry import Polygon

from collections import defaultdict

from placement_engine.config import DEFAULT_OVERLAP_TOLERANCE_MM2
from placement_engine.geometry.polygons import coords_to_polygon
from placement_engine.models import Layout, PlacedPiece, Slab


class GeometryValidationError(ValueError):
    """Raised when input geometry violates a structural rule."""


def build_project_polygon(layout: Layout) -> Polygon:
    """Convert a Layout into a single Shapely Polygon and sanity-check it.

    Holes must lie strictly inside the boundary. We rely on Shapely's
    `Polygon(boundary, holes=...)` constructor for the basic ring validity
    and add the containment check ourselves so the error message points at
    the bad hole.
    """
    boundary = coords_to_polygon(layout.boundary)
    if boundary.is_empty or boundary.area <= 0:
        raise GeometryValidationError("project boundary has zero area")

    for i, hole_coords in enumerate(layout.holes):
        hole_poly = coords_to_polygon(hole_coords)
        if not boundary.contains(hole_poly):
            raise GeometryValidationError(
                f"hole[{i}] is not fully inside the project boundary"
            )

    project = coords_to_polygon(layout.boundary, holes=layout.holes)
    if not project.is_valid:
        raise GeometryValidationError("composed project polygon is invalid")
    return project


def assert_pieces_non_overlapping(
    pieces: list[Polygon], tolerance_mm2: float = DEFAULT_OVERLAP_TOLERANCE_MM2
) -> None:
    """Raise if any two placed pieces overlap by more than `tolerance_mm2`.

    O(n^2) is fine at MVP piece counts. Replace with an STRtree if a
    project ever exceeds a few thousand pieces.
    """
    for i, a in enumerate(pieces):
        for j in range(i + 1, len(pieces)):
            inter = a.intersection(pieces[j])
            if not inter.is_empty and inter.area > tolerance_mm2:
                raise GeometryValidationError(
                    f"pieces {i} and {j} overlap by {inter.area:.2f} mm² "
                    f"(tolerance {tolerance_mm2} mm²)"
                )


def assert_pieces_inside(pieces: list[Polygon], project: Polygon, tolerance_mm2: float = DEFAULT_OVERLAP_TOLERANCE_MM2) -> None:
    """Raise if any piece extends outside the usable project area."""
    for i, piece in enumerate(pieces):
        outside = piece.difference(project)
        if not outside.is_empty and outside.area > tolerance_mm2:
            raise GeometryValidationError(
                f"piece {i} extends {outside.area:.2f} mm² outside the project area"
            )


def assert_pieces_within_slab_bounds(
    pieces: list[PlacedPiece],
    slabs: list[Slab],
    tolerance_mm: float = 0.5,
) -> None:
    """Every `slab_polygon` must lie inside its source slab rectangle.

    We check coordinate ranges rather than constructing a Shapely polygon
    per slab: it's faster, and offcut sub-polygons are always axis-aligned
    so a coordinate range check is exact.
    """
    slab_lookup = {s.slab_id: s for s in slabs}
    for i, piece in enumerate(pieces):
        slab = slab_lookup.get(piece.source_slab_id or piece.slab_id)
        if slab is None:
            raise GeometryValidationError(
                f"piece {piece.piece_id!r} references unknown slab "
                f"{piece.slab_id!r}"
            )
        for x, y in piece.slab_polygon:
            if x < -tolerance_mm or x > slab.width + tolerance_mm:
                raise GeometryValidationError(
                    f"piece {piece.piece_id!r} slab_polygon x={x} is outside "
                    f"slab '{slab.slab_id}' [0, {slab.width}]"
                )
            if y < -tolerance_mm or y > slab.height + tolerance_mm:
                raise GeometryValidationError(
                    f"piece {piece.piece_id!r} slab_polygon y={y} is outside "
                    f"slab '{slab.slab_id}' [0, {slab.height}]"
                )


def assert_no_slab_local_overlaps(
    pieces: list[PlacedPiece],
    tolerance_mm2: float = DEFAULT_OVERLAP_TOLERANCE_MM2,
) -> None:
    """Pieces cut from the same physical slab must not overlap in
    slab-local coordinates — i.e. the engine must never claim the same
    chunk of marble was used twice.
    """
    by_slab: dict[str, list[PlacedPiece]] = defaultdict(list)
    for piece in pieces:
        sid = piece.source_slab_id or piece.slab_id
        by_slab[sid].append(piece)
    for slab_id, group in by_slab.items():
        polys = [coords_to_polygon(p.slab_polygon) for p in group]
        for i in range(len(polys)):
            for j in range(i + 1, len(polys)):
                inter = polys[i].intersection(polys[j])
                if not inter.is_empty and inter.area > tolerance_mm2:
                    raise GeometryValidationError(
                        f"pieces {group[i].piece_id!r} and {group[j].piece_id!r} "
                        f"overlap in slab '{slab_id}' coordinates by "
                        f"{inter.area:.2f} mm²"
                    )
