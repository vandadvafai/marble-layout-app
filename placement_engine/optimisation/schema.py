"""Light wrapper around `Assignment` to record the strategy that produced it.

The greedy assignment package already owns the canonical schema. The
optimisation layer just attaches a strategy label so designers can
A/B compare runs across strategies.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from placement_engine.assignment.schema import Assignment


@dataclass
class OptimisationResult:
    """An `Assignment` produced by the optimiser plus its strategy label."""

    assignment: Assignment
    strategy: str

    def to_dict(self) -> dict[str, Any]:
        """Serialise wrapping the assignment dict with the strategy embedded.

        ``strategy`` is added at the top level for quick filtering
        across multiple runs and also inside ``summary`` for the
        one-page view.
        """
        d = self.assignment.to_dict()
        d["strategy"] = self.strategy
        if "summary" in d:
            d["summary"] = {"strategy": self.strategy, **d["summary"]}
        return d

    def summary_dict(self) -> dict[str, Any]:
        """Just the summary, with strategy attached."""
        s = self.assignment.summary.to_dict()
        return {"strategy": self.strategy, **s}


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


def write_optimised_assignment_json(
    result: OptimisationResult, path: str | Path,
) -> Path:
    """Serialise the full optimised assignment to JSON."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return p


def write_optimised_summary_json(
    result: OptimisationResult, path: str | Path,
) -> Path:
    """Serialise only the summary (the fabrication one-pager)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(result.summary_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return p
