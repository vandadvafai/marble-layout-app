"""Cost function for the V1 assignment optimisation.

The optimiser minimises total cost over a bipartite matching. Cost is
constructed so that lexicographic priorities collapse into a single
linear objective via large power-of-ten multipliers:

    cost(piece, slab) =
        - PRIORITY_WEIGHT[piece.classification]   ← dominant: pick high-priority pieces first
        - AREA_REWARD_WEIGHT * piece.area_m2      ← within a class, prefer bigger pieces
        + WASTE_PENALTY_WEIGHT * waste_m2(slab, piece)   ← tie-break: lowest waste wins
        (INFEASIBLE_COST when slab can't cover piece — the solver only
         picks infeasible pairs as a last resort, and we filter them
         out post-hoc.)

Weights are chosen so each priority level dominates everything below
it. With piece areas up to ~5 m² and waste up to ~5 m², the largest
single term contributing to one level is bounded by ~AREA × 5 ≈ 5e3
of the AREA_REWARD scale. The next-higher PRIORITY level is 1e6,
1e9, 1e12 — three orders of magnitude apart — comfortably above any
within-class differences across the ~30-piece, ~7-slab test datasets.

Negative weights for class-priority + area-reward mean assigning a
piece *reduces* total cost (good). Waste is a positive penalty.
"""

from __future__ import annotations

# Lexicographic class priority. Full pieces are the most valuable to
# get onto their best slab — they're the standard fabrication panels.
# Slivers come last; losing one is the least painful.
PRIORITY_WEIGHT: dict[str, float] = {
    "full":   1e12,
    "edge":   1e9,
    "hole":   1e9,   # internal-cut pieces sit alongside edges
    "sliver": 1e6,
}

# Within a class, reward bigger pieces (covers more floor). Scaled so
# 1 m² of piece > any waste delta but < any priority delta.
AREA_REWARD_WEIGHT: float = 1e3

# Final tie-break: lowest waste wins. 1.0 m² waste ≡ 1.0 of cost.
WASTE_PENALTY_WEIGHT: float = 1.0

# Slab-doesn't-fit-piece cost. Big enough that the solver never picks
# an infeasible pair if any feasible alternative exists.
INFEASIBLE_COST: float = 1e15

# Threshold used post-hoc: anything ≥ this is treated as an infeasible
# match (e.g. the matrix forced a pair we shouldn't honour) and
# filtered into "unassigned" with reason ``no_slab_fits``.
INFEASIBLE_THRESHOLD: float = INFEASIBLE_COST * 0.5


def piece_slab_cost(
    classification: str,
    piece_area_m2: float,
    slab_area_m2: float,
    fits: bool,
) -> float:
    """Cost of pairing a piece with a slab. Lower is better.

    ``fits`` is the pre-computed width × height feasibility. When
    False, the cost is the INFEASIBLE sentinel regardless of areas.
    """
    if not fits:
        return INFEASIBLE_COST
    priority = PRIORITY_WEIGHT.get(classification, 0.0)
    waste = max(slab_area_m2 - piece_area_m2, 0.0)
    return (
        -priority
        - AREA_REWARD_WEIGHT * piece_area_m2
        + WASTE_PENALTY_WEIGHT * waste
    )
