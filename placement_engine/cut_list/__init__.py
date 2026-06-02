"""Cut-list layer — layout.json → fabrication-style cut list.

This is a **read-only** consumer of the layout layer. A `CutList`
formalises every layout piece into a manufacturing requirement,
classifying it as one of four mutually-exclusive primary types
(``full`` / ``edge`` / ``hole`` / ``sliver``). Booleans for the
underlying flags (`is_full_piece`, `is_edge_piece`, `intersects_hole`,
`requires_internal_cut`) are still exposed for downstream queries.

The cut list explicitly does NOT:

* assign slabs to pieces (later milestone)
* run any placement packer (shelf / polygon / BLF — all untouched)
* attempt waste optimisation or offcut reuse
* drop or merge slivers — slivers stay visible

Public API:

    CutList                  full cut list for one layout
    CutListPiece             one fabrication-required piece
    CutListSummary           aggregate counts + total area
    build_cut_list           layout.json (path or dict) → CutList
    write_cut_list_json      → cut_list.json
    write_summary_json       → cut_list_summary.json
    render_cut_list_preview  → cut_list_preview.png
"""

from placement_engine.cut_list.builder import build_cut_list
from placement_engine.cut_list.renderer import render_cut_list_preview
from placement_engine.cut_list.schema import (
    CLASSIFICATION_EDGE,
    CLASSIFICATION_FULL,
    CLASSIFICATION_HOLE,
    CLASSIFICATION_SLIVER,
    CutList,
    CutListPiece,
    CutListSummary,
    write_cut_list_json,
    write_summary_json,
)

__all__ = [
    "CLASSIFICATION_EDGE",
    "CLASSIFICATION_FULL",
    "CLASSIFICATION_HOLE",
    "CLASSIFICATION_SLIVER",
    "CutList",
    "CutListPiece",
    "CutListSummary",
    "build_cut_list",
    "render_cut_list_preview",
    "write_cut_list_json",
    "write_summary_json",
]
