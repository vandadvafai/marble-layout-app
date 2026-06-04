"""Grid generation: tile the usable floor polygon with nominal rectangles.

For every grid cell that intersects ``usable = boundary − Σ holes``:

* If the cell sits entirely inside the usable polygon (within a small
  area tolerance), it becomes a **full tile**.
* Otherwise the intersection is clipped against the usable polygon
  and emitted as one or more **edge pieces** — partial pieces are
  kept, not rejected.

Holes that split a single grid cell into multiple disjoint regions
each become their own edge piece (``piece_id`` gets a ``_p<idx>``
suffix). Slabs are NOT consulted at this stage.
"""

from __future__ import annotations

import math
from typing import Iterable

from shapely.geometry import MultiPolygon, Polygon as ShPolygon
from shapely.geometry.base import BaseGeometry

from placement_engine.layout.anchoring import (
    ANCHOR_AUTO,
    ANCHOR_BOTTOM_LEFT,
    DEFAULT_CANDIDATE_MODES,
    SUPPORTED_ANCHOR_MODES,
    SliverEvaluation,
    SliverPolicy,
    compute_anchor_origin,
    evaluate_layout,
    score_evaluation,
)
from placement_engine.layout.inventory_stats import (
    HasDimensions,
    compute_inventory_dimension_summary,
)
from placement_engine.layout.schema import (
    LAYOUT_BASIS_EXPLICIT,
    LAYOUT_BASIS_INVENTORY_MEDIAN,
    LayoutResult,
    Piece,
)
from placement_engine.target_area.dxf_target import TargetGeometry

# Area-comparison tolerance (mm²). Two areas differing by less than
# this are treated as equal — covers Shapely floating-point noise on
# intersections that "should" be exact.
_AREA_EQ_TOL_MM2: float = 1.0

# A piece whose actual area is below this fraction of the nominal tile
# area gets a ``sliver`` note. Designers can decide whether to merge
# or discard them later.
DEFAULT_SLIVER_AREA_FRACTION: float = 0.05


def generate_tile_layout(
    geometry: TargetGeometry,
    tile_width_mm: float,
    tile_height_mm: float,
    *,
    origin: tuple[float, float] | None = None,
    sliver_area_fraction: float = DEFAULT_SLIVER_AREA_FRACTION,
) -> LayoutResult:
    """Generate a tile-grid layout over the geometry's usable polygon.

    The grid is anchored at ``origin`` (defaults to the boundary's
    bottom-left). Cells are walked row-major from the bottom-left
    upward and rightward. Every cell that touches the usable polygon
    contributes one (or more, if split by a hole) `Piece`.
    """
    if tile_width_mm <= 0 or tile_height_mm <= 0:
        raise ValueError(
            f"tile dimensions must be positive; got "
            f"{tile_width_mm}×{tile_height_mm}"
        )

    boundary = ShPolygon(geometry.boundary)
    usable: BaseGeometry = boundary
    for hole in geometry.holes:
        usable = usable.difference(ShPolygon(hole))

    bx0, by0, bx1, by1 = geometry.bbox
    if origin is None:
        origin = (bx0, by0)
    ox, oy = origin

    nominal_area_mm2 = tile_width_mm * tile_height_mm
    sliver_area_mm2 = sliver_area_fraction * nominal_area_mm2

    n_cols = max(0, math.ceil((bx1 - ox) / tile_width_mm))
    n_rows = max(0, math.ceil((by1 - oy) / tile_height_mm))

    pieces: list[Piece] = []
    for row in range(n_rows):
        for col in range(n_cols):
            tile_x = ox + col * tile_width_mm
            tile_y = oy + row * tile_height_mm
            tile_poly = ShPolygon([
                (tile_x, tile_y),
                (tile_x + tile_width_mm, tile_y),
                (tile_x + tile_width_mm, tile_y + tile_height_mm),
                (tile_x, tile_y + tile_height_mm),
            ])

            inter = usable.intersection(tile_poly)
            if inter.is_empty:
                continue  # tile sits entirely outside usable polygon

            sub_polys = _flatten_polygons(inter)
            if not sub_polys:
                continue

            # A hole inside the tile splits the intersection into > 1 piece.
            split_by_hole = len(sub_polys) > 1
            for p_idx, sub in enumerate(sub_polys):
                actual_area_mm2 = sub.area
                # A piece is "full" iff exactly one sub-polygon AND its
                # area matches the nominal tile area within tolerance.
                is_full = (
                    not split_by_hole
                    and abs(actual_area_mm2 - nominal_area_mm2) < _AREA_EQ_TOL_MM2
                )
                bx_min, by_min, bx_max, by_max = sub.bounds

                # Detect whether *this* tile was cut by a hole (vs only
                # the outer boundary): if the nominal tile lies fully
                # inside the boundary, any area loss is from a hole.
                cut_by_hole = split_by_hole or (
                    boundary.contains(tile_poly)
                    and (nominal_area_mm2 - actual_area_mm2) > _AREA_EQ_TOL_MM2
                )

                piece_id = f"tile_r{row}_c{col}"
                if split_by_hole:
                    piece_id += f"_p{p_idx}"

                notes: list[str] = []
                if split_by_hole:
                    notes.append("split_by_hole")
                if not is_full and actual_area_mm2 < sliver_area_mm2:
                    notes.append("sliver")

                pieces.append(Piece(
                    piece_id=piece_id,
                    row=row, col=col,
                    nominal_x_mm=float(tile_x),
                    nominal_y_mm=float(tile_y),
                    nominal_width_mm=float(tile_width_mm),
                    nominal_height_mm=float(tile_height_mm),
                    actual_cut_polygon=_polygon_to_coords(sub),
                    bounding_width_mm=float(bx_max - bx_min),
                    bounding_height_mm=float(by_max - by_min),
                    actual_area_m2=actual_area_mm2 / 1_000_000.0,
                    is_full_tile=is_full,
                    is_edge_piece=not is_full,
                    intersects_hole=cut_by_hole,
                    interior_holes=_polygon_interior_rings(sub),
                    notes=notes,
                ))

    return LayoutResult(
        target=geometry,
        tile_width_mm=float(tile_width_mm),
        tile_height_mm=float(tile_height_mm),
        origin=(float(ox), float(oy)),
        pieces=pieces,
        layout_basis=LAYOUT_BASIS_EXPLICIT,
    )


