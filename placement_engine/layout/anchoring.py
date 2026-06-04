"""Anchor-mode selection for the tile grid.

A grid laid against a non-aligned floor leaves a leftover strip in
each axis. If that leftover lands on the *wrong* side, designers end
up with thin uncuttable slivers where they would have wanted a clean
edge. This module:

  * formalises the available anchor modes (bottom_left, bottom_right,
    top_left, top_right) and how each maps to a grid origin,
  * defines a sliver policy (minimum cuttable width/height) and a
    per-layout evaluation,
  * generates candidate layouts and ranks them with the same priority
    a designer uses by hand:

      1. fewer uncuttable slivers   (any side below min cuttable size)
      2. fewer total slivers
      3. lower total sliver area
      4. cleaner edge distribution  (larger minimum edge-piece bbox side
                                     — punishes layouts that scatter
                                     leftover into many thin strips)
      5. deterministic tie-break    (alphabetical anchor mode)

Only horizontal modes (``bottom_left`` and ``bottom_right``) are
enabled by default — that covers the headline L-shape case and keeps
the candidate-grid search small. The vertical variants are wired in
so callers can opt into them without API churn.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

# Anchor-mode labels. ``bottom_left`` reproduces the pre-anchoring
# default (origin at the bbox's bottom-left corner).
ANCHOR_BOTTOM_LEFT: str = "bottom_left"
ANCHOR_BOTTOM_RIGHT: str = "bottom_right"
ANCHOR_TOP_LEFT: str = "top_left"
ANCHOR_TOP_RIGHT: str = "top_right"
# Auto-selection sentinel — when passed to the layout generator,
# every mode in ``DEFAULT_CANDIDATE_MODES`` is tried and the best is
# kept.
ANCHOR_AUTO: str = "auto"

SUPPORTED_ANCHOR_MODES: tuple[str, ...] = (
    ANCHOR_BOTTOM_LEFT,
    ANCHOR_BOTTOM_RIGHT,
    ANCHOR_TOP_LEFT,
    ANCHOR_TOP_RIGHT,
)

# V1 candidate set: horizontal anchoring only. Designers care most
# about left-vs-right rhythm; vertical leftover usually lands at the
# top where it's a header strip that the eye reads as a baseboard.
# Vertical variants stay available for explicit override.
DEFAULT_CANDIDATE_MODES: tuple[str, ...] = (
    ANCHOR_BOTTOM_LEFT,
    ANCHOR_BOTTOM_RIGHT,
)

# Designer rule of thumb: anything narrower than ~100 mm cannot be
# safely cut from marble without breaking. 100 mm is conservative;
# fabrication shops typically push it to 150 mm. Configurable via
# ``SliverPolicy``.
DEFAULT_MIN_SLIVER_WIDTH_MM: float = 100.0
DEFAULT_MIN_SLIVER_HEIGHT_MM: float = 100.0


@dataclass
class SliverPolicy:
    """Designer-facing thresholds for "what counts as a sliver".

    A sliver-by-area piece (already flagged in the layout's ``notes``
    via ``DEFAULT_SLIVER_AREA_FRACTION``) is one signal; this policy
    adds a *cuttability* signal: any edge piece whose bbox is below
    these dimensions is flagged ``uncuttable`` regardless of its area.
    """

    min_sliver_width_mm: float = DEFAULT_MIN_SLIVER_WIDTH_MM
    min_sliver_height_mm: float = DEFAULT_MIN_SLIVER_HEIGHT_MM

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_sliver_width_mm": self.min_sliver_width_mm,
            "min_sliver_height_mm": self.min_sliver_height_mm,
        }


@dataclass
class SliverEvaluation:
    """Per-candidate scorecard. Serialised into the layout JSON under
    ``grid.candidate_evaluations`` so designers can audit *why* the
    auto-selector picked a particular anchor."""

    anchor_mode: str
    sliver_count: int                 # pieces flagged ``sliver`` by area
    uncuttable_piece_count: int       # edge pieces below the cuttable threshold
    total_sliver_area_m2: float       # sum of areas of sliver-flagged pieces
    min_sliver_width_mm: float | None
    min_sliver_height_mm: float | None
    # Smallest side (min of width or height) across all edge pieces.
    # Higher = cleaner — leftover concentrated into one wider strip
    # rather than scattered thin ones. ``None`` if no edge pieces.
    min_edge_piece_side_mm: float | None
    edge_piece_count: int
    selected: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "anchor_mode": self.anchor_mode,
            "sliver_count": self.sliver_count,
            "uncuttable_piece_count": self.uncuttable_piece_count,
            "total_sliver_area_m2": round(self.total_sliver_area_m2, 6),
            "min_sliver_width_mm": (
                round(self.min_sliver_width_mm, 3)
                if self.min_sliver_width_mm is not None else None
            ),
            "min_sliver_height_mm": (
                round(self.min_sliver_height_mm, 3)
                if self.min_sliver_height_mm is not None else None
            ),
            "min_edge_piece_side_mm": (
                round(self.min_edge_piece_side_mm, 3)
                if self.min_edge_piece_side_mm is not None else None
            ),
            "edge_piece_count": self.edge_piece_count,
            "selected": self.selected,
        }


def compute_anchor_origin(
    bbox: tuple[float, float, float, float],
    tile_width_mm: float,
    tile_height_mm: float,
    anchor_mode: str,
) -> tuple[float, float]:
    """Map ``anchor_mode`` to the grid origin for a bbox + tile size.

    The bottom-left mode is the engine's historical default: origin
    sits at the bbox's bottom-left corner so leftover strips land at
    the top and right edges. The other three modes shift the origin
    backward by exactly the leftover so the rhythm starts cleanly
    from the chosen corner.
    """
    if tile_width_mm <= 0 or tile_height_mm <= 0:
        raise ValueError(
            f"tile dimensions must be positive; got "
            f"{tile_width_mm}×{tile_height_mm}"
        )
    if anchor_mode not in SUPPORTED_ANCHOR_MODES:
        raise ValueError(
            f"unsupported anchor mode {anchor_mode!r}; "
            f"choose one of {SUPPORTED_ANCHOR_MODES}"
        )

    bx0, by0, bx1, by1 = bbox
    width = bx1 - bx0
    height = by1 - by0
    # leftover lives in [0, tile). When width is an exact multiple of
    # the tile size the leftover is zero and no shift is needed —
    # all four anchor modes collapse to the bottom-left origin.
    leftover_x = width - math.floor(width / tile_width_mm) * tile_width_mm
    leftover_y = height - math.floor(height / tile_height_mm) * tile_height_mm
    shift_x = (tile_width_mm - leftover_x) % tile_width_mm
    shift_y = (tile_height_mm - leftover_y) % tile_height_mm

    if anchor_mode == ANCHOR_BOTTOM_LEFT:
        return (bx0, by0)
    if anchor_mode == ANCHOR_BOTTOM_RIGHT:
        return (bx0 - shift_x, by0)
    if anchor_mode == ANCHOR_TOP_LEFT:
        return (bx0, by0 - shift_y)
    # ANCHOR_TOP_RIGHT
    return (bx0 - shift_x, by0 - shift_y)


def evaluate_layout(
    layout: Any,  # avoid a circular import; this is always a LayoutResult
    *,
    anchor_mode: str,
    policy: SliverPolicy,
) -> SliverEvaluation:
    """Compute the sliver-evaluation scorecard for a given layout.

    Two distinct "bad-piece" signals are tracked:

    * ``sliver`` — pieces the grid generator already flagged because
      their area is below ``DEFAULT_SLIVER_AREA_FRACTION`` of nominal.
    * ``uncuttable`` — any edge piece whose bbox is narrower than the
      policy's minimum cuttable side. This catches strips that pass
      the area threshold (e.g. a 100×1800 mm strip is ~10% of a
      1590×2200 tile so not a sliver-by-area) but still can't safely
      be cut.

    The two sets overlap heavily but neither is a subset of the other.
    """
    slivers = [p for p in layout.pieces if "sliver" in p.notes]
    uncuttable_edges = [
        p for p in layout.pieces
        if p.is_edge_piece
        and (
            p.bounding_width_mm < policy.min_sliver_width_mm
            or p.bounding_height_mm < policy.min_sliver_height_mm
        )
    ]
    edge_pieces = [p for p in layout.pieces if p.is_edge_piece]
    min_edge_side: float | None = None
    if edge_pieces:
        min_edge_side = min(
            min(p.bounding_width_mm, p.bounding_height_mm)
            for p in edge_pieces
        )

    return SliverEvaluation(
        anchor_mode=anchor_mode,
        sliver_count=len(slivers),
        uncuttable_piece_count=len(uncuttable_edges),
        total_sliver_area_m2=sum(p.actual_area_m2 for p in slivers),
        min_sliver_width_mm=(
            min(p.bounding_width_mm for p in slivers) if slivers else None
        ),
        min_sliver_height_mm=(
            min(p.bounding_height_mm for p in slivers) if slivers else None
        ),
        min_edge_piece_side_mm=min_edge_side,
        edge_piece_count=len(edge_pieces),
    )


def score_evaluation(ev: SliverEvaluation) -> tuple:
    """Return a sort key; lower is better.

    The order encodes the designer's own priority list verbatim. The
    final ``anchor_mode`` field is the deterministic tie-break so the
    selector is reproducible across runs even when two candidates are
    indistinguishable on every cutting-relevant metric.
    """
    return (
        ev.uncuttable_piece_count,
        ev.sliver_count,
        # Round to micrometres² to absorb Shapely's float noise on
        # otherwise-identical areas.
        round(ev.total_sliver_area_m2, 6),
        # Negate so "higher minimum edge side" sorts to the front.
        # ``None`` is treated as 0 — a layout with no edge pieces at
        # all is maximally clean, but in practice this only triggers
        # for exact-divisor floors where every anchor ties anyway.
        -(ev.min_edge_piece_side_mm or 0.0),
        ev.anchor_mode,
    )
