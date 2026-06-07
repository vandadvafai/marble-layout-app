"""Architectural rules + scoring (V1).

This layer sits **on top of** the layout / cut_list / cutting outputs
and translates designer-facing architectural intent (spaces, doorways,
columns, visibility, matching mode) into per-rule pass/violation
verdicts and a single design score. It never modifies the layout
itself — its job is to evaluate and report.

Why a separate layer:
  * Layout/cutting are purely geometric. They know nothing about
    "this is the main entrance" or "this column should host a seam".
  * Architectural metadata is supplied by a small JSON file (one per
    project) which a future preprocessing layer or DXF annotation
    pass will produce automatically; today it's authored by hand.
  * Rule evaluation can be re-run cheaply against any candidate
    layout, so the engine can compare scoring across alternatives
    without rerunning the geometric pipeline.

Public API:

    ArchitecturalPlan, Space, Doorway, Column
    VISIBILITY_*, MATCHING_*
    load_architectural_plan(path)
    write_architectural_plan_json(plan, path)

    Seam, detect_seams(pieces)

    RuleReport, RuleResult, PieceEvaluation, SeamEvaluation
    evaluate_layout(layout_dict, cut_list_dict, plan) -> RuleReport
    write_rule_report_json(report, path)
"""

from placement_engine.architectural.loader import (
    load_architectural_plan,
)
from placement_engine.architectural.rules import (
    PieceEvaluation,
    RuleReport,
    RuleResult,
    SeamEvaluation,
    evaluate_layout,
    write_rule_report_json,
    write_rule_report_summary_json,
)
from placement_engine.architectural.schema import (
    DEFAULT_MIN_PIECE_HEIGHT_MM,
    DEFAULT_MIN_PIECE_WIDTH_MM,
    DEFAULT_SMALL_PIECE_THRESHOLD_MM,
    MATCHING_BOOK_MATCH,
    MATCHING_NONE,
    MATCHING_VEIN_MATCH,
    SUPPORTED_MATCHING_MODES,
    SUPPORTED_VISIBILITY_LEVELS,
    VISIBILITY_HIGH,
    VISIBILITY_LOW,
    VISIBILITY_MEDIUM,
    VISIBILITY_VERY_HIGH,
    VISIBILITY_VERY_LOW,
    ArchitecturalPlan,
    Column,
    Doorway,
    GuideLine,
    Space,
    write_architectural_plan_json,
)
from placement_engine.architectural.candidates import (
    STRATEGY_ANCHOR,
    STRATEGY_COLUMN_ALIGNED,
    STRATEGY_DOORWAY_CENTRED,
    STRATEGY_GRID_OFFSET,
    SUPPORTED_STRATEGIES,
    CandidateSpec,
    enumerate_candidate_specs,
)
from placement_engine.architectural.seams import Seam, detect_seams
from placement_engine.architectural.selector import (
    DEFAULT_CANDIDATE_ANCHOR_MODES,
    CandidateSelectionResult,
    LayoutCandidate,
    select_best_layout,
    write_candidate_summary_json,
    write_selected_artifacts,
)

__all__ = [
    "ArchitecturalPlan",
    "CandidateSelectionResult",
    "CandidateSpec",
    "Column",
    "DEFAULT_CANDIDATE_ANCHOR_MODES",
    "LayoutCandidate",
    "STRATEGY_ANCHOR",
    "STRATEGY_COLUMN_ALIGNED",
    "STRATEGY_DOORWAY_CENTRED",
    "STRATEGY_GRID_OFFSET",
    "SUPPORTED_STRATEGIES",
    "DEFAULT_MIN_PIECE_HEIGHT_MM",
    "DEFAULT_MIN_PIECE_WIDTH_MM",
    "DEFAULT_SMALL_PIECE_THRESHOLD_MM",
    "Doorway",
    "GuideLine",
    "MATCHING_BOOK_MATCH",
    "MATCHING_NONE",
    "MATCHING_VEIN_MATCH",
    "PieceEvaluation",
    "RuleReport",
    "RuleResult",
    "SUPPORTED_MATCHING_MODES",
    "SUPPORTED_VISIBILITY_LEVELS",
    "Seam",
    "SeamEvaluation",
    "Space",
    "VISIBILITY_HIGH",
    "VISIBILITY_LOW",
    "VISIBILITY_MEDIUM",
    "VISIBILITY_VERY_HIGH",
    "VISIBILITY_VERY_LOW",
    "detect_seams",
    "enumerate_candidate_specs",
    "evaluate_layout",
    "load_architectural_plan",
    "select_best_layout",
    "write_architectural_plan_json",
    "write_candidate_summary_json",
    "write_rule_report_json",
    "write_rule_report_summary_json",
    "write_selected_artifacts",
]
