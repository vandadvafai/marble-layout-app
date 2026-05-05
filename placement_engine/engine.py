"""Top-level orchestrator.

The engine takes a parsed `ProjectInput`, runs each requested strategy,
validates the result, computes metrics, and returns an `EngineOutput`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from shapely.geometry import Polygon

from placement_engine.config import ENGINE_VERSION
from placement_engine.geometry.polygons import coords_to_polygon
from placement_engine.geometry.validation import (
    GeometryValidationError,
    assert_pieces_inside,
    assert_pieces_non_overlapping,
    build_project_polygon,
)
from placement_engine.models import (
    EngineOutput,
    Explanation,
    LayoutOption,
    PlacedPiece,
    ProjectInput,
    ReviewMarker,
    StrategyName,
)
from placement_engine.scoring.risk import (
    annotate_pieces_with_risks,
    build_risk_review_markers,
)
from placement_engine.scoring.waste import compute_basic_metrics
from placement_engine.strategies.base import PlacementStrategy, StrategyContext
from placement_engine.strategies.row_based import BalancedStrategy
from placement_engine.utils.ids import IdSequence


# Strategy registry. New strategies plug in here without touching engine code.
# Until other strategies land, every requested name falls back to balanced.
STRATEGY_REGISTRY: Mapping[StrategyName, type[PlacementStrategy]] = {
    "balanced": BalancedStrategy,
}


def load_input_from_file(path: str | Path) -> ProjectInput:
    raw = json.loads(Path(path).read_text())
    return ProjectInput.model_validate(raw)


def _validate_pieces(project: Polygon, pieces: list[PlacedPiece]) -> None:
    polys = [coords_to_polygon(p.project_polygon) for p in pieces]
    assert_pieces_inside(polys, project)
    assert_pieces_non_overlapping(polys)


def _option_for_strategy(
    name: StrategyName,
    project_input: ProjectInput,
    project_polygon: Polygon,
    option_seq: IdSequence,
) -> LayoutOption | None:
    strategy_cls = STRATEGY_REGISTRY.get(name)
    if strategy_cls is None:
        # MVP: silently skip unknown strategies. The engine still produces a
        # valid output as long as at least one requested strategy runs.
        return None

    strategy = strategy_cls()
    result = strategy.generate(
        StrategyContext(project_input=project_input, project_polygon=project_polygon)
    )
    _validate_pieces(project_polygon, result.pieces)

    # Soft risk evaluation: mutates each piece in place to attach
    # `risk_flags`, then synthesises one `piece_risk` review marker per
    # flagged piece. Hard-drop filtering already happened upstream.
    risk_thresholds = project_input.rules.risk_thresholds
    flagged_count = annotate_pieces_with_risks(result.pieces, risk_thresholds)
    risk_markers = build_risk_review_markers(result.pieces)

    # Merge strategy markers + risk markers and renumber so IDs form one
    # contiguous sequence within this option.
    all_markers: list[ReviewMarker] = list(result.review_markers) + risk_markers
    marker_seq = IdSequence("R")
    for m in all_markers:
        m.review_id = marker_seq.next()

    metrics = compute_basic_metrics(
        project_polygon, result.pieces, project_input.slabs
    )
    metrics.small_piece_count = sum(
        1 for p in result.pieces
        if any(f.type == "small_piece" for f in p.risk_flags)
    )

    tradeoffs = [
        "Seam detection and visual scoring are not yet implemented.",
        "All requested strategies currently fall back to the balanced row-based layout.",
    ]
    if result.review_markers:
        tradeoffs.append(
            f"{len(result.review_markers)} placement(s) were skipped because "
            "the slab did not intersect the usable project area at that "
            "cursor position; see review_markers."
        )
    if flagged_count:
        tradeoffs.append(
            f"{flagged_count} piece(s) carry risk flags (small, narrow, "
            "short, high aspect ratio, or irregular). The pieces remain in "
            "the layout for the designer to review or accept."
        )

    return LayoutOption(
        option_id=option_seq.next(),
        option_name=f"{name.replace('_', ' ').title()} layout",
        strategy=name,
        recommended=(name == project_input.design_requirements.priority),
        score=0.0,
        metrics=metrics,
        placed_pieces=result.pieces,
        seams=[],
        review_markers=all_markers,
        explanation=Explanation(
            summary=(
                f"Row-based MVP layout. Generated {len(result.pieces)} pieces "
                f"from {metrics.slabs_used} slab(s) with "
                f"{metrics.waste_percentage}% waste."
            ),
            tradeoffs=tradeoffs,
        ),
    )


def run(project_input: ProjectInput) -> EngineOutput:
    """Run the engine end-to-end and return an `EngineOutput`."""
    project_polygon = build_project_polygon(project_input.layout)

    requested = project_input.options_requested or ["balanced"]
    option_seq = IdSequence("OPT_")
    options: list[LayoutOption] = []
    for name in requested:
        option = _option_for_strategy(
            name=name,
            project_input=project_input,
            project_polygon=project_polygon,
            option_seq=option_seq,
        )
        if option is not None:
            options.append(option)

    if not options:
        raise GeometryValidationError(
            "no layout options were generated; check options_requested values"
        )

    return EngineOutput(
        project_id=project_input.project_id,
        engine_version=ENGINE_VERSION,
        units=project_input.units,
        layout_options=options,
    )
