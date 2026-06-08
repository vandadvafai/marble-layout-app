"""Architectural metadata + validation rules.

This layer sits **on top of** the layout / cut_list / cutting outputs
and translates designer-facing architectural intent (spaces,
doorways, columns, visibility, matching mode) into per-rule
pass/violation verdicts and a single design score. It never modifies
the layout itself — its job is to evaluate and report.

Designed to be consumed by **either**:
  * a backend validator behind an interactive designer UI (the
    current product direction): the editor produces a layout, this
    layer validates it against the architectural plan and surfaces
    rule outcomes for designer review,
  * or any future automated layout generator that wants to score
    its candidates against the same rule set.

The previous automatic candidate selector / strategy enumeration
direction (versions 0.1.26–0.1.29) lived in ``selector.py`` and
``candidates.py``. Those modules were removed in 0.1.30; the full
implementation is preserved on the ``checkpoint-before-ui-pivot``
branch. **The validation logic in ``rules.py`` is kept in full** —
it's the part of that work that's reusable for the new direction.

Public API:

    ArchitecturalPlan, Space, Doorway, Column, GuideLine
    VISIBILITY_*, MATCHING_*
    load_architectural_plan(path)
    write_architectural_plan_json(plan, path)

    Seam, detect_seams(pieces)

    RuleReport, RuleResult, PieceEvaluation, SeamEvaluation
    evaluate_layout(layout_dict, cut_list_dict, plan) -> RuleReport
    write_rule_report_json(report, path)
    write_rule_report_summary_json(report, path)
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
from placement_engine.architectural.seams import Seam, detect_seams

__all__ = [
    "ArchitecturalPlan",
    "Column",
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
    "evaluate_layout",
    "load_architectural_plan",
    "write_architectural_plan_json",
    "write_rule_report_json",
    "write_rule_report_summary_json",
]
