"""`lowest_waste` strategy: row-based main pass + offcut reuse.

Phase 1 — Main pass:
    Run the same row-based loop the `balanced` strategy uses. Returns
    main pieces and a `PlacementRecord` per successfully placed slab.

Phase 2 — Offcut inventory:
    For each successful placement, compute the *unused* slab-local area:
        - the right column of the slab not covered by the clipped pieces
        - the top row of the slab not covered by the row's height
        - the bottom or left strips when clipping pulled the used bbox
          off the slab origin
    Each unused rectangle becomes an `OffcutRectangle` in slab-local
    coordinates, tagged with its source slab.

Phase 3 — Uncovered region detection:
    `project − ⋃ main piece polygons` gives every pixel of project area
    we still need to fill. Each connected component is traversed in a
    deterministic order (by area, descending).

Phase 4 — Greedy fill:
    For each uncovered component:
        a. Take its axis-aligned bounding box and intersect with the
           project polygon (so we never extend an offcut past a
           project edge or into a hole).
        b. Pick the largest available offcut that has any chance of
           fitting (one of its dimensions ≤ the gap's matching
           dimension), preferring offcuts that match a dimension
           exactly.
        c. Cut the offcut at its corner: take a sub-rectangle sized
           min(offcut, gap) anchored at the offcut's slab-local origin.
        d. Place the resulting piece in the project at the gap's
           bottom-left corner; record both the project polygon (clipped
           against the project) and the slab-local polygon.
        e. Update inventory: subtract the consumed rectangle from the
           offcut, leaving zero, one, or two smaller offcut rectangles.
        f. Subtract the new piece's project polygon from the uncovered
           area and continue.

Phase 5 — Output assembly:
    Renumber pieces so each carries `piece_id = "{slab_id}_{N}"` (1-based
    per slab) and `piece_index_from_slab` agrees. Main pieces are
    tagged `piece_role = "main"`; reused offcut pieces are
    `piece_role = "offcut"`.

Limits (intentional for v1):
  * Only axis-aligned rectangular offcuts are tracked.
  * Uncovered components are filled at their bounding box; if a
    component is non-rectangular, only the rectangular core is filled
    (any L-shaped tail stays uncovered until a smarter decomposer lands).
  * Greedy first-fit, no back-tracking.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from shapely.geometry import GeometryCollection, MultiPolygon, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from placement_engine.config import AREA_EPSILON_MM2, LENGTH_EPSILON_MM
from placement_engine.geometry.polygons import polygon_to_coords
from placement_engine.models import (
    PlacedPiece,
    Slab,
    TextureTransform,
)
from placement_engine.strategies.base import (
    PlacementStrategy,
    StrategyContext,
    StrategyResult,
)
from placement_engine.strategies.row_based import (
    PlacementRecord,
    _passes_min_size,
    run_row_based_placement,
)


# ---------------------------------------------------------------------------
# Internal types
# ---------------------------------------------------------------------------


@dataclass
class OffcutRectangle:
    """An axis-aligned rectangular leftover region in slab-local coords."""

    slab_id: str
    x: float
    y: float
    width: float
    height: float

    @property
    def area(self) -> float:
        return self.width * self.height


# ---------------------------------------------------------------------------
# Phase 2 — build the offcut inventory
# ---------------------------------------------------------------------------


def _build_offcuts(records: list[PlacementRecord]) -> dict[str, list[OffcutRectangle]]:
    """For each placement record, compute the slab-local rectangles that
    were *not* consumed and return them grouped by slab."""
    offcuts: dict[str, list[OffcutRectangle]] = defaultdict(list)
    for rec in records:
        slab = rec.slab
        # Slab-local bbox of the actually-used area (union of clipped
        # piece bboxes). For row-based placements the slab is anchored
        # at slab-local (0, 0); used bbox is therefore the bbox of every
        # slab_polygon emitted from this record.
        used_xs: list[float] = []
        used_ys: list[float] = []
        for piece in rec.pieces:
            for x, y in piece.slab_polygon:
                used_xs.append(x)
                used_ys.append(y)
        if not used_xs:
            continue
        used_xmin = min(used_xs)
        used_ymin = min(used_ys)
        used_xmax = max(used_xs)
        used_ymax = max(used_ys)

        sw, sh = slab.width, slab.height
        # Disjoint partition of (slab \ used_bbox) into up to four
        # corner-anchored rectangles. Order is right, top, left, bottom
        # so the largest rectangle in a typical edge-clip case (right
        # strip) is generated first.
        candidates = [
            # right of used area, full slab height
            OffcutRectangle(slab.slab_id, used_xmax, 0.0, sw - used_xmax, sh),
            # top of used area, only over the used x-range
            OffcutRectangle(slab.slab_id, used_xmin, used_ymax,
                            used_xmax - used_xmin, sh - used_ymax),
            # left of used area, full slab height
            OffcutRectangle(slab.slab_id, 0.0, 0.0, used_xmin, sh),
            # bottom of used area, only over the used x-range
            OffcutRectangle(slab.slab_id, used_xmin, 0.0,
                            used_xmax - used_xmin, used_ymin),
        ]
        for cand in candidates:
            if cand.width > LENGTH_EPSILON_MM and cand.height > LENGTH_EPSILON_MM:
                offcuts[slab.slab_id].append(cand)
    return offcuts


# ---------------------------------------------------------------------------
# Phase 3 — uncovered region detection
# ---------------------------------------------------------------------------


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
    return []


def _uncovered_components(project: Polygon, pieces: list[PlacedPiece]) -> list[Polygon]:
    """Connected components of `project − ⋃ pieces`, sorted by area desc."""
    if not pieces:
        return _flatten_polygons(project)
    placed_union = unary_union([Polygon(p.project_polygon) for p in pieces])
    uncovered = project.difference(placed_union)
    components = _flatten_polygons(uncovered)
    components.sort(key=lambda p: p.area, reverse=True)
    return components


# ---------------------------------------------------------------------------
# Phase 4 — greedy offcut filling
# ---------------------------------------------------------------------------


def _best_offcut(
    gap_w: float, gap_h: float, offcuts: list[OffcutRectangle]
) -> OffcutRectangle | None:
    """Pick the offcut that best fills a `gap_w × gap_h` rectangle.

    Preference order:
        1. offcut whose width ≥ gap_w AND height ≥ gap_h (covers the
           gap by itself in one cut)
        2. offcut whose width ≥ gap_w (full-width strip; cut to height)
        3. offcut whose height ≥ gap_h (full-height column; cut to width)
        4. otherwise, the largest offcut by area (will partially fill)

    Within each tier, larger area wins.
    """
    full = [o for o in offcuts if o.width >= gap_w and o.height >= gap_h]
    if full:
        return max(full, key=lambda o: o.area)
    width_match = [o for o in offcuts if o.width >= gap_w]
    if width_match:
        return max(width_match, key=lambda o: o.area)
    height_match = [o for o in offcuts if o.height >= gap_h]
    if height_match:
        return max(height_match, key=lambda o: o.area)
    return max(offcuts, key=lambda o: o.area) if offcuts else None


def _shrink_offcut(
    offcut: OffcutRectangle, used_w: float, used_h: float
) -> list[OffcutRectangle]:
    """Return the at-most-two rectangles left after taking a corner-anchored
    `used_w × used_h` slice from the bottom-left of `offcut`."""
    remaining: list[OffcutRectangle] = []
    if offcut.width - used_w > LENGTH_EPSILON_MM:
        remaining.append(OffcutRectangle(
            offcut.slab_id,
            offcut.x + used_w,
            offcut.y,
            offcut.width - used_w,
            offcut.height,
        ))
    if offcut.height - used_h > LENGTH_EPSILON_MM:
        remaining.append(OffcutRectangle(
            offcut.slab_id,
            offcut.x,
            offcut.y + used_h,
            used_w,
            offcut.height - used_h,
        ))
    return remaining


def _make_offcut_piece(
    slab: Slab,
    project_clip: Polygon,
    slab_local_rect: tuple[float, float, float, float],
    piece_id: str,
) -> PlacedPiece:
    """Build a `PlacedPiece` for an offcut placement.

    `project_clip` is the actual project-space polygon for the placement
    (already clipped against the project boundary). The slab-local
    polygon is the corner-anchored rectangle (sx, sy, sx+w, sy+h)
    inside the source slab.
    """
    sx, sy, sx_max, sy_max = slab_local_rect
    slab_coords: list[tuple[float, float]] = [
        (sx, sy), (sx_max, sy), (sx_max, sy_max), (sx, sy_max),
    ]
    project_coords = polygon_to_coords(project_clip)
    return PlacedPiece(
        piece_id=piece_id,
        slab_id=slab.slab_id,
        source_slab_id=slab.slab_id,
        piece_index_from_slab=1,  # finalised in renumber pass
        piece_role="offcut",
        project_polygon=project_coords,
        slab_polygon=slab_coords,
        rotation=0.0,
        texture_transform=TextureTransform(
            image_path=slab.image_path,
            uv_origin=(sx, sy),
            uv_width=sx_max - sx,
            uv_height=sy_max - sy,
            rotation=0.0,
            scale=(1.0, 1.0),
        ),
        is_full_slab=False,
        risk_flags=[],
    )


def _fill_uncovered(
    uncovered: list[Polygon],
    project: Polygon,
    offcuts: dict[str, list[OffcutRectangle]],
    slab_lookup: dict[str, Slab],
    rules,
    piece_counter: list[int],
) -> list[PlacedPiece]:
    """Greedily place offcuts into uncovered components."""
    out: list[PlacedPiece] = []

    # Flatten the offcut inventory into a single list we can search.
    def _all_offcuts() -> list[OffcutRectangle]:
        return [o for slabs_offcuts in offcuts.values() for o in slabs_offcuts]

    remaining_components = list(uncovered)
    while remaining_components and _all_offcuts():
        comp = remaining_components.pop(0)
        if comp.area <= AREA_EPSILON_MM2:
            continue

        # Work bbox-by-bbox: take the bbox of the current component as
        # the gap to fill, clip placements against the project.
        cminx, cminy, cmaxx, cmaxy = comp.bounds
        gap_w = cmaxx - cminx
        gap_h = cmaxy - cminy

        offcut = _best_offcut(gap_w, gap_h, _all_offcuts())
        if offcut is None:
            break

        used_w = min(offcut.width, gap_w)
        used_h = min(offcut.height, gap_h)

        # Project-space placement anchored at the gap's bottom-left.
        place_rect = box(cminx, cminy, cminx + used_w, cminy + used_h)
        clipped = place_rect.intersection(project)
        clipped_polys = _flatten_polygons(clipped)
        if not clipped_polys:
            # Pop this offcut from inventory so we don't loop forever.
            offcuts[offcut.slab_id].remove(offcut)
            remaining_components.insert(0, comp)
            continue

        # Use the clipped polygon as-is (typically a single rectangle).
        # If clipping produced a non-rectangular shape, we still emit it;
        # the slab-local polygon stays the corner-anchored rectangle the
        # offcut was cut from. That's defensible because the slab-local
        # rectangle is what was physically cut; the project polygon is
        # only used for layout placement.
        placed_piece_polys = [p for p in clipped_polys if _passes_min_size(p, rules)]
        if not placed_piece_polys:
            offcuts[offcut.slab_id].remove(offcut)
            remaining_components.insert(0, comp)
            continue

        slab = slab_lookup[offcut.slab_id]
        for poly in placed_piece_polys:
            piece_counter[0] += 1
            out.append(_make_offcut_piece(
                slab=slab,
                project_clip=poly,
                slab_local_rect=(
                    offcut.x, offcut.y,
                    offcut.x + used_w, offcut.y + used_h,
                ),
                piece_id=f"OFF{piece_counter[0]:04d}",  # provisional, renumbered later
            ))

        # Update offcut inventory.
        offcuts[offcut.slab_id].remove(offcut)
        offcuts[offcut.slab_id].extend(_shrink_offcut(offcut, used_w, used_h))

        # Update the uncovered component: subtract every clipped polygon.
        consumed_union = unary_union([Polygon(polygon_to_coords(p)) for p in placed_piece_polys])
        new_comp = comp.difference(consumed_union)
        for new_part in _flatten_polygons(new_comp):
            remaining_components.append(new_part)
        # Re-sort so the next iteration tackles the largest gap.
        remaining_components.sort(key=lambda p: p.area, reverse=True)

    return out


# ---------------------------------------------------------------------------
# Phase 5 — finalise piece ids
# ---------------------------------------------------------------------------


def _renumber_by_slab(pieces: list[PlacedPiece]) -> list[PlacedPiece]:
    """Assign `piece_id = "{slab_id}_{N}"` and `piece_index_from_slab`
    in placement order, keeping main pieces ahead of offcut pieces from
    the same slab."""
    counters: dict[str, int] = defaultdict(int)
    # Stable sort: main pieces before offcut pieces (within each slab).
    role_order = {"main": 0, "offcut": 1}
    ordered = sorted(
        enumerate(pieces),
        key=lambda kv: (kv[1].slab_id, role_order.get(kv[1].piece_role, 99), kv[0]),
    )
    final_by_id: dict[int, PlacedPiece] = {}
    for original_index, piece in ordered:
        counters[piece.slab_id] += 1
        n = counters[piece.slab_id]
        # Build a new piece (Pydantic models are mutable but copying
        # documents intent and avoids surprising aliasing).
        final_by_id[original_index] = piece.model_copy(update={
            "piece_id": f"{piece.slab_id}_{n}",
            "piece_index_from_slab": n,
            "source_slab_id": piece.source_slab_id or piece.slab_id,
        })
    # Restore original placement order for output.
    return [final_by_id[i] for i in range(len(pieces))]


# ---------------------------------------------------------------------------
# Strategy class
# ---------------------------------------------------------------------------


class LowestWasteStrategy(PlacementStrategy):
    """Row-based main placement plus rectangular offcut reuse."""

    name = "lowest_waste"

    def generate(self, ctx: StrategyContext) -> StrategyResult:
        # Phase 1 — main row-based pass.
        main_pieces, markers, records = run_row_based_placement(ctx)

        # Tag main pieces explicitly. (`run_row_based_placement` already
        # sets piece_role="main" but be defensive.)
        for p in main_pieces:
            p.piece_role = "main"
            p.source_slab_id = p.slab_id

        # Phase 2 — offcut inventory.
        offcuts = _build_offcuts(records)

        # Phase 3 — uncovered components.
        uncovered = _uncovered_components(ctx.project_polygon, main_pieces)

        # Phase 4 — greedy fill.
        slab_lookup = {s.slab_id: s for s in ctx.project_input.slabs}
        offcut_pieces = _fill_uncovered(
            uncovered=uncovered,
            project=ctx.project_polygon,
            offcuts=offcuts,
            slab_lookup=slab_lookup,
            rules=ctx.project_input.rules,
            piece_counter=[0],
        )

        # Phase 5 — renumber piece ids slab-by-slab.
        all_pieces = _renumber_by_slab(main_pieces + offcut_pieces)
        return StrategyResult(pieces=all_pieces, review_markers=markers)