def generate_tile_layout_from_inventory(
    geometry: TargetGeometry,
    inventory: Iterable[HasDimensions],
    *,
    source_inventory_path: str | None = None,
    origin: tuple[float, float] | None = None,
    sliver_area_fraction: float = DEFAULT_SLIVER_AREA_FRACTION,
    anchor_mode: str = ANCHOR_AUTO,
    sliver_policy: SliverPolicy | None = None,
    candidate_modes: tuple[str, ...] | None = None,
) -> LayoutResult:
    """Generate a tile-grid layout using the inventory's median slab size.

    This is the **default** entry point for V1: the layout's nominal
    tile dimensions are the median width × median height of the supplied
    inventory, so the geometric layout reflects the actual stock the
    client is laying. Use ``generate_tile_layout`` directly only when
    an explicit override is required (debug, testing, designer pick).

    ``anchor_mode`` controls where the grid is anchored:

      * ``"auto"`` (default): every mode in ``candidate_modes`` is
        tried and the one with the fewest uncuttable slivers wins
        (see ``anchoring.score_evaluation`` for the full priority).
      * an explicit anchor name (``"bottom_left"``, …): that origin
        is used directly, no candidate search.

    Passing an explicit ``origin=`` always takes precedence — anchor
    selection is skipped and the origin is used as-is. This preserves
    the lower-level escape hatch the tests already rely on.

    The returned ``LayoutResult.layout_basis`` is
    ``"inventory_median"``, and ``inventory_dimension_summary`` carries
    the full stats so the JSON trace records why this tile size was
    chosen.
    """
    summary = compute_inventory_dimension_summary(inventory)
    policy = sliver_policy if sliver_policy is not None else SliverPolicy()
    candidates = (
        candidate_modes if candidate_modes is not None
        else DEFAULT_CANDIDATE_MODES
    )

    if origin is not None:
        # Explicit origin overrides everything — used by tests and
        # designer debug. Anchor metadata is left unset because the
        # origin no longer corresponds to any of the named modes.
        result = generate_tile_layout(
            geometry,
            tile_width_mm=summary.median_width_mm,
            tile_height_mm=summary.median_height_mm,
            origin=origin,
            sliver_area_fraction=sliver_area_fraction,
        )
        chosen_mode = "explicit_origin"
        evaluations: list[SliverEvaluation] = []
    elif anchor_mode == ANCHOR_AUTO:
        result, chosen_mode, evaluations = _generate_with_auto_anchor(
            geometry, summary, sliver_area_fraction, policy, candidates,
        )
    else:
        if anchor_mode not in SUPPORTED_ANCHOR_MODES:
            raise ValueError(
                f"unsupported anchor_mode {anchor_mode!r}; "
                f"choose one of {(*SUPPORTED_ANCHOR_MODES, ANCHOR_AUTO)}"
            )
        chosen_origin = compute_anchor_origin(
            geometry.bbox,
            summary.median_width_mm, summary.median_height_mm,
            anchor_mode,
        )
        result = generate_tile_layout(
            geometry,
            tile_width_mm=summary.median_width_mm,
            tile_height_mm=summary.median_height_mm,
            origin=chosen_origin,
            sliver_area_fraction=sliver_area_fraction,
        )
        ev = evaluate_layout(result, anchor_mode=anchor_mode, policy=policy)
        ev.selected = True
        chosen_mode = anchor_mode
        evaluations = [ev]

    result.layout_basis = LAYOUT_BASIS_INVENTORY_MEDIAN
    result.source_inventory_path = source_inventory_path
    result.inventory_dimension_summary = summary
    result.anchor_mode = chosen_mode
    result.sliver_policy = policy
    result.candidate_evaluations = evaluations
    return result


