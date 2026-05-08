"""Row-based placement: lay slabs left-to-right in horizontal rows.

This is the engine's primary geometric placement loop. The `balanced`
strategy is a thin wrapper around `run_row_based_placement`; the
`lowest_waste` strategy uses the same loop and then runs a second-pass
offcut filler over the placement records it returns.

Algorithm (MVP):
  1. Take the project polygon's bounding box as the placement field.
  2. Walk slabs in inventory order. Maintain two pointers: the cursor
     (current placement position) and the slab index. The cursor always
     advances every iteration; the slab index only advances when a
     placement produces at least one valid clipped piece.
  3. Each slab is placed as an axis-aligned rectangle at the current
     cursor (x, y). Width = slab.width, height = current row height
     (= the height of the first slab in the row).
  4. Clip the rectangle against the project polygon. Each surviving
     sub-polygon becomes a `PlacedPiece` whose `slab_polygon` is the
     same shape translated into the slab's local coordinate frame.
  5. If the placement yields zero valid pieces (the cursor is in a
     notch, off the boundary, or every clip is below min size), emit an
     `empty_slab_placement_skipped` review marker and retry the same
     slab at the next cursor position. The cursor advances; the slab
     index does not.
  6. Advance x by slab.width. When the cursor reaches the bbox right
     edge, start a new row at y += row_height.
  7. Stop when y exceeds the project's max y or the inventory is empty.

Termination is guaranteed because the cursor advances by slab.width
(> 0) every iteration regardless of success, so y eventually exceeds
the project bbox.

Limitations (intentional for MVP):
  - Only rotation 0 is used. `Rules.allowed_rotations` is accepted but
    rotation logic is deferred until the next milestone.
  - Slabs taller than the row are cropped (slab_polygon reflects only
    the used portion). Offcut tracking lives in the `lowest_waste`
    strategy on top of the records this loop emits.
  - No back-tracking, packing, or seam optimisation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from shapely.geometry import Polygon

from placement_engine.geometry.clipping import clip_to_project
from placement_engine.geometry.polygons import (
    bbox_dimensions,
    polygon_to_coords,
    rectangle,
)
from placement_engine.models import (
    PlacedPiece,
    ReviewMarker,
    Rules,
    Slab,
    TextureTransform,
)
from placement_engine.strategies.base import (
    PlacementStrategy,
    StrategyContext,
    StrategyResult,
)
from placement_engine.utils.ids import IdSequence


@dataclass
class PlacementRecord:
    """Per-slab record of what the row loop placed.

    Other strategies (notably `lowest_waste`) use this to compute which
    parts of each slab were *not* used and can be reclaimed as offcuts.
    Not part of the public output schema.
    """

    slab: Slab
    # Project-space origin where the slab rectangle was anchored.
    project_origin: tuple[float, float]
    # Width and height the slab was placed at (height = row_height,
    # which can be ≤ slab.height when the first slab of the row was
    # shorter).
    placed_size: tuple[float, float]
    pieces: list[PlacedPiece] = field(default_factory=list)


def _passes_min_size(piece: Polygon, rules: Rules) -> bool:
    minx, miny, maxx, maxy = bbox_dimensions(piece)
    bbox_w = maxx - minx
    bbox_h = maxy - miny
    if bbox_w < rules.min_piece_width:
        return False
    if bbox_h < rules.min_piece_height:
        return False
    if piece.area < rules.min_piece_area:
        return False
    return True


def _piece_from_clip(
    project_clip: Polygon,
    slab: Slab,
    placed_origin: tuple[float, float],
    piece_id: str,
) -> PlacedPiece:
    """Translate a clipped project-space polygon into slab-local space.

    The placed slab rectangle occupies project-space [(ox, oy), (ox+w, oy+h)].
    The slab's image space starts at (0, 0) and ends at (slab.width, slab.height).
    Slab-local coords for any project-space point (px, py) are simply
    (px - ox, py - oy) — no rotation in MVP.
    """
    ox, oy = placed_origin
    project_coords = polygon_to_coords(project_clip)
    slab_coords = [(px - ox, py - oy) for px, py in project_coords]

    # Texture transform mirrors the slab-local bounding box. Blender will use
    # this to crop the right portion of the slab image.
    sxs = [c[0] for c in slab_coords]
    sys = [c[1] for c in slab_coords]
    uv_origin = (min(sxs), min(sys))
    uv_w = max(sxs) - min(sxs)
    uv_h = max(sys) - min(sys)

    is_full_slab = (
        abs(uv_w - slab.width) < 1.0
        and abs(uv_h - slab.height) < 1.0
        and abs(project_clip.area - slab.width * slab.height) < 1.0
    )

    return PlacedPiece(
        piece_id=piece_id,
        slab_id=slab.slab_id,
        source_slab_id=slab.slab_id,
        piece_index_from_slab=1,  # finalised by the caller after grouping
        piece_role="main",
        project_polygon=project_coords,
        slab_polygon=slab_coords,
        rotation=0.0,
        texture_transform=TextureTransform(
            image_path=slab.image_path,
            uv_origin=uv_origin,
            uv_width=uv_w,
            uv_height=uv_h,
            rotation=0.0,
            scale=(1.0, 1.0),
        ),
        is_full_slab=is_full_slab,
        risk_flags=[],
    )


def run_row_based_placement(
    ctx: StrategyContext,
) -> tuple[list[PlacedPiece], list[ReviewMarker], list[PlacementRecord]]:
    """Internal helper used by both `balanced` and `lowest_waste`.

    Returns the produced pieces, any skip markers raised during the loop,
    and a `PlacementRecord` per *successful* slab placement so callers
    can inspect what slab-local area was used.
    """
    rules = ctx.project_input.rules
    slabs = ctx.project_input.slabs
    project = ctx.project_polygon

    minx, miny, maxx, maxy = bbox_dimensions(project)
    piece_ids = IdSequence("P")
    marker_ids = IdSequence("R")
    pieces: list[PlacedPiece] = []
    markers: list[ReviewMarker] = []
    records: list[PlacementRecord] = []

    cursor_x = minx
    cursor_y = miny
    row_height: float | None = None
    slab_idx = 0

    while slab_idx < len(slabs) and cursor_y < maxy:
        slab = slabs[slab_idx]

        # First slab in a row defines the row height.
        if row_height is None:
            row_height = slab.height

        placed_rect = rectangle(cursor_x, cursor_y, slab.width, row_height)
        new_pieces: list[PlacedPiece] = []
        for clip in clip_to_project(placed_rect, project):
            if not _passes_min_size(clip, rules):
                continue
            new_pieces.append(
                _piece_from_clip(
                    clip,
                    slab,
                    placed_origin=(cursor_x, cursor_y),
                    piece_id=piece_ids.next(),
                )
            )

        if new_pieces:
            pieces.extend(new_pieces)
            records.append(
                PlacementRecord(
                    slab=slab,
                    project_origin=(cursor_x, cursor_y),
                    placed_size=(slab.width, row_height),
                    pieces=list(new_pieces),
                )
            )
            slab_idx += 1
        else:
            markers.append(
                ReviewMarker(
                    review_id=marker_ids.next(),
                    type="empty_slab_placement_skipped",
                    location=(
                        cursor_x + slab.width / 2.0,
                        cursor_y + row_height / 2.0,
                    ),
                    related_piece_ids=[],
                    severity="low",
                    message=(
                        f"Slab '{slab.slab_id}' did not intersect the "
                        f"usable project area at this position; "
                        f"retrying at the next cursor."
                    ),
                )
            )

        cursor_x += slab.width
        if cursor_x >= maxx:
            cursor_y += row_height
            cursor_x = minx
            row_height = None

    return pieces, markers, records


class RowBasedStrategy(PlacementStrategy):
    """Generic row-based generator. Concrete strategies wrap this."""

    name = "balanced"

    def generate(self, ctx: StrategyContext) -> StrategyResult:
        pieces, markers, _records = run_row_based_placement(ctx)
        return StrategyResult(pieces=pieces, review_markers=markers)


class BalancedStrategy(RowBasedStrategy):
    """Default strategy: row-based generator with no special weighting."""

    name = "balanced"
