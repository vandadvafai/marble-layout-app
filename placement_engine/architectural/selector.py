"""Architectural-aware candidate layout selection.

The architectural layer used to consume a single generated layout and
emit a rule report. This module promotes it into a **chooser**:
generate several candidate layouts, evaluate each, and pick the one
the architectural rules approve of most strongly.

V1 candidate strategy:

  * one candidate per **global anchor mode** in
    ``DEFAULT_CANDIDATE_ANCHOR_MODES`` (bottom_left, bottom_right,
    top_left, top_right). The anchor is applied uniformly across
    every zone.
  * **Hard disqualification**: any candidate producing a piece below
    the plan's ``min_piece_*_mm`` (R1) is rejected, even if its
    design_score would be highest. The winner is picked from the
    valid pool only.
  * If every candidate fails the hard gate, the selector falls back
    to the least-bad candidate and records the fallback explicitly
    in ``selection_reason``.
  * Tie-break: higher design_score wins; alphabetical candidate_id
    is the deterministic last resort.

**V1 limitation — global anchoring.** Each candidate applies the
same anchor mode to every zone. Real designers would let separate
spaces pick independent anchors (a hallway running along one wall, a
living room aligned to the entrance). A future milestone will
enumerate per-zone anchor combinations (4^N combinations for N zones,
pruned by per-zone hard rules so fan-out stays tractable). The
function signature already accepts an arbitrary
``candidate_anchor_modes`` tuple so the extension is non-breaking.

Out of scope: vein_match / book_match enforcement, guide-line
alignment behaviour (schema placeholder only), DXF export, UI.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from placement_engine.architectural.candidates import (
    CandidateSpec,
    STRATEGY_ANCHOR,
    enumerate_candidate_specs,
)
from placement_engine.architectural.rules import (
    RULE_FULL_COVERAGE,
    RULE_MIN_PIECE_SIZE,
    RuleReport,
    STATUS_VIOLATION,
    evaluate_layout,
    write_rule_report_json,
)
from placement_engine.architectural.schema import (
    ArchitecturalPlan,
    DEFAULT_VISIBILITY_WEIGHTS,
)
from placement_engine.layout import (
    ANCHOR_AUTO,
    ANCHOR_BOTTOM_LEFT,
    ANCHOR_BOTTOM_RIGHT,
    ANCHOR_TOP_LEFT,
    ANCHOR_TOP_RIGHT,
    LayoutResult,
    compute_inventory_dimension_summary,
    generate_tile_layout_from_inventory,
    write_layout_json,
)
from placement_engine.target_area.dxf_target import TargetGeometry

# Retained for backward compatibility — the default specs always
# include these four anchor candidates (see
# ``placement_engine.architectural.candidates._anchor_specs``).
DEFAULT_CANDIDATE_ANCHOR_MODES: tuple[str, ...] = (
    ANCHOR_BOTTOM_LEFT,
    ANCHOR_BOTTOM_RIGHT,
    ANCHOR_TOP_LEFT,
    ANCHOR_TOP_RIGHT,
)


# ---------------------------------------------------------------------------
# dataclasses
# ---------------------------------------------------------------------------


@dataclass
class LayoutCandidate:
    """One candidate layout + its rule report.

    ``is_valid`` reflects the hard-rule gate. ``disqualifications``
    lists the rule IDs that triggered the rejection (empty when valid).
    ``strategy`` and ``description`` come from the originating
    ``CandidateSpec``: strategy is the high-level category (one of
    ``placement_engine.architectural.candidates.SUPPORTED_STRATEGIES``)
    and description is a human-readable one-liner.
    """

    candidate_id: str
    anchor_mode: str
    layout: LayoutResult
    report: RuleReport
    is_valid: bool
    disqualifications: list[str] = field(default_factory=list)
    strategy: str = STRATEGY_ANCHOR
    description: str = ""

    @property
    def design_score(self) -> float:
        return self.report.design_score

    @property
    def hard_violation_count(self) -> int:
        return self.report.hard_violation_count

    @property
    def soft_violation_count(self) -> int:
        return self.report.soft_violation_count

    @property
    def reward_count(self) -> int:
        return self.report.reward_count

    def summary_dict(self) -> dict[str, Any]:
        """Compact comparison view — drops the heavy pieces[] / seams[]
        arrays from the full report and surfaces the headline
        counts designers want side-by-side."""
        rep = self.report
        doorway_conflicts = [
            {
                "seam_id": s.seam_id,
                "doorway_ids": list(s.crosses_doorways),
                "crosses_main_entrance": s.crosses_main_entrance,
            }
            for s in rep.seams if s.crosses_doorways
        ]
        pieces_below_min = [
            {
                "piece_id": p.piece_id,
                "bbox_width_mm": round(p.bbox_width_mm, 3),
                "bbox_height_mm": round(p.bbox_height_mm, 3),
            }
            for p in rep.pieces if p.is_below_min
        ]
        small_by_vis: dict[str, int] = {}
        for p in rep.pieces:
            if p.is_small and not p.is_below_min:
                small_by_vis[p.visibility] = small_by_vis.get(p.visibility, 0) + 1
        column_seam_rewards = [
            {"seam_id": s.seam_id, "column_ids": list(s.near_columns)}
            for s in rep.seams if s.near_columns
        ]
        return {
            "candidate_id": self.candidate_id,
            "strategy": self.strategy,
            "description": self.description,
            "anchor_mode": self.anchor_mode,
            "design_score": round(rep.design_score, 3),
            "is_valid": self.is_valid,
            "disqualifications": list(self.disqualifications),
            "hard_violation_count": rep.hard_violation_count,
            "soft_violation_count": rep.soft_violation_count,
            "reward_count": rep.reward_count,
            "rule_status": {r.rule_id: r.status for r in rep.rules},
            "score_breakdown": {
                k: round(v, 3) for k, v in rep.score_breakdown.items()
            },
            "doorway_seam_conflicts": doorway_conflicts,
            "pieces_below_minimum": pieces_below_min,
            "small_pieces_by_visibility": small_by_vis,
            "column_seam_rewards": column_seam_rewards,
            "layout_piece_count": len(rep.pieces),
            "layout_seam_count": len(rep.seams),
        }


@dataclass
class CandidateSelectionResult:
    """Output of ``select_best_layout``."""

    candidates: list[LayoutCandidate]
    selected_candidate_id: str
    selection_reason: str
    valid_candidate_count: int
    v1_limitations: list[str] = field(default_factory=lambda: [
        "global_anchor_only: every candidate applies one anchor mode "
        "uniformly to every zone. A future milestone will enumerate "
        "per-zone anchor combinations so distinct spaces can pick "
        "independent grid starts.",
        "matching_mode_ignored: vein_match / book_match are parsed "
        "but not enforced.",
        "guide_lines_ignored: declared guide lines are surfaced but "
        "the grid does not yet align to them.",
    ])

    @property
    def selected(self) -> LayoutCandidate:
        return next(
            c for c in self.candidates
            if c.candidate_id == self.selected_candidate_id
        )

    @property
    def layout(self) -> LayoutResult:
        return self.selected.layout

    @property
    def report(self) -> RuleReport:
        return self.selected.report

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected_candidate_id": self.selected_candidate_id,
            "selection_reason": self.selection_reason,
            "valid_candidate_count": self.valid_candidate_count,
            "total_candidate_count": len(self.candidates),
            "sliver_handling_note": (
                "Architectural rule R1 (min_piece_size) checks the "
                "FINAL layout — after the absorption pass folds "
                "sub-cuttable slivers into adjacent pieces. The "
                "layout JSON's grid.candidate_evaluations may show "
                "pre-absorption sliver counts: those slivers do NOT "
                "appear in the final cut pieces. Tiny pieces are "
                "only a real R1 risk if they survive into "
                "selected_layout.json's pieces[] array."
            ),
            "v1_limitations": list(self.v1_limitations),
            "candidates": [c.summary_dict() for c in self.candidates],
        }


# ---------------------------------------------------------------------------
# main entry point
# ---------------------------------------------------------------------------


def select_best_layout(
    geometry: TargetGeometry,
    inventory: Iterable | list,
    plan: ArchitecturalPlan,
    *,
    candidate_specs: list[CandidateSpec] | None = None,
    visibility_weights: dict[str, float] | None = None,
    source_inventory_path: str | None = None,
    enable_absorption: bool = True,
) -> CandidateSelectionResult:
    """Generate one layout per ``CandidateSpec``, score each, return
    the best.

    When ``candidate_specs`` is None the selector calls
    ``enumerate_candidate_specs`` to build the default pool: four
    global anchor candidates + two grid-offset candidates + one
    doorway-centred candidate per declared doorway + two
    column-aligned candidates per declared column.

    Hard-rule discipline:
      * Any candidate that triggers R1 (a piece below
        ``plan.min_piece_*_mm``) is marked invalid and excluded from
        the winner pool.
      * Among valid candidates, the highest ``design_score`` wins.
      * If no candidates are valid, the selector picks the least-bad
        one and ``selection_reason`` explains the fallback.

    ``enable_absorption`` is plumbed through to the layout layer so
    targeted tests can force sub-cuttable slivers to survive into
    the candidate set (used to verify the hard-rule gate).
    """
    weights = visibility_weights or DEFAULT_VISIBILITY_WEIGHTS

    if candidate_specs is None:
        # Derive median tile size from the inventory and let the
        # candidates module pick which strategies are relevant for
        # the given plan.
        inv_list = list(inventory)
        summary = compute_inventory_dimension_summary(inv_list)
        candidate_specs = enumerate_candidate_specs(
            geometry, summary.median_width_mm, summary.median_height_mm,
            plan,
        )
        # Re-bind ``inventory`` so the layout pipeline receives the
        # same materialised list (Iterable input could be a generator
        # we just consumed).
        inventory = inv_list

    if not candidate_specs:
        raise ValueError("candidate_specs must contain at least one spec")

    candidates: list[LayoutCandidate] = []
    for spec in candidate_specs:
        layout = generate_tile_layout_from_inventory(
            geometry, inventory,
            source_inventory_path=source_inventory_path,
            # When the spec carries an explicit origin the layout
            # layer ignores anchor_mode and uses the origin verbatim.
            # When origin is None we fall back to whichever named
            # anchor mode the spec declared.
            anchor_mode=spec.anchor_mode or ANCHOR_AUTO,
            origin=spec.origin,
            enable_absorption=enable_absorption,
        )
        # The architectural rule evaluator only needs the layout shape;
        # we pass an empty cut-list dict because rule R1..R8 don't
        # consume cut-list classification today.
        report = evaluate_layout(
            layout.to_dict(), {"pieces": []}, plan,
            visibility_weights=weights,
        )
        disqs = _check_hard_rules(report)
        # When the spec used an explicit origin, the resulting layout
        # records anchor_mode = "explicit_origin" — surface that
        # verbatim so the report shows what actually drove the layout.
        effective_anchor = (
            spec.anchor_mode if spec.origin is None
            else (layout.anchor_mode or "explicit_origin")
        )
        candidates.append(LayoutCandidate(
            candidate_id=spec.candidate_id,
            anchor_mode=effective_anchor or "auto",
            strategy=spec.strategy,
            description=spec.description,
            layout=layout,
            report=report,
            is_valid=not disqs,
            disqualifications=disqs,
        ))

    selected_id, reason, valid_count = _pick_winner(candidates)
    return CandidateSelectionResult(
        candidates=candidates,
        selected_candidate_id=selected_id,
        selection_reason=reason,
        valid_candidate_count=valid_count,
    )


# ---------------------------------------------------------------------------
# winner selection — extracted so tests can drive it with synthetic
# candidates (no real layout generation required)
# ---------------------------------------------------------------------------


def _pick_winner(
    candidates: list[LayoutCandidate],
) -> tuple[str, str, int]:
    """Pick the winner from a candidate list.

    Returns ``(winner_id, reason, valid_count)``. Exposed at module
    level (with leading underscore) so the test suite can verify the
    selection logic against hand-built candidate lists, including
    mixed-validity cases the current layout generator can't naturally
    produce.
    """
    if not candidates:
        raise ValueError("cannot select from an empty candidate list")

    valid = [c for c in candidates if c.is_valid]
    pool = valid if valid else candidates
    pool_sorted = sorted(
        pool,
        key=lambda c: (
            -c.design_score,
            c.hard_violation_count,
            c.candidate_id,
        ),
    )
    winner = pool_sorted[0]
    valid_count = len(valid)

    if valid:
        runner_up = pool_sorted[1] if len(pool_sorted) > 1 else None
        if runner_up is None:
            reason = (
                f"only one valid candidate ({winner.candidate_id}); "
                f"design_score {winner.design_score:.1f}."
            )
        elif runner_up.design_score == winner.design_score:
            reason = (
                f"highest design_score ({winner.design_score:.1f}) "
                f"tied across {sum(1 for c in valid if c.design_score == winner.design_score)} "
                f"valid candidate(s); alphabetical tie-break selected "
                f"{winner.candidate_id}."
            )
        else:
            reason = (
                f"highest design_score ({winner.design_score:.1f}) "
                f"among {valid_count} valid candidate(s); runner-up "
                f"({runner_up.candidate_id}) scored "
                f"{runner_up.design_score:.1f}."
            )
    else:
        reason = (
            f"every candidate failed at least one hard rule "
            f"(R1 min piece size). Fell back to least-bad: "
            f"{winner.candidate_id} with design_score "
            f"{winner.design_score:.1f} and "
            f"{winner.hard_violation_count} hard violation(s)."
        )
    return winner.candidate_id, reason, valid_count


# ---------------------------------------------------------------------------
# hard-rule gate
# ---------------------------------------------------------------------------


# Rule IDs that cause a candidate to be rejected outright. Centralised
# here so a future milestone can extend the gate (e.g. main-entrance
# doorway crossings) without rewriting the selector loop.
HARD_RULE_IDS: tuple[str, ...] = (RULE_MIN_PIECE_SIZE, RULE_FULL_COVERAGE)


def _check_hard_rules(report: RuleReport) -> list[str]:
    """Return the IDs of HARD rules the report violates. Empty list
    means the candidate clears the hard gate."""
    return [
        r.rule_id for r in report.rules
        if r.rule_id in HARD_RULE_IDS and r.status == STATUS_VIOLATION
    ]


# ---------------------------------------------------------------------------
# JSON writers
# ---------------------------------------------------------------------------


def write_candidate_summary_json(
    result: CandidateSelectionResult, path: str | Path,
) -> Path:
    """Write the per-candidate comparison view."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return p


def write_selected_artifacts(
    result: CandidateSelectionResult, output_dir: str | Path,
) -> dict[str, Path]:
    """Write the winner's layout + report into ``output_dir`` alongside
    the candidate summary. Returns a dict mapping artefact name → path
    so the CLI can log them.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    selected = result.selected
    paths["selected_layout"] = write_layout_json(
        selected.layout, out / "selected_layout.json",
    )
    paths["selected_report"] = write_rule_report_json(
        selected.report, out / "selected_report.json",
    )
    paths["candidates_summary"] = write_candidate_summary_json(
        result, out / "candidates_summary.json",
    )
    return paths