def _generate_with_auto_anchor(
    geometry: TargetGeometry,
    summary,
    sliver_area_fraction: float,
    policy: SliverPolicy,
    candidates: tuple[str, ...],
) -> tuple[LayoutResult, str, list[SliverEvaluation]]:
    """Build a layout for every candidate anchor and return the best.

    Best = lowest tuple under ``score_evaluation``. All candidates are
    returned in the evaluations list (with ``selected=True`` set on
    the winner) so the JSON trace explains *why* a given mode beat
    the alternatives.
    """
    if not candidates:
        raise ValueError("candidate_modes must contain at least one anchor")

    layouts: list[tuple[LayoutResult, SliverEvaluation]] = []
    for mode in candidates:
        origin = compute_anchor_origin(
            geometry.bbox,
            summary.median_width_mm, summary.median_height_mm,
            mode,
        )
        layout = generate_tile_layout(
            geometry,
            tile_width_mm=summary.median_width_mm,
            tile_height_mm=summary.median_height_mm,
            origin=origin,
            sliver_area_fraction=sliver_area_fraction,
        )
        ev = evaluate_layout(layout, anchor_mode=mode, policy=policy)
        layouts.append((layout, ev))

    layouts.sort(key=lambda pair: score_evaluation(pair[1]))
    best_layout, best_ev = layouts[0]
    best_ev.selected = True
    # Return the evaluations in original candidate order so the JSON
    # output reads as a stable "we tried X, then Y, here's why X won".
    eval_by_mode = {ev.anchor_mode: ev for _, ev in layouts}
    ordered_evals = [eval_by_mode[m] for m in candidates]
    return best_layout, best_ev.anchor_mode, ordered_evals


# ---------------------------------------------------------------------------
# Shapely helpers
# ---------------------------------------------------------------------------


def _flatten_polygons(geom: BaseGeometry) -> list[ShPolygon]:
    """Unwrap a possibly-Multi geometry to a list of polygons.

    Lines and points (which can appear from degenerate intersections)
    are dropped — they have zero area and don't contribute to the
    layout.
    """
    if geom.is_empty:
        return []
    if isinstance(geom, ShPolygon):
        return [geom] if geom.area > 0 else []
    if isinstance(geom, MultiPolygon):
        return [g for g in geom.geoms if g.area > 0]
    # GeometryCollection — pick out the polygons.
    polys: list[ShPolygon] = []
    for g in getattr(geom, "geoms", ()):
        polys.extend(_flatten_polygons(g))
    return polys


def _polygon_to_coords(poly: ShPolygon) -> list[tuple[float, float]]:
    """Exterior ring as a closed list of (x, y) tuples (mm)."""
    return [(float(x), float(y)) for x, y in poly.exterior.coords]


def _polygon_interior_rings(poly: ShPolygon) -> list[list[tuple[float, float]]]:
    """Interior rings (holes) of the polygon as closed (x, y) lists (mm).

    Empty when the polygon has no interior holes — the common case for
    full tiles, edge clips, and edge-touching holes. Populated only
    when the tile contains a hole strictly inside it (the cut piece
    has a marble cut-out, i.e. fabrication needs an internal cut).
    """
    return [
        [(float(x), float(y)) for x, y in ring.coords]
        for ring in poly.interiors
    ]
