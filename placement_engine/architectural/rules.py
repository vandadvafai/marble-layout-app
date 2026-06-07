"""Architectural rule evaluator + design score.

Given a layout JSON + an architectural plan, produce a structured
`RuleReport` that lists, for each rule, whether the layout passes,
violates, or partially-satisfies the rule. The report also carries
per-piece and per-seam evaluations and a single weighted design
score so two candidate layouts can be compared at a glance.

V1 rule coverage:

  R1  no piece below the policy's minimum cuttable side       [hard]
  R2  no seams cross a doorway                                [strong]
  R3  one orientation per space (informational)               [strong]
  R4  matching mode honoured (informational)                  [strong]
  R5  prefer seams near columns                               [soft +]
  R6  small pieces should land in low-visibility spaces       [soft]
  R7  full slabs in doorways                                  [soft +]
  R8  pieces with absorbed slivers are flagged                [info]

Hard violations subtract from the design score; soft violations cost
fewer points; rewards add to the score. Each rule's individual
contribution shows up in ``RuleReport.score_breakdown`` so designers
can see *why* a layout scored what it did.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shapely.geometry import LineString, Point, Polygon as ShPolygon
from shapely.geometry.base import BaseGeometry

from placement_engine.architectural.schema import (
    DEFAULT_VISIBILITY_WEIGHTS,
    MATCHING_NONE,
    VISIBILITY_MEDIUM,
    ArchitecturalPlan,
    Column,
    Doorway,
    Space,
)
from placement_engine.architectural.seams import Seam, detect_seams

# ---------------------------------------------------------------------------
# rule identifiers — exposed as constants for tests and reports
# ---------------------------------------------------------------------------

RULE_MIN_PIECE_SIZE: str = "R1_min_piece_size"
RULE_NO_SEAMS_IN_DOORWAYS: str = "R2_no_seams_in_doorways"
RULE_ONE_DIRECTION_PER_SPACE: str = "R3_one_direction_per_space"
RULE_MATCHING_MODE: str = "R4_matching_mode"
RULE_SEAMS_NEAR_COLUMNS: str = "R5_seams_near_columns"
RULE_SMALL_PIECES_IN_LOW_VISIBILITY: str = "R6_small_pieces_in_low_visibility"
RULE_FULL_SLABS_IN_DOORWAYS: str = "R7_full_slabs_in_doorways"
RULE_ABSORBED_SLIVERS: str = "R8_absorbed_slivers"
# Coverage is the second hard rule. The layout layer can technically
# produce partial-coverage layouts (e.g. when a candidate origin shifts
# tiles off the bbox left edge), but those layouts leave bare floor —
# unacceptable for a real install. Same penalty weight as R1 so any
# coverage shortfall lands the candidate firmly in the invalid pool.
RULE_FULL_COVERAGE: str = "R9_full_coverage"

# rule status labels
STATUS_PASS: str = "pass"
STATUS_VIOLATION: str = "violation"
STATUS_REWARD: str = "reward"           # soft rule that adds to the score
STATUS_INFO: str = "info"               # no pass/fail concept (matching mode)
STATUS_NOT_APPLICABLE: str = "not_applicable"

# ---------------------------------------------------------------------------
# scoring weights
# ---------------------------------------------------------------------------

# Hard violation: 100 points each. Below the minimum cuttable side
# means the piece is fabrication-impossible — should never happen
# post-absorption, so we want a giant red flag if it does.
_PENALTY_BELOW_MIN: float = 100.0

# Coverage shortfall — also a hard violation. One penalty per
# candidate (the rule is binary: either the layout covers the floor
# or it doesn't).
_PENALTY_COVERAGE_SHORTFALL: float = 100.0

# Doorway seam crossing — strong constraint; main entrance is worse.
_PENALTY_DOORWAY_SEAM: float = 25.0
_PENALTY_DOORWAY_SEAM_MAIN: float = 50.0

# Seam near column — soft reward.
_REWARD_COLUMN_SEAM: float = 5.0

# Small piece in a space — penalty scales with the space's visibility
# weight (see schema.DEFAULT_VISIBILITY_WEIGHTS). Base multiplier is
# this value; very-low visibility multiplies by 0.25, very-high by 4.
_PENALTY_SMALL_PIECE_BASE: float = 4.0

# Full slab covering a doorway — soft reward.
_REWARD_FULL_SLAB_DOORWAY: float = 8.0

# Starting design score — every layout starts at 100 and accumulates
# penalties / rewards. Capped to [0, 200] at the end for readability.
_STARTING_SCORE: float = 100.0


# ---------------------------------------------------------------------------
# evaluation dataclasses
# ---------------------------------------------------------------------------


@dataclass
class PieceEvaluation:
    """Per-piece architectural classification."""

    piece_id: str
    zone_id: str
    space_id: str | None
    visibility: str
    bbox_width_mm: float
    bbox_height_mm: float
    is_below_min: bool
    is_small: bool
    is_absorbed_holder: bool
    crosses_doorway: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "piece_id": self.piece_id,
            "zone_id": self.zone_id,
            "space_id": self.space_id,
            "visibility": self.visibility,
            "bbox_width_mm": round(self.bbox_width_mm, 3),
            "bbox_height_mm": round(self.bbox_height_mm, 3),
            "is_below_min": self.is_below_min,
            "is_small": self.is_small,
            "is_absorbed_holder": self.is_absorbed_holder,
            "crosses_doorway": self.crosses_doorway,
        }


@dataclass
class SeamEvaluation:
    """Per-seam architectural classification."""

    seam_id: str
    piece_a_id: str
    piece_b_id: str
    length_mm: float
    # Doorway crossings + the doorways crossed (their IDs).
    crosses_doorways: list[str] = field(default_factory=list)
    crosses_main_entrance: bool = False
    # Columns the seam is within proximity of (their IDs).
    near_columns: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seam_id": self.seam_id,
            "piece_a_id": self.piece_a_id,
            "piece_b_id": self.piece_b_id,
            "length_mm": round(self.length_mm, 3),
            "crosses_doorways": list(self.crosses_doorways),
            "crosses_main_entrance": self.crosses_main_entrance,
            "near_columns": list(self.near_columns),
        }


@dataclass
class RuleResult:
    """One rule's verdict."""

    rule_id: str
    status: str
    # Optional headline count (e.g. # of doorway crossings).
    count: int = 0
    # Free-form explanation, designer-facing.
    message: str = ""
    # IDs of the pieces / seams / doorways implicated in this rule.
    affected_ids: list[str] = field(default_factory=list)
    # Score contribution (signed — negative for violations, positive
    # for rewards). Always reflected in ``score_breakdown``.
    score_delta: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "status": self.status,
            "count": self.count,
            "message": self.message,
            "affected_ids": list(self.affected_ids),
            "score_delta": round(self.score_delta, 3),
        }


