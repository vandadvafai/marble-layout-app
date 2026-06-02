"""Build a `CutList` from a layout (path or in-memory dict).

This is the only entry point that knows the layout-JSON shape. Anyone
downstream consumes the resulting `CutList` directly. The builder
performs no clipping, no inventory matching, no packer logic — it just
walks the layout pieces, classifies each one, renumbers them as
``P001…Pnnn``, and packages them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from placement_engine.cut_list.schema import (
    CLASSIFICATION_EDGE,
    CLASSIFICATION_FULL,
    CLASSIFICATION_HOLE,
    CLASSIFICATION_SLIVER,
    Classification,
    CutList,
    CutListPiece,
)


def build_cut_list(
    layout: str | Path | dict[str, Any],
) -> CutList:
    """Build a `CutList` from a layout JSON path or already-loaded dict.

    The returned cut list mirrors the layout's pieces 1:1 (no merging,
    no drops). Slivers stay visible; the builder only renames them.
    """
    layout_dict, source_path = _load_layout(layout)
    layout_pieces = layout_dict.get("pieces", [])
    target = layout_dict.get("target", {})
    grid = layout_dict.get("grid", {})

    cut_pieces: list[CutListPiece] = []
    for i, lp in enumerate(layout_pieces, start=1):
        cut_pieces.append(_layout_piece_to_cut_piece(lp, sequence_number=i))

    return CutList(
        source_layout_path=str(source_path) if source_path else "",
        target_id=str(target.get("target_id", "")),
        target_name=str(target.get("name", "")),
        tile_width_mm=float(grid.get("tile_width_mm", 0.0)),
        tile_height_mm=float(grid.get("tile_height_mm", 0.0)),
        pieces=cut_pieces,
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _load_layout(
    layout: str | Path | dict[str, Any],
) -> tuple[dict[str, Any], Path | None]:
    """Resolve the input to ``(layout_dict, source_path_or_None)``."""
    if isinstance(layout, dict):
        return layout, None
    path = Path(layout)
    if not path.exists():
        raise FileNotFoundError(f"layout JSON not found: {path}")
    return json.loads(path.read_text(encoding="utf-8")), path


def _layout_piece_to_cut_piece(
    lp: dict[str, Any], *, sequence_number: int,
) -> CutListPiece:
    """Convert one layout-JSON piece to a `CutListPiece`."""
    interiors_raw = lp.get("interior_holes", []) or []
    interiors = [
        [(float(pt[0]), float(pt[1])) for pt in ring]
        for ring in interiors_raw
    ]
    exterior = [
        (float(pt[0]), float(pt[1])) for pt in lp.get("actual_cut_polygon", [])
    ]
    notes = list(lp.get("notes", []))

    is_full_piece = bool(lp.get("is_full_tile", False))
    is_edge_piece = bool(lp.get("is_edge_piece", False))
    intersects_hole = bool(lp.get("intersects_hole", False))
    requires_internal_cut = bool(interiors)  # ground truth: do we have inner rings?
    classification = _classify(
        notes=notes,
        is_full_piece=is_full_piece,
        is_edge_piece=is_edge_piece,
        requires_internal_cut=requires_internal_cut,
    )

    return CutListPiece(
        piece_id=f"P{sequence_number:03d}",
        source_layout_piece_id=str(lp.get("piece_id", "")),
        nominal_width_mm=float(lp.get("nominal_width_mm", 0.0)),
        nominal_height_mm=float(lp.get("nominal_height_mm", 0.0)),
        bounding_width_mm=float(lp.get("bounding_width_mm", 0.0)),
        bounding_height_mm=float(lp.get("bounding_height_mm", 0.0)),
        area_m2=float(lp.get("actual_area_m2", 0.0)),
        classification=classification,
        is_full_piece=is_full_piece,
        is_edge_piece=is_edge_piece,
        intersects_hole=intersects_hole,
        requires_internal_cut=requires_internal_cut,
        cut_polygon_exterior=exterior,
        cut_polygon_interiors=interiors,
        notes=notes,
    )


def _classify(
    *,
    notes: list[str],
    is_full_piece: bool,
    is_edge_piece: bool,
    requires_internal_cut: bool,
) -> Classification:
    """Map the layout flags + notes onto the four primary categories.

    Priority (top wins):
        1. ``sliver`` — slivers stay visible even when they're also
           edge or hole pieces
        2. ``hole``   — has an interior cut (the piece literally has a
           hole inside it that must be cut)
        3. ``edge``   — clipped by the outer boundary, no interior cut
        4. ``full``   — clean rectangle, no clipping, no hole
    """
    if "sliver" in notes:
        return CLASSIFICATION_SLIVER
    if requires_internal_cut:
        return CLASSIFICATION_HOLE
    if is_edge_piece:
        return CLASSIFICATION_EDGE
    if is_full_piece:
        return CLASSIFICATION_FULL
    # Defensive fallback: shouldn't reach here on a well-formed layout
    # (the layout invariant is is_full_piece XOR is_edge_piece).
    return CLASSIFICATION_EDGE
