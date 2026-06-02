"""Cut-list dataclasses + JSON I/O.

Schema deliberately kept narrow and serialisation-stable: a single
list of `CutListPiece` records plus a top-level summary. Designers and
fabrication consume it; nothing in this module knows about slabs or
inventory.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

# Primary classification labels — these are the values that drive the
# preview palette and the summary counts. Exposed as constants so
# callers can switch on them without importing Literal types.
CLASSIFICATION_FULL: str = "full"
CLASSIFICATION_EDGE: str = "edge"
CLASSIFICATION_HOLE: str = "hole"
CLASSIFICATION_SLIVER: str = "sliver"

Classification = Literal["full", "edge", "hole", "sliver"]


@dataclass
class CutListPiece:
    """One fabrication-required piece.

    ``piece_id`` is the cut-list's sequential ID (``P001``, ``P002``…)
    so the cut list reads naturally as a manufacturing document.
    ``source_layout_piece_id`` carries the layout's ``tile_rX_cY``
    identifier so any downstream consumer can round-trip back to the
    layout if needed.
    """

    piece_id: str
    source_layout_piece_id: str
    # Nominal slab size the design batch is supplying (median tile, in
    # practice). Useful for sanity-checking the cut list against the
    # inventory dimension summary.
    nominal_width_mm: float
    nominal_height_mm: float
    # Bbox of the actual cut shape. For a full piece this equals the
    # nominal size; for clipped pieces it shrinks accordingly.
    bounding_width_mm: float
    bounding_height_mm: float
    area_m2: float
    # Primary classification — drives the preview colour and the
    # summary counts. Mutually exclusive; see CutList docstring for the
    # priority order.
    classification: Classification
    # Underlying booleans. These are NOT mutually exclusive (e.g. an
    # edge piece can also intersect a hole at its perimeter) and are
    # kept around for downstream filtering.
    is_full_piece: bool
    is_edge_piece: bool
    intersects_hole: bool
    requires_internal_cut: bool
    # Geometry. ``cut_polygon_exterior`` is closed (last vertex = first).
    # ``cut_polygon_interiors`` is non-empty iff ``requires_internal_cut``.
    cut_polygon_exterior: list[tuple[float, float]]
    cut_polygon_interiors: list[list[tuple[float, float]]] = field(
        default_factory=list
    )
    # Free-form notes carried verbatim from the layout piece (e.g.
    # ``sliver``, ``split_by_hole``). Useful diagnostic context.
    notes: list[str] = field(default_factory=list)


@dataclass
class CutListSummary:
    """Aggregate cut-list counts — the manufacturing one-pager."""

    total_pieces: int
    full_pieces: int
    edge_pieces: int
    hole_pieces: int
    pieces_with_internal_cuts: int
    sliver_pieces: int
    total_area_m2: float

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["total_area_m2"] = round(self.total_area_m2, 4)
        return d


@dataclass
class CutList:
    """Top-level cut list — everything fabrication needs.

    Classification priority (when multiple booleans apply to a single
    piece, the topmost matching label wins):

      1. ``sliver``   — flagged in layout notes; wins so slivers are
                        always visible
      2. ``hole``     — has interior rings → ``requires_internal_cut``
      3. ``edge``     — clipped by the outer boundary, no internal cut
      4. ``full``     — clean rectangle, no clipping, no holes
    """

    source_layout_path: str
    target_id: str
    target_name: str
    tile_width_mm: float
    tile_height_mm: float
    pieces: list[CutListPiece]

    @property
    def summary(self) -> CutListSummary:
        return CutListSummary(
            total_pieces=len(self.pieces),
            full_pieces=sum(
                1 for p in self.pieces if p.classification == CLASSIFICATION_FULL
            ),
            edge_pieces=sum(
                1 for p in self.pieces if p.classification == CLASSIFICATION_EDGE
            ),
            hole_pieces=sum(
                1 for p in self.pieces if p.classification == CLASSIFICATION_HOLE
            ),
            pieces_with_internal_cuts=sum(
                1 for p in self.pieces if p.requires_internal_cut
            ),
            sliver_pieces=sum(
                1 for p in self.pieces if p.classification == CLASSIFICATION_SLIVER
            ),
            total_area_m2=sum(p.area_m2 for p in self.pieces),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_layout_path": self.source_layout_path,
            "target": {"target_id": self.target_id, "name": self.target_name},
            "grid": {
                "tile_width_mm": self.tile_width_mm,
                "tile_height_mm": self.tile_height_mm,
            },
            "pieces": [_piece_to_dict(p) for p in self.pieces],
            "summary": self.summary.to_dict(),
        }


def _piece_to_dict(p: CutListPiece) -> dict[str, Any]:
    """Stable JSON shape — tuples → lists, no asdict() surprises."""
    return {
        "piece_id": p.piece_id,
        "source_layout_piece_id": p.source_layout_piece_id,
        "nominal_width_mm": p.nominal_width_mm,
        "nominal_height_mm": p.nominal_height_mm,
        "bounding_width_mm": p.bounding_width_mm,
        "bounding_height_mm": p.bounding_height_mm,
        "area_m2": round(p.area_m2, 6),
        "classification": p.classification,
        "is_full_piece": p.is_full_piece,
        "is_edge_piece": p.is_edge_piece,
        "intersects_hole": p.intersects_hole,
        "requires_internal_cut": p.requires_internal_cut,
        "cut_polygon_exterior": [list(pt) for pt in p.cut_polygon_exterior],
        "cut_polygon_interiors": [
            [list(pt) for pt in ring] for ring in p.cut_polygon_interiors
        ],
        "notes": list(p.notes),
    }


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


def write_cut_list_json(cut_list: CutList, path: str | Path) -> Path:
    """Serialize the full cut list to JSON. Returns the written path."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(cut_list.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return p


def write_summary_json(cut_list: CutList, path: str | Path) -> Path:
    """Serialize only the summary to JSON — the one-pager for fabrication."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(cut_list.summary.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return p
