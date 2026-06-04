"""Geometric tile-layout generator (V1).

Generates a clean grid of equal nominal rectangles across the usable
floor polygon (boundary − holes), clipping rectangles that cross the
boundary or holes into **edge pieces**. The output is the geometric
layout designers will later use to *decide which slabs supply which
pieces*. Slab inventory is NOT consulted at this stage — see
``placement_engine.inventory`` and ``placement_engine.preview`` for
the later slab-assignment + rendering layers.

Public API:

    Piece                  one tile or edge piece (dataclass)
    LayoutResult           full result for a single tile_w × tile_h run
    generate_tile_layout() build a LayoutResult from a TargetGeometry
    write_layout_json()    serialize a LayoutResult to JSON
    render_layout_geometric()  clean CAD-style PNG preview
"""

from placement_engine.layout.anchoring import (
    ANCHOR_AUTO,
    ANCHOR_BOTTOM_LEFT,
    ANCHOR_BOTTOM_RIGHT,
    ANCHOR_TOP_LEFT,
    ANCHOR_TOP_RIGHT,
    DEFAULT_CANDIDATE_MODES,
    DEFAULT_MIN_SLIVER_HEIGHT_MM,
    DEFAULT_MIN_SLIVER_WIDTH_MM,
    SUPPORTED_ANCHOR_MODES,
    SliverEvaluation,
    SliverPolicy,
    compute_anchor_origin,
    evaluate_layout,
    score_evaluation,
)
from placement_engine.layout.grid import (
    generate_tile_layout,
    generate_tile_layout_from_inventory,
)
from placement_engine.layout.inventory_stats import (
    InventoryDimensionSummary,
    compute_inventory_dimension_summary,
)
from placement_engine.layout.renderer import render_layout_geometric
from placement_engine.layout.schema import (
    LAYOUT_BASIS_EXPLICIT,
    LAYOUT_BASIS_INVENTORY_MEDIAN,
    LayoutResult,
    Piece,
    write_layout_json,
)

__all__ = [
    "ANCHOR_AUTO",
    "ANCHOR_BOTTOM_LEFT",
    "ANCHOR_BOTTOM_RIGHT",
    "ANCHOR_TOP_LEFT",
    "ANCHOR_TOP_RIGHT",
    "DEFAULT_CANDIDATE_MODES",
    "DEFAULT_MIN_SLIVER_HEIGHT_MM",
    "DEFAULT_MIN_SLIVER_WIDTH_MM",
    "LAYOUT_BASIS_EXPLICIT",
    "LAYOUT_BASIS_INVENTORY_MEDIAN",
    "InventoryDimensionSummary",
    "LayoutResult",
    "Piece",
    "SUPPORTED_ANCHOR_MODES",
    "SliverEvaluation",
    "SliverPolicy",
    "compute_anchor_origin",
    "compute_inventory_dimension_summary",
    "evaluate_layout",
    "generate_tile_layout",
    "generate_tile_layout_from_inventory",
    "render_layout_geometric",
    "score_evaluation",
    "write_layout_json",
]
