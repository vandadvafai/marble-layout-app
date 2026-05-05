"""Strategy interface.

Every strategy turns a validated `ProjectInput` plus a project polygon into
an unscored list of candidate `PlacedPiece` objects. Scoring, seam
detection, and risk-flagging happen downstream so strategies stay focused
on layout generation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from shapely.geometry import Polygon

from placement_engine.models import (
    PlacedPiece,
    ProjectInput,
    ReviewMarker,
    StrategyName,
)


@dataclass
class StrategyContext:
    """Read-only inputs every strategy receives."""

    project_input: ProjectInput
    project_polygon: Polygon


@dataclass
class StrategyResult:
    """What a strategy returns: placed pieces plus any review markers it
    chose to emit during generation (e.g. for skipped placements)."""

    pieces: list[PlacedPiece]
    review_markers: list[ReviewMarker] = field(default_factory=list)


class PlacementStrategy(ABC):
    """Implementations should be deterministic given the same context+seed."""

    name: StrategyName

    @abstractmethod
    def generate(self, ctx: StrategyContext) -> StrategyResult:
        """Return placed pieces and any markers raised during generation."""
        raise NotImplementedError
