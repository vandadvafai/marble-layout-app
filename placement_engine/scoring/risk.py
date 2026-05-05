"""Per-piece risk evaluation.

Runs after the strategy has produced a layout. Each piece is checked
against the soft warning thresholds in `Rules.risk_thresholds`. Pieces
that breach a threshold receive `RiskFlag` entries; the engine then
builds a `piece_risk` `ReviewMarker` per flagged piece so the designer
sees them at a glance.

Hard-drop filtering (`Rules.min_piece_*`) happens upstream in the
strategy. By the time `evaluate_piece` runs, every piece is geometrically
valid and inside the project area — risk flags are advisory only.
"""

from __future__ import annotations

from shapely.geometry import Polygon

from placement_engine.models import (
    PlacedPiece,
    ReviewMarker,
    RiskFlag,
    RiskThresholds,
)


def _piece_bbox(piece: PlacedPiece) -> tuple[float, float, float, float]:
    """Return the piece's project-space bounding box (min/max x and y)."""
    poly = Polygon(piece.project_polygon)
    return poly.bounds


def evaluate_piece(piece: PlacedPiece, thresholds: RiskThresholds) -> list[RiskFlag]:
    """Return all soft-warning flags that apply to this piece.

    Multiple flags can apply to the same piece (a 60 mm × 1500 mm sliver
    is both narrow and high aspect ratio). Order is fixed to keep the
    output deterministic.
    """
    poly = Polygon(piece.project_polygon)
    minx, miny, maxx, maxy = poly.bounds
    width = maxx - minx
    height = maxy - miny
    area = poly.area

    flags: list[RiskFlag] = []

    if area < thresholds.min_piece_area:
        flags.append(RiskFlag(
            type="small_piece",
            severity="medium",
            message=(
                f"piece area {area:.0f} mm² is below the comfort threshold "
                f"of {thresholds.min_piece_area:.0f} mm²"
            ),
        ))

    if width < thresholds.min_piece_width:
        flags.append(RiskFlag(
            type="narrow_piece",
            severity="medium",
            message=(
                f"piece width {width:.0f} mm is below the comfort threshold "
                f"of {thresholds.min_piece_width:.0f} mm"
            ),
        ))

    if height < thresholds.min_piece_height:
        flags.append(RiskFlag(
            type="short_piece",
            severity="medium",
            message=(
                f"piece height {height:.0f} mm is below the comfort threshold "
                f"of {thresholds.min_piece_height:.0f} mm"
            ),
        ))

    # Aspect ratio is taken as max(w/h, h/w) so a single threshold catches
    # both long-and-thin and short-and-wide. Only meaningful when both
    # dimensions are positive.
    if width > 0 and height > 0:
        ratio = max(width / height, height / width)
        if ratio > thresholds.max_aspect_ratio:
            flags.append(RiskFlag(
                type="thin_aspect_ratio",
                severity="low",
                message=(
                    f"piece aspect ratio {ratio:.1f} exceeds the comfort "
                    f"threshold of {thresholds.max_aspect_ratio:.1f}"
                ),
            ))

    # A clean rectangle has 4 vertices. Anything significantly above
    # suggests the piece was clipped around a hole or an irregular
    # boundary and is no longer a simple cut.
    vertex_count = len(piece.project_polygon)
    if vertex_count > thresholds.max_vertex_count:
        flags.append(RiskFlag(
            type="irregular_piece",
            severity="low",
            message=(
                f"piece has {vertex_count} vertices "
                f"(threshold {thresholds.max_vertex_count}); "
                "non-rectangular cuts are harder to fabricate cleanly"
            ),
        ))

    return flags


def annotate_pieces_with_risks(
    pieces: list[PlacedPiece], thresholds: RiskThresholds
) -> int:
    """Mutate each piece in `pieces` to attach its risk flags.

    Returns the number of pieces that picked up at least one flag.
    """
    flagged = 0
    for piece in pieces:
        piece.risk_flags = evaluate_piece(piece, thresholds)
        if piece.risk_flags:
            flagged += 1
    return flagged


def _highest_severity(flags: list[RiskFlag]) -> str:
    """Pick the worst severity present in a flag list."""
    order = {"low": 0, "medium": 1, "high": 2}
    return max((f.severity for f in flags), key=lambda s: order[s])


def build_risk_review_markers(pieces: list[PlacedPiece]) -> list[ReviewMarker]:
    """Build one `piece_risk` marker per flagged piece.

    `review_id` is left as a placeholder; the engine renumbers all
    markers (strategy markers + risk markers) into a single sequence
    after collection so IDs don't collide.
    """
    markers: list[ReviewMarker] = []
    for piece in pieces:
        if not piece.risk_flags:
            continue
        centroid = Polygon(piece.project_polygon).centroid
        flag_types = ", ".join(f.type for f in piece.risk_flags)
        markers.append(ReviewMarker(
            review_id="R000",  # rewritten by engine
            type="piece_risk",
            location=(float(centroid.x), float(centroid.y)),
            related_piece_ids=[piece.piece_id],
            severity=_highest_severity(piece.risk_flags),
            message=f"Piece {piece.piece_id} flagged for review: {flag_types}",
        ))
    return markers