@dataclass
class RuleReport:
    """The full architectural report."""

    target_id: str
    target_name: str
    source_layout_path: str
    source_cut_list_path: str
    architectural_plan: ArchitecturalPlan
    pieces: list[PieceEvaluation] = field(default_factory=list)
    seams: list[SeamEvaluation] = field(default_factory=list)
    rules: list[RuleResult] = field(default_factory=list)
    design_score: float = 0.0
    score_breakdown: dict[str, float] = field(default_factory=dict)
    # Quick top-level counts so a summary can skim without re-walking.
    hard_violation_count: int = 0
    soft_violation_count: int = 0
    reward_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "target_name": self.target_name,
            "source_layout_path": self.source_layout_path,
            "source_cut_list_path": self.source_cut_list_path,
            "architectural_plan": self.architectural_plan.to_dict(),
            "pieces": [p.to_dict() for p in self.pieces],
            "seams": [s.to_dict() for s in self.seams],
            "rules": [r.to_dict() for r in self.rules],
            "design_score": round(self.design_score, 3),
            "score_breakdown": {
                k: round(v, 3) for k, v in self.score_breakdown.items()
            },
            "hard_violation_count": self.hard_violation_count,
            "soft_violation_count": self.soft_violation_count,
            "reward_count": self.reward_count,
        }

    def summary_dict(self) -> dict[str, Any]:
        """One-page summary for fabrication / dashboard consumers."""
        return {
            "target_id": self.target_id,
            "design_score": round(self.design_score, 3),
            "hard_violation_count": self.hard_violation_count,
            "soft_violation_count": self.soft_violation_count,
            "reward_count": self.reward_count,
            "score_breakdown": {
                k: round(v, 3) for k, v in self.score_breakdown.items()
            },
            "rule_status": {r.rule_id: r.status for r in self.rules},
        }


