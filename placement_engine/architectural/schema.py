"""Dataclasses + JSON I/O for the architectural rules layer.

The architectural plan is a small JSON document a designer hand-authors
(or a future preprocessing layer will produce automatically). It tells
the rule engine where the doorways are, where the columns sit, which
parts of the floor are high-visibility, and which matching strategy is
in effect. The plan never owns geometry — it points into the same
coordinate frame as the layout JSON (mm, with the same origin).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Visibility levels.  Ordered most-visible → least-visible so weights
# can be derived by simple lookup. Strings are the canonical form used
# in the JSON; tooling should reference these constants rather than
# string-literal them.
# ---------------------------------------------------------------------------

VISIBILITY_VERY_HIGH: str = "very_high"
VISIBILITY_HIGH: str = "high"
VISIBILITY_MEDIUM: str = "medium"
VISIBILITY_LOW: str = "low"
VISIBILITY_VERY_LOW: str = "very_low"

SUPPORTED_VISIBILITY_LEVELS: tuple[str, ...] = (
    VISIBILITY_VERY_HIGH,
    VISIBILITY_HIGH,
    VISIBILITY_MEDIUM,
    VISIBILITY_LOW,
    VISIBILITY_VERY_LOW,
)

# Visibility → numeric weight used in scoring. Higher weight = bigger
# penalty for things designers don't want in visible areas (small
# pieces, awkward seams). Exposed as a dict for callers that want to
# override defaults — see scoring.py.
DEFAULT_VISIBILITY_WEIGHTS: dict[str, float] = {
    VISIBILITY_VERY_HIGH: 4.0,
    VISIBILITY_HIGH: 3.0,
    VISIBILITY_MEDIUM: 2.0,
    VISIBILITY_LOW: 1.0,
    VISIBILITY_VERY_LOW: 0.25,
}


# ---------------------------------------------------------------------------
# Matching modes
# ---------------------------------------------------------------------------

MATCHING_NONE: str = "none"
MATCHING_VEIN_MATCH: str = "vein_match"
MATCHING_BOOK_MATCH: str = "book_match"

SUPPORTED_MATCHING_MODES: tuple[str, ...] = (
    MATCHING_NONE,
    MATCHING_VEIN_MATCH,
    MATCHING_BOOK_MATCH,
)


# ---------------------------------------------------------------------------
# Defaults — single source of truth for the engine's V1 thresholds
# ---------------------------------------------------------------------------

# "The absolute worst-case minimum piece width is 10 cm." Maps 1:1 to
# the layout's existing SliverPolicy default. Restated here so the
# architectural layer is self-describing.
DEFAULT_MIN_PIECE_WIDTH_MM: float = 100.0
DEFAULT_MIN_PIECE_HEIGHT_MM: float = 100.0
# Hard coverage threshold — any candidate covering less of the target
# area than this fraction is disqualified. Default 99.9% leaves a tiny
# tolerance for boundary-clipping float noise; designers can tighten
# to 1.0 or relax to anything ≥ 0.
DEFAULT_MIN_COVERAGE_RATIO: float = 0.999

# A "small piece" for visibility scoring. Anything whose short bbox
# side falls below this threshold is treated as small and penalised in
# proportion to its space's visibility weight. 300 mm is the V1
# default — slightly larger than the hard minimum so designers see a
# distinction between "uncuttable" and "small but cuttable".
DEFAULT_SMALL_PIECE_THRESHOLD_MM: float = 300.0

# How close to a column a seam has to be (centre-to-edge distance,
# millimetres) to count as "near the column" and earn the column
# reward. Tuned to a slab thickness × a small factor — within 200 mm
# is visually attached to the column.
DEFAULT_COLUMN_SEAM_PROXIMITY_MM: float = 200.0


# ---------------------------------------------------------------------------
# Geometry primitives — kept simple; designer authors them by hand
# ---------------------------------------------------------------------------


Polygon = list[tuple[float, float]]
Segment = tuple[tuple[float, float], tuple[float, float]]


@dataclass
class Space:
    """One architectural space (room, hallway, open-plan area).

    Spaces are the unit of "one direction per space" — every layout
    piece falling inside a space's polygon should share orientation
    with its neighbours in that space. Adjacent connected spaces are
    separate iff a doorway sits between them.

    ``visibility`` drives small-piece penalties: small pieces in
    ``low`` / ``very_low`` spaces are cheap; the same piece in a
    ``high`` / ``very_high`` space is expensive.
    """

    space_id: str
    name: str
    polygon: Polygon
    visibility: str = "medium"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "space_id": self.space_id,
            "name": self.name,
            "polygon": [list(pt) for pt in self.polygon],
            "visibility": self.visibility,
            "notes": list(self.notes),
        }


@dataclass
class Doorway:
    """A door / opening — a line segment a piece-seam should avoid.

    Doorways are line segments (the threshold itself), not polygons.
    The rule engine reports any layout seam that crosses one. Main
    entrances earn an extra penalty weight on violation.
    """

    doorway_id: str
    segment: Segment
    is_main_entrance: bool = False
    name: str = ""
    width_mm: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "doorway_id": self.doorway_id,
            "segment": [list(pt) for pt in self.segment],
            "is_main_entrance": self.is_main_entrance,
            "name": self.name,
            "width_mm": self.width_mm,
            "notes": list(self.notes),
        }


@dataclass
class GuideLine:
    """A primary architectural axis the layout should eventually
    align with (a long wall, a feature line on the floor, the centre
    line of an open span).

    V1 status: schema placeholder only — the rule engine does NOT yet
    align the tile grid to guide lines. The selector surfaces the
    declared lines in its report so designers can see the engine
    acknowledges them, and a later milestone will wire the
    enforcement.
    """

    guide_line_id: str
    segment: Segment
    priority: int = 0
    name: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "guide_line_id": self.guide_line_id,
            "segment": [list(pt) for pt in self.segment],
            "priority": self.priority,
            "name": self.name,
            "notes": list(self.notes),
        }


@dataclass
class Column:
    """A column / pillar — a small obstruction near which seams are
    welcome (the column itself breaks the floor visually, so a seam
    landing on its edge reads as a deliberate detail rather than a
    rough cut)."""

    column_id: str
    polygon: Polygon
    name: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "column_id": self.column_id,
            "polygon": [list(pt) for pt in self.polygon],
            "name": self.name,
            "notes": list(self.notes),
        }


@dataclass
class ArchitecturalPlan:
    """All architectural metadata for one target floor.

    Every field has a sensible default so a plan can omit anything it
    doesn't know yet — the rule engine treats missing metadata as
    "no constraint", never as "constraint violated".
    """

    target_id: str
    spaces: list[Space] = field(default_factory=list)
    doorways: list[Doorway] = field(default_factory=list)
    columns: list[Column] = field(default_factory=list)
    # Designer-declared primary axes. V1: schema placeholder; the
    # selector reports them but the layout layer does not yet align
    # the tile grid to them.
    guide_lines: list[GuideLine] = field(default_factory=list)
    matching_mode: str = MATCHING_NONE
    min_piece_width_mm: float = DEFAULT_MIN_PIECE_WIDTH_MM
    min_piece_height_mm: float = DEFAULT_MIN_PIECE_HEIGHT_MM
    small_piece_threshold_mm: float = DEFAULT_SMALL_PIECE_THRESHOLD_MM
    column_seam_proximity_mm: float = DEFAULT_COLUMN_SEAM_PROXIMITY_MM
    min_coverage_ratio: float = DEFAULT_MIN_COVERAGE_RATIO
    # Free-form notes the designer wants to surface in the report
    # (e.g. "kitchen on east wall — under-cabinet zone TBD").
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "matching_mode": self.matching_mode,
            "min_piece_width_mm": self.min_piece_width_mm,
            "min_piece_height_mm": self.min_piece_height_mm,
            "small_piece_threshold_mm": self.small_piece_threshold_mm,
            "column_seam_proximity_mm": self.column_seam_proximity_mm,
            "min_coverage_ratio": self.min_coverage_ratio,
            "spaces": [s.to_dict() for s in self.spaces],
            "doorways": [d.to_dict() for d in self.doorways],
            "columns": [c.to_dict() for c in self.columns],
            "guide_lines": [g.to_dict() for g in self.guide_lines],
            "notes": list(self.notes),
        }


def write_architectural_plan_json(
    plan: ArchitecturalPlan, path: str | Path,
) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(plan.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return p
