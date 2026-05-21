"""DXF export for the CAD/Rhino/AutoCAD designer hand-off.

The DXF intentionally carries only **clean editable geometry** plus
short labels. Detailed warnings, risk explanations, and review notes
live in the Markdown report — keeping them out of the drawing keeps
the file usable in Rhino/AutoCAD without the designer having to clean
up red circles before working with it.

Layers:

  PROJECT_BOUNDARY        — closed polyline of the project outline
  HOLES_CUTOUTS           — closed polylines of every hole
  SLAB_PIECES             — closed polylines of `piece_role == "main"` pieces
  OFFCUT_PIECES           — closed polylines of `piece_role == "offcut"` pieces
  SEAMS                   — line entities for every detected seam
  PIECE_LABELS            — `piece_id` (and `slab_id`) text near each centroid
  REVIEW_REFERENCE_POINTS — small POINT entities at piece-level review marker
                            locations (subtle; not a warning circle)

Output is deterministic for a given (input, layout option). Text height
is scaled to the project bounding box so labels are readable at the
project's real-world scale rather than tiny or overwhelming.
"""

from __future__ import annotations

from pathlib import Path

import ezdxf
from shapely.geometry import Polygon

from placement_engine.models import (
    LayoutOption,
    PlacedPiece,
    ProjectInput,
)


# ezdxf uses ACI (AutoCAD Color Index) values. The choices below are
# meant to be readable in both light and dark Rhino/AutoCAD themes.
_LAYER_DEFS = {
    "PROJECT_BOUNDARY":         {"color": 7, "lineweight": 50},   # white/black, thick
    "HOLES_CUTOUTS":            {"color": 1, "lineweight": 35},   # red
    "SLAB_PIECES":              {"color": 5, "lineweight": 25},   # blue
    "OFFCUT_PIECES":            {"color": 4, "lineweight": 25},   # cyan
    "SEAMS":                    {"color": 8, "lineweight": 13},   # grey, thin
    "PIECE_LABELS":             {"color": 7, "lineweight": 13},   # default
    "REVIEW_REFERENCE_POINTS":  {"color": 8, "lineweight": 13},   # grey
}


def _ensure_layers(doc: "ezdxf.document.Drawing") -> None:
    for name, attrs in _LAYER_DEFS.items():
        if name not in doc.layers:
            doc.layers.add(name=name, **attrs)


def _label_text_height(project_input: ProjectInput) -> float:
    """Pick a text height that's readable at the project's real-world scale.

    The project boundary is in mm. A small countertop (~1 m²) wants
    roughly 30 mm text; a long corridor (~30 m) wants something like
    200 mm. Linear interpolation against the bbox diagonal works fine
    for the project sizes we ship today.
    """
    xs = [pt[0] for pt in project_input.layout.boundary]
    ys = [pt[1] for pt in project_input.layout.boundary]
    diag = ((max(xs) - min(xs)) ** 2 + (max(ys) - min(ys)) ** 2) ** 0.5
    # 1/100 of the diagonal, clamped to a sensible band.
    return max(30.0, min(diag / 100.0, 250.0))


def _piece_centroid(piece: PlacedPiece) -> tuple[float, float]:
    c = Polygon(piece.project_polygon).centroid
    return float(c.x), float(c.y)


def write_dxf(
    project_input: ProjectInput,
    layout_option: LayoutOption,
    target: str | Path,
) -> Path:
    """Write a clean DXF for one layout option and return the path."""
    target_path = Path(target)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    # AC1027 == AutoCAD R2013, broadly compatible with Rhino and any
    # AutoCAD version from 2013 onwards.
    doc = ezdxf.new(dxfversion="R2013", setup=True)
    doc.units = ezdxf.units.MM
    _ensure_layers(doc)
    msp = doc.modelspace()

    # 1. Project boundary
    msp.add_lwpolyline(
        list(project_input.layout.boundary),
        close=True,
        dxfattribs={"layer": "PROJECT_BOUNDARY"},
    )

    # 2. Holes / cutouts
    for hole in project_input.layout.holes:
        msp.add_lwpolyline(
            list(hole),
            close=True,
            dxfattribs={"layer": "HOLES_CUTOUTS"},
        )

    # 3 & 4. Pieces — split by role.
    text_h = _label_text_height(project_input)
    for piece in layout_option.placed_pieces:
        layer = "OFFCUT_PIECES" if piece.piece_role == "offcut" else "SLAB_PIECES"
        msp.add_lwpolyline(
            list(piece.project_polygon),
            close=True,
            dxfattribs={"layer": layer},
        )

        # Label: piece_id on the first line, slab_id (+ "offcut" tag) below.
        cx, cy = _piece_centroid(piece)
        primary = piece.piece_id
        if piece.piece_role == "offcut":
            secondary = f"{piece.slab_id} offcut"
        else:
            secondary = piece.slab_id

        text_top = msp.add_text(
            primary,
            height=text_h,
            dxfattribs={"layer": "PIECE_LABELS"},
        )
        text_top.set_placement((cx, cy + text_h * 0.6), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)

        text_bot = msp.add_text(
            secondary,
            height=text_h * 0.7,
            dxfattribs={"layer": "PIECE_LABELS"},
        )
        text_bot.set_placement((cx, cy - text_h * 0.6), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)

    # 5. Seams — drawn as multi-vertex polylines so curved or multi-segment
    # seams are preserved exactly (Shapely sometimes returns >2 points per
    # LineString from boundary intersections).
    for seam in layout_option.seams:
        if len(seam.line) < 2:
            continue
        if len(seam.line) == 2:
            msp.add_line(
                tuple(seam.line[0]),
                tuple(seam.line[1]),
                dxfattribs={"layer": "SEAMS"},
            )
        else:
            msp.add_lwpolyline(
                list(seam.line),
                close=False,
                dxfattribs={"layer": "SEAMS"},
            )

    # 6. Optional: subtle reference POINT for piece-level review markers.
    # Skips layout-level markers (location is None) — those are explained
    # in the Markdown report instead.
    for marker in layout_option.review_markers:
        if marker.location is None:
            continue
        msp.add_point(
            tuple(marker.location),
            dxfattribs={"layer": "REVIEW_REFERENCE_POINTS"},
        )

    doc.saveas(target_path)
    return target_path