# ---------------------------------------------------------------------------
# main entry point
# ---------------------------------------------------------------------------


def evaluate_layout(
    layout: dict[str, Any] | str | Path,
    cut_list: dict[str, Any] | str | Path,
    plan: ArchitecturalPlan,
    *,
    visibility_weights: dict[str, float] | None = None,
) -> RuleReport:
    """Evaluate a layout against an architectural plan.

    ``layout`` and ``cut_list`` accept either an already-parsed dict
    or a path-to-JSON. The plan is the dataclass from ``loader.py``.

    ``visibility_weights`` overrides the default weight table.
    """
    layout_dict, layout_path = _load_json(layout)
    cut_list_dict, cl_path = _load_json(cut_list)
    weights = visibility_weights or DEFAULT_VISIBILITY_WEIGHTS

    target = layout_dict.get("target", {})
    pieces_raw = layout_dict.get("pieces", [])

    # Pre-compute per-piece Shapely geometry once.
    piece_polygons: dict[str, ShPolygon] = {}
    for p in pieces_raw:
        ext = p.get("actual_cut_polygon") or []
        if len(ext) >= 3:
            try:
                piece_polygons[str(p.get("piece_id", ""))] = ShPolygon(ext)
            except Exception:
                # Malformed — let the rule loop classify it as "below min".
                pass

    space_polygons: list[tuple[Space, ShPolygon]] = []
    for sp in plan.spaces:
        if len(sp.polygon) >= 3:
            try:
                space_polygons.append((sp, ShPolygon(sp.polygon)))
            except Exception:
                pass

    doorway_lines: list[tuple[Doorway, LineString]] = []
    for dr in plan.doorways:
        doorway_lines.append((dr, LineString([dr.segment[0], dr.segment[1]])))

    column_polygons: list[tuple[Column, ShPolygon]] = []
    for col in plan.columns:
        if len(col.polygon) >= 3:
            try:
                column_polygons.append((col, ShPolygon(col.polygon)))
            except Exception:
                pass

    # ------------------------------------------------------------------
    # piece evaluations
    # ------------------------------------------------------------------
    piece_evals: list[PieceEvaluation] = []
    for p in pieces_raw:
        bbox_w = float(p.get("bounding_width_mm", 0.0))
        bbox_h = float(p.get("bounding_height_mm", 0.0))
        is_below = (
            bbox_w < plan.min_piece_width_mm
            or bbox_h < plan.min_piece_height_mm
        )
        is_small = (
            min(bbox_w, bbox_h) < plan.small_piece_threshold_mm
        )
        space_id, visibility = _piece_space(p, piece_polygons, space_polygons)
        piece_evals.append(PieceEvaluation(
            piece_id=str(p.get("piece_id", "")),
            zone_id=str(p.get("zone_id", "")),
            space_id=space_id,
            visibility=visibility,
            bbox_width_mm=bbox_w,
            bbox_height_mm=bbox_h,
            is_below_min=is_below,
            is_small=is_small,
            is_absorbed_holder=any(
                str(n).startswith("absorbed_sliver:")
                for n in p.get("notes", [])
            ),
            crosses_doorway=_piece_crosses_doorway(
                piece_polygons.get(str(p.get("piece_id", ""))),
                doorway_lines,
            ),
        ))

    # ------------------------------------------------------------------
    # seam detection + evaluation
    # ------------------------------------------------------------------
    seams = detect_seams(pieces_raw)
    seam_evals: list[SeamEvaluation] = []
    for seam in seams:
        seam_line = LineString(seam.coords) if len(seam.coords) >= 2 else None
        crosses: list[str] = []
        crosses_main = False
        near_cols: list[str] = []
        if seam_line is not None:
            for dr, dr_line in doorway_lines:
                if _seam_crosses_doorway(seam_line, dr_line):
                    crosses.append(dr.doorway_id)
                    if dr.is_main_entrance:
                        crosses_main = True
            for col, col_poly in column_polygons:
                if seam_line.distance(col_poly) <= plan.column_seam_proximity_mm:
                    near_cols.append(col.column_id)
        seam_evals.append(SeamEvaluation(
            seam_id=seam.seam_id,
            piece_a_id=seam.piece_a_id,
            piece_b_id=seam.piece_b_id,
            length_mm=seam.length_mm,
            crosses_doorways=crosses,
            crosses_main_entrance=crosses_main,
            near_columns=near_cols,
        ))

    # ------------------------------------------------------------------
    # rules
    # ------------------------------------------------------------------
    rules: list[RuleResult] = []
    score = _STARTING_SCORE
    breakdown: dict[str, float] = {}

    # R1 — min piece size (hard)
    below = [pe for pe in piece_evals if pe.is_below_min]
    delta = -_PENALTY_BELOW_MIN * len(below)
    rules.append(RuleResult(
        rule_id=RULE_MIN_PIECE_SIZE,
        status=STATUS_PASS if not below else STATUS_VIOLATION,
        count=len(below),
        message=(
            f"All {len(piece_evals)} pieces are at or above the "
            f"{plan.min_piece_width_mm:.0f} mm minimum cuttable side."
            if not below else
            f"{len(below)} piece(s) below the "
            f"{plan.min_piece_width_mm:.0f} mm minimum cuttable side."
        ),
        affected_ids=[pe.piece_id for pe in below],
        score_delta=delta,
    ))
    breakdown[RULE_MIN_PIECE_SIZE] = delta
    score += delta

    # R9 — full coverage (hard). Compare the layout's pre-computed
    # coverage_percentage against the plan's min_coverage_ratio
    # (default 99.9%). Partial-coverage layouts leave bare floor and
    # are not real candidates — disqualify them.
    derived = layout_dict.get("derived", {})
    coverage_pct = float(derived.get("coverage_percentage", 0.0))
    coverage_ratio = coverage_pct / 100.0
    min_ratio = float(plan.min_coverage_ratio)
    coverage_short = coverage_ratio + 1e-9 < min_ratio
    delta = -_PENALTY_COVERAGE_SHORTFALL if coverage_short else 0.0
    rules.append(RuleResult(
        rule_id=RULE_FULL_COVERAGE,
        status=STATUS_VIOLATION if coverage_short else STATUS_PASS,
        count=1 if coverage_short else 0,
        message=(
            f"Coverage {coverage_ratio * 100:.2f}% is below the "
            f"required {min_ratio * 100:.2f}% — the layout leaves "
            f"bare floor."
            if coverage_short else
            f"Coverage {coverage_ratio * 100:.2f}% meets or exceeds "
            f"the required {min_ratio * 100:.2f}%."
        ),
        score_delta=delta,
    ))
    breakdown[RULE_FULL_COVERAGE] = delta
    score += delta

    # R2 — no seams in doorways (strong)
    door_crossings = [se for se in seam_evals if se.crosses_doorways]
    main_crossings = [se for se in door_crossings if se.crosses_main_entrance]
    delta = (
        -_PENALTY_DOORWAY_SEAM * (len(door_crossings) - len(main_crossings))
        - _PENALTY_DOORWAY_SEAM_MAIN * len(main_crossings)
    )
    if not plan.doorways:
        rules.append(RuleResult(
            rule_id=RULE_NO_SEAMS_IN_DOORWAYS,
            status=STATUS_NOT_APPLICABLE,
            message="No doorways defined in the architectural plan.",
            score_delta=0.0,
        ))
        breakdown[RULE_NO_SEAMS_IN_DOORWAYS] = 0.0
    else:
        rules.append(RuleResult(
            rule_id=RULE_NO_SEAMS_IN_DOORWAYS,
            status=STATUS_PASS if not door_crossings else STATUS_VIOLATION,
            count=len(door_crossings),
            message=(
                f"No seams cross any of {len(plan.doorways)} doorway(s)."
                if not door_crossings else
                f"{len(door_crossings)} seam(s) cross a doorway "
                f"({len(main_crossings)} on the main entrance)."
            ),
            affected_ids=[se.seam_id for se in door_crossings],
            score_delta=delta,
        ))
        breakdown[RULE_NO_SEAMS_IN_DOORWAYS] = delta
        score += delta

    # R3 — one direction per space (informational)
    # Group pieces by space; record whether all pieces in a space share
    # the same nominal width × height (rotation isn't tracked yet, so
    # this is a pass-or-info rule for V1).
    pieces_by_space: dict[str, set[tuple[float, float]]] = {}
    for pe in piece_evals:
        if pe.space_id is None:
            continue
        # Look up nominal dims from the raw piece.
        raw = next(
            (p for p in pieces_raw if str(p.get("piece_id", "")) == pe.piece_id),
            None,
        )
        if raw is None:
            continue
        nw = round(float(raw.get("nominal_width_mm", 0.0)), 3)
        nh = round(float(raw.get("nominal_height_mm", 0.0)), 3)
        pieces_by_space.setdefault(pe.space_id, set()).add((nw, nh))
    mixed_spaces = [s for s, dims in pieces_by_space.items() if len(dims) > 1]
    rules.append(RuleResult(
        rule_id=RULE_ONE_DIRECTION_PER_SPACE,
        status=STATUS_PASS if not mixed_spaces else STATUS_VIOLATION,
        count=len(mixed_spaces),
        message=(
            "Every space uses a single tile orientation."
            if not mixed_spaces else
            f"{len(mixed_spaces)} space(s) contain pieces of more than one "
            "nominal dimension — orientation may be inconsistent."
        ),
        affected_ids=mixed_spaces,
        score_delta=0.0,  # informational in V1
    ))
    breakdown[RULE_ONE_DIRECTION_PER_SPACE] = 0.0

    # R4 — matching mode (informational in V1)
    if plan.matching_mode == MATCHING_NONE:
        rules.append(RuleResult(
            rule_id=RULE_MATCHING_MODE,
            status=STATUS_PASS,
            message='matching_mode="none" — no vein or book-match constraints.',
        ))
    else:
        rules.append(RuleResult(
            rule_id=RULE_MATCHING_MODE,
            status=STATUS_INFO,
            message=(
                f'matching_mode="{plan.matching_mode}" requested but '
                "not yet enforced by the engine — slab orientation "
                "tracking is on the roadmap."
            ),
        ))
    breakdown[RULE_MATCHING_MODE] = 0.0

    # R5 — seams near columns (soft reward)
    column_rewards = [se for se in seam_evals if se.near_columns]
    delta = _REWARD_COLUMN_SEAM * len(column_rewards)
    if not plan.columns:
        rules.append(RuleResult(
            rule_id=RULE_SEAMS_NEAR_COLUMNS,
            status=STATUS_NOT_APPLICABLE,
            message="No columns defined in the architectural plan.",
            score_delta=0.0,
        ))
        breakdown[RULE_SEAMS_NEAR_COLUMNS] = 0.0
    else:
        rules.append(RuleResult(
            rule_id=RULE_SEAMS_NEAR_COLUMNS,
            status=STATUS_REWARD if column_rewards else STATUS_PASS,
            count=len(column_rewards),
            message=(
                f"{len(column_rewards)} seam(s) land within "
                f"{plan.column_seam_proximity_mm:.0f} mm of a column — "
                "natural break points."
                if column_rewards else
                f"No seams within {plan.column_seam_proximity_mm:.0f} mm "
                "of any column."
            ),
            affected_ids=[se.seam_id for se in column_rewards],
            score_delta=delta,
        ))
        breakdown[RULE_SEAMS_NEAR_COLUMNS] = delta
        score += delta

    # R6 — small pieces in low-visibility spaces (soft, visibility-weighted)
    small_pieces = [pe for pe in piece_evals if pe.is_small and not pe.is_below_min]
    small_delta = 0.0
    for pe in small_pieces:
        weight = weights.get(pe.visibility, weights.get(VISIBILITY_MEDIUM, 2.0))
        small_delta -= _PENALTY_SMALL_PIECE_BASE * weight / 4.0  # normalize
    rules.append(RuleResult(
        rule_id=RULE_SMALL_PIECES_IN_LOW_VISIBILITY,
        status=STATUS_PASS if not small_pieces else STATUS_VIOLATION,
        count=len(small_pieces),
        message=(
            f"No pieces below the "
            f"{plan.small_piece_threshold_mm:.0f} mm small-piece "
            "threshold."
            if not small_pieces else
            f"{len(small_pieces)} small piece(s); penalty scales with "
            "each piece's space visibility."
        ),
        affected_ids=[pe.piece_id for pe in small_pieces],
        score_delta=small_delta,
    ))
    breakdown[RULE_SMALL_PIECES_IN_LOW_VISIBILITY] = small_delta
    score += small_delta

    # R7 — full slabs covering doorways (soft reward)
    # Designer intent: prefer a SINGLE slab spanning the entire
    # doorway opening (no seam in the threshold). Per doorway,
    # we look for one piece whose footprint contains the full
    # doorway segment — i.e. the piece spans from before the
    # doorway's first endpoint to after its second, *and* it
    # extends across the threshold so the slab actually sits
    # over the opening. Two adjacent tiles each covering half do
    # NOT qualify: that's a seam in the doorway (caught by R2).
    spanning_by_doorway: dict[str, str] = {}
    for dr, dr_line in doorway_lines:
        for pe in piece_evals:
            if pe.is_below_min or pe.is_absorbed_holder:
                continue
            piece_poly = piece_polygons.get(pe.piece_id)
            if piece_poly is None:
                continue
            if _piece_fully_spans_doorway(piece_poly, dr_line):
                spanning_by_doorway[dr.doorway_id] = pe.piece_id
                break  # one piece per doorway is enough
    spanning_piece_ids = list(spanning_by_doorway.values())
    delta = _REWARD_FULL_SLAB_DOORWAY * len(spanning_by_doorway)
    if not plan.doorways:
        rules.append(RuleResult(
            rule_id=RULE_FULL_SLABS_IN_DOORWAYS,
            status=STATUS_NOT_APPLICABLE,
            message="No doorways defined in the architectural plan.",
            score_delta=0.0,
        ))
        breakdown[RULE_FULL_SLABS_IN_DOORWAYS] = 0.0
    else:
        n_spanned = len(spanning_by_doorway)
        n_doorways = len(plan.doorways)
        rules.append(RuleResult(
            rule_id=RULE_FULL_SLABS_IN_DOORWAYS,
            status=(
                STATUS_REWARD if n_spanned else STATUS_PASS
            ),
            count=n_spanned,
            message=(
                f"{n_spanned} of {n_doorways} doorway(s) covered by a "
                f"single slab spanning the full opening."
                if n_spanned else
                f"None of {n_doorways} doorway(s) covered by a single "
                f"slab spanning the full opening (designers may want "
                f"to revisit the layout)."
            ),
            affected_ids=spanning_piece_ids,
            score_delta=delta,
        ))
        breakdown[RULE_FULL_SLABS_IN_DOORWAYS] = delta
        score += delta

    # R8 — absorbed slivers (informational — designers want to know
    # which pieces ended up bigger than nominal because of absorption)
    holders = [pe for pe in piece_evals if pe.is_absorbed_holder]
    rules.append(RuleResult(
        rule_id=RULE_ABSORBED_SLIVERS,
        status=STATUS_INFO if holders else STATUS_NOT_APPLICABLE,
        count=len(holders),
        message=(
            f"{len(holders)} piece(s) absorbed a neighbour sliver — "
            "they will need a slab wider/taller than the nominal tile."
            if holders else
            "No sliver absorption occurred on this layout."
        ),
        affected_ids=[pe.piece_id for pe in holders],
        score_delta=0.0,
    ))
    breakdown[RULE_ABSORBED_SLIVERS] = 0.0

    _HARD_RULES = {RULE_MIN_PIECE_SIZE, RULE_FULL_COVERAGE}
    hard_count = sum(
        1 for r in rules
        if r.status == STATUS_VIOLATION and r.rule_id in _HARD_RULES
    )
    soft_count = sum(
        1 for r in rules
        if r.status == STATUS_VIOLATION and r.rule_id not in _HARD_RULES
    )
    reward_count = sum(1 for r in rules if r.status == STATUS_REWARD)

    # Clamp the score so it stays readable.
    score = max(0.0, min(score, 200.0))

    return RuleReport(
        target_id=str(target.get("target_id", plan.target_id)),
        target_name=str(target.get("name", "")),
        source_layout_path=str(layout_path) if layout_path else "",
        source_cut_list_path=str(cl_path) if cl_path else "",
        architectural_plan=plan,
        pieces=piece_evals,
        seams=seam_evals,
        rules=rules,
        design_score=score,
        score_breakdown=breakdown,
        hard_violation_count=hard_count,
        soft_violation_count=soft_count,
        reward_count=reward_count,
    )


