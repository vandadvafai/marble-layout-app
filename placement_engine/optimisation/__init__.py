"""V1 assignment optimisation — better-than-greedy slab-to-piece matching.

Solves the V1 assignment problem globally via min-cost bipartite
matching (scipy's Hungarian ``linear_sum_assignment`` over a
rectangular cost matrix) instead of the greedy walk in
``placement_engine.assignment``.

Same V1 constraints:

  * one slab supplies one piece
  * a slab fits a piece iff ``slab.width ≥ piece_w AND slab.height ≥ piece_h``
    (no rotation)

What changes is **which** slab is paired with which piece. The cost
function (see ``scoring.py``) is constructed so the matching
maximises high-priority assigned pieces first, then total assigned
floor area, then minimises waste — all in a single linear objective.

V1 ships one strategy: ``min_waste_global``. The strategy flag exists
so later schemes (visual continuity, designer-weighted, …) can plug
in without touching the API.

This package does NOT introduce rotation, multi-piece-per-slab
cutting, or offcut reuse. The greedy assignment layer remains
available so designers can A/B compare results.
"""

from placement_engine.optimisation.assignment_optimizer import (
    OPTIMISATION_STRATEGY_MIN_WASTE_GLOBAL,
    SUPPORTED_STRATEGIES,
    optimise_assignment,
)
from placement_engine.optimisation.schema import (
    OptimisationResult,
    write_optimised_assignment_json,
    write_optimised_summary_json,
)

__all__ = [
    "OPTIMISATION_STRATEGY_MIN_WASTE_GLOBAL",
    "OptimisationResult",
    "SUPPORTED_STRATEGIES",
    "optimise_assignment",
    "write_optimised_assignment_json",
    "write_optimised_summary_json",
]
