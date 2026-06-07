"""Candidate-layout strategy enumeration.

The V1 selector tried only four global anchor modes. On most real
floors that produces just two distinct layouts (bottom-anchored vs.
top-anchored) because the horizontal leftover lands in the same
fixed place regardless of left-vs-right anchor. The selector
mechanism works, but its discrimination power is gated by the
candidate pool.

This module widens the pool with three additional strategies, each
matching one of the design-team rules:

  * ``grid_offset``      — half-tile origin shifts on each axis,
                            useful when the bbox is not tile-aligned
  * ``doorway_centred``  — origin chosen so a full tile lands centred
                            on each doorway midpoint (rule #3:
                            "prefer a full slab over the doorway")
  * ``column_aligned``   — origin chosen so a grid line falls on a
                            column edge (rule #4: "seams near columns
                            are preferred over seams in open areas")

Each strategy contributes its own ``CandidateSpec``; the selector
then runs the existing layout pipeline once per spec. The new specs
DO NOT alter how individual layouts are generated — they just
broaden the input variation the selector compares.

**V1 limitations** (carried forward from the prior milestone):

  * Anchor selection still applies globally — every spec uses one
    anchor (or origin) uniformly across all zones. Per-zone anchor
    combinations are a future milestone.
  * Doorway-aware specs only place the tile *centred* on the
    midpoint. They don't yet try centre-of-room or offset-by-frame
    variants, and they don't reorder zone tiling.
  * Column-aware specs only align to the left and right edges of
    each column. Top/bottom edge alignment for horizontal seams is
    a future addition.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from placement_engine.architectural.schema import ArchitecturalPlan, Column, Doorway
from placement_engine.layout import (
    ANCHOR_BOTTOM_LEFT,
    ANCHOR_BOTTOM_RIGHT,
    ANCHOR_TOP_LEFT,
    ANCHOR_TOP_RIGHT,
)
from placement_engine.target_area.dxf_target import TargetGeometry

# Strategy category labels surfaced in candidates_summary.json.
STRATEGY_ANCHOR: str = "anchor"
STRATEGY_GRID_OFFSET: str = "grid_offset"
STRATEGY_DOORWAY_CENTRED: str = "doorway_centred"
STRATEGY_COLUMN_ALIGNED: str = "column_aligned"

SUPPORTED_STRATEGIES: tuple[str, ...] = (
    STRATEGY_ANCHOR,
    STRATEGY_GRID_OFFSET,
    STRATEGY_DOORWAY_CENTRED,
    STRATEGY_COLUMN_ALIGNED,
)


@dataclass
class CandidateSpec:
    """One recipe for generating a candidate layout.

    Exactly one of ``anchor_mode`` and ``origin`` is meaningful:

    * ``anchor_mode`` set + ``origin`` None → use the layout layer's
      named-anchor path (per-zone anchor selection still applies).
    * ``origin`` set + ``anchor_mode`` None → pass the explicit origin
      to the layout layer (anchor selection is bypassed).
    """

    candidate_id: str
    strategy: str
    description: str
    anchor_mode: str | None = None
    origin: tuple[float, float] | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "strategy": self.strategy,
            "description": self.description,
            "anchor_mode": self.anchor_mode,
            "origin": (
                [self.origin[0], self.origin[1]]
                if self.origin is not None else None
            ),
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# enumeration
# ---------------------------------------------------------------------------


def enumerate_candidate_specs(
    geometry: TargetGeometry,
    tile_width_mm: float,
    tile_height_mm: float,
    plan: ArchitecturalPlan,
) -> list[CandidateSpec]:
    """Build the full candidate set for one layout job.

    The returned list always starts with the four global anchor
    candidates so existing selectors keep their baseline. Grid-offset
    candidates always follow. Plan-driven strategies (doorway,
    column) only contribute specs when the plan declares the relevant
    features, so simple plans don't produce a huge candidate pool.

    Candidate IDs are renumbered sequentially (``C01_…`` through
    ``CNN_…``) so the selector's deterministic tie-break is stable
    regardless of declaration order.
    """
    if tile_width_mm <= 0 or tile_height_mm <= 0:
        raise ValueError(
            f"tile dimensions must be positive; got "
            f"{tile_width_mm}×{tile_height_mm}"
        )

    specs: list[CandidateSpec] = []
    specs.extend(_anchor_specs())
    specs.extend(_grid_offset_specs(geometry, tile_width_mm, tile_height_mm))
    specs.extend(_doorway_centred_specs(
        geometry, tile_width_mm, tile_height_mm, plan,
    ))
    specs.extend(_column_aligned_specs(geometry, plan))

    # Renumber so candidate IDs are stable across strategy interleaving.
    for i, spec in enumerate(specs, start=1):
        spec.candidate_id = f"C{i:02d}_{spec.candidate_id}"
    return specs


# ---------------------------------------------------------------------------
# strategy generators
# ---------------------------------------------------------------------------


def _anchor_specs() -> list[CandidateSpec]:
    """Four global anchor modes — the existing V1 baseline."""
    return [
        CandidateSpec(
            candidate_id=a,
            strategy=STRATEGY_ANCHOR,
            description=f"global anchor mode '{a}'",
            anchor_mode=a,
        )
        for a in (
            ANCHOR_BOTTOM_LEFT,
            ANCHOR_BOTTOM_RIGHT,
            ANCHOR_TOP_LEFT,
            ANCHOR_TOP_RIGHT,
        )
    ]


def _grid_offset_specs(
    geometry: TargetGeometry,
    tile_w: float,
    tile_h: float,
) -> list[CandidateSpec]:
    """Half-tile shifts on each axis.

    These are the simplest non-anchor candidates and useful as a
    sanity baseline against the doorway/column-aware specs: if a
    half-shift happens to score better than every doorway-centred
    candidate, the doorway-centred logic isn't paying its way.
    """
    bx0, by0, _, _ = geometry.bbox
    return [
        CandidateSpec(
            candidate_id="offset_half_x",
            strategy=STRATEGY_GRID_OFFSET,
            description=f"half-tile shift on the x axis ({tile_w/2:.0f} mm)",
            origin=(bx0 + tile_w / 2.0, by0),
        ),
        CandidateSpec(
            candidate_id="offset_half_y",
            strategy=STRATEGY_GRID_OFFSET,
            description=f"half-tile shift on the y axis ({tile_h/2:.0f} mm)",
            origin=(bx0, by0 + tile_h / 2.0),
        ),
    ]


def _doorway_centred_specs(
    geometry: TargetGeometry,
    tile_w: float,
    tile_h: float,
    plan: ArchitecturalPlan,
) -> list[CandidateSpec]:
    """One candidate per doorway — origin chosen so a full tile lands
    centred on the doorway midpoint, on whichever axis the doorway
    runs along.

    The candidate is per-doorway because each doorway lives on its
    own wall and the centring origin differs per midpoint. Designers
    can compare them side by side in the candidates summary.
    """
    if not plan.doorways:
        return []
    bx0, by0, _, _ = geometry.bbox
    specs: list[CandidateSpec] = []
    for dr in plan.doorways:
        ox, oy, axis_note = _origin_centred_on_doorway(
            dr, tile_w, tile_h, default_x=bx0, default_y=by0,
        )
        specs.append(CandidateSpec(
            candidate_id=f"doorway_{dr.doorway_id}",
            strategy=STRATEGY_DOORWAY_CENTRED,
            description=(
                f"tile centred on doorway {dr.doorway_id!r} {axis_note}"
                + (" (main entrance)" if dr.is_main_entrance else "")
            ),
            origin=(ox, oy),
        ))
    return specs


def _origin_centred_on_doorway(
    doorway: Doorway,
    tile_w: float,
    tile_h: float,
    *,
    default_x: float,
    default_y: float,
) -> tuple[float, float, str]:
    """Pick the grid origin that puts a full tile across the doorway.

    A horizontal doorway (segment along x) wants a *vertical* tile
    boundary at ``midpoint.x - tile_w/2`` so the tile spans
    ``[mid - tile/2, mid + tile/2]``. A vertical doorway is the
    horizontal-edge mirror. The other axis keeps the default
    (bbox bottom-left). The returned note describes which axis was
    targeted so the candidate description is self-explanatory.
    """
    (x1, y1), (x2, y2) = doorway.segment
    mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    if abs(x2 - x1) >= abs(y2 - y1):
        return (mx - tile_w / 2.0, default_y, f"on x={mx:.0f}")
    return (default_x, my - tile_h / 2.0, f"on y={my:.0f}")


def _column_aligned_specs(
    geometry: TargetGeometry,
    plan: ArchitecturalPlan,
) -> list[CandidateSpec]:
    """Two candidates per column — grid x-line aligned to the column's
    left edge and to its right edge.

    Aligning to a column edge makes the tile boundary coincide with
    the column boundary, so a seam landing there reads as a
    deliberate detail rather than a wandering cut. The rule layer
    rewards this via R5; this strategy is the corresponding
    generator-side knob.
    """
    if not plan.columns:
        return []
    _, by0, _, _ = geometry.bbox
    specs: list[CandidateSpec] = []
    for col in plan.columns:
        xs = [pt[0] for pt in col.polygon]
        col_left, col_right = min(xs), max(xs)
        specs.append(CandidateSpec(
            candidate_id=f"column_{col.column_id}_left",
            strategy=STRATEGY_COLUMN_ALIGNED,
            description=(
                f"grid x-line aligned to column {col.column_id!r} "
                f"left edge (x={col_left:.0f})"
            ),
            origin=(col_left, by0),
        ))
        specs.append(CandidateSpec(
            candidate_id=f"column_{col.column_id}_right",
            strategy=STRATEGY_COLUMN_ALIGNED,
            description=(
                f"grid x-line aligned to column {col.column_id!r} "
                f"right edge (x={col_right:.0f})"
            ),
            origin=(col_right, by0),
        ))
    return specs