# ---------------------------------------------------------------------------
# JSON writers
# ---------------------------------------------------------------------------


def write_rule_report_json(report: RuleReport, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return p


def write_rule_report_summary_json(
    report: RuleReport, path: str | Path,
) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(report.summary_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return p


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _load_json(
    data: dict[str, Any] | str | Path,
) -> tuple[dict[str, Any], Path | None]:
    if isinstance(data, dict):
        return data, None
    p = Path(data)
    if not p.exists():
        raise FileNotFoundError(f"input JSON not found: {p}")
    return json.loads(p.read_text(encoding="utf-8")), p


def _piece_space(
    piece: dict[str, Any],
    piece_polygons: dict[str, ShPolygon],
    space_polygons: list[tuple[Space, ShPolygon]],
) -> tuple[str | None, str]:
    """Return (space_id, visibility) for a piece by centroid containment.

    Falls back to ``(None, "medium")`` when no space contains the piece
    centroid — typical when the plan declares zero spaces, in which
    case every piece gets medium-visibility scoring.
    """
    if not space_polygons:
        return None, VISIBILITY_MEDIUM
    shp = piece_polygons.get(str(piece.get("piece_id", "")))
    if shp is None or shp.is_empty:
        return None, VISIBILITY_MEDIUM
    centroid: Point = shp.centroid
    for space, poly in space_polygons:
        if poly.contains(centroid) or poly.touches(centroid):
            return space.space_id, space.visibility
    # Centroid outside every declared space — could happen if the plan
    # doesn't cover the full polygon. Defensive default.
    return None, VISIBILITY_MEDIUM


def _piece_crosses_doorway(
    piece_poly: ShPolygon | None,
    doorway_lines: list[tuple[Doorway, LineString]],
) -> bool:
    if piece_poly is None or not doorway_lines:
        return False
    for _, line in doorway_lines:
        if piece_poly.intersects(line):
            return True
    return False


def _seam_crosses_doorway(
    seam_line: LineString, doorway_line: LineString,
) -> bool:
    """A seam crosses a doorway iff it intersects the doorway segment
    *inside the opening* — not just at one of the doorway's endpoints
    (the wall corners).

    Three cases qualify:

    1. Shapely.crosses — true interior-to-interior crossing.
    2. Co-linear overlap — the seam runs along the doorway segment.
    3. Point intersection where the seam touches the doorway segment
       at a point strictly inside the opening (e.g. a vertical seam
       between two adjacent floor tiles emanating from a point in the
       middle of the threshold). Shapely.crosses misses this case
       because the touch is at one of the seam's endpoints, but a
       seam landing in the middle of the opening IS a conflict.
    """
    if seam_line.is_empty or doorway_line.is_empty:
        return False
    if seam_line.crosses(doorway_line):
        return True
    inter = seam_line.intersection(doorway_line)
    if inter.is_empty:
        return False
    # Co-linear overlap — non-zero length intersection.
    if getattr(inter, "length", 0.0) > 0.0:
        return True
    # Point intersection — does the touch point fall strictly inside
    # the doorway segment (not at a wall corner)?
    if inter.geom_type == "Point":
        return _point_strictly_inside_segment(inter, doorway_line)
    return False


def _point_strictly_inside_segment(
    point: Point, segment: LineString, tol: float = 1.0,
) -> bool:
    """True iff ``point`` lies on ``segment`` and is not within ``tol``
    millimetres of either endpoint. We use a 1 mm tolerance because
    grid origins and column coordinates can have float noise on that
    order; anything inside the segment by more than 1 mm is a real
    interior touch.
    """
    coords = list(segment.coords)
    if len(coords) < 2:
        return False
    x0, y0 = coords[0]
    x1, y1 = coords[-1]
    px, py = point.x, point.y
    d0 = ((px - x0) ** 2 + (py - y0) ** 2) ** 0.5
    d1 = ((px - x1) ** 2 + (py - y1) ** 2) ** 0.5
    return d0 > tol and d1 > tol


def _piece_fully_spans_doorway(
    piece_poly: ShPolygon, doorway_line: LineString,
) -> bool:
    """True iff the piece's footprint covers the entire doorway
    segment as a single contiguous chunk.

    We measure the length of the intersection between the piece
    polygon and the doorway line segment and require it to equal
    the full doorway length (within 1 mm). This catches the
    "single slab spans the opening" case while excluding the
    "two adjacent slabs each cover half" case — which would
    produce two separate intersection segments, neither of full
    length on its own.
    """
    if piece_poly is None or doorway_line.is_empty:
        return False
    door_length = doorway_line.length
    if door_length <= 0:
        return False
    inter = piece_poly.intersection(doorway_line)
    if inter.is_empty:
        return False
    return getattr(inter, "length", 0.0) >= door_length - 1.0
