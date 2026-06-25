"""DXF export for the Step-4 "Export factory DXF" button.

Why a separate module from ``placement_engine.exporters.dxf_exporter``
---------------------------------------------------------------------
The legacy exporter is keyed to the cutting-package schema
(``ProjectInput`` / ``LayoutOption`` / ``PlacedPiece``). The editor
API speaks a different language — finalized pieces, an assignments
table, a matcher response — and shoehorning the editor state into
ProjectInput just to call ``write_dxf`` adds friction with no
upside. So this module is a small, focused builder that takes the
editor's data straight in.

It still uses ``ezdxf`` so the output passes the same downstream
sanity checks as the legacy exporter, and the layer convention
(``FLOOR_BOUNDARY`` / ``CUT_PIECES`` / ``PIECE_LABELS`` /
``SLAB_LABELS`` / ``DOORWAYS`` / ``SEAMS``) matches what the
milestone brief asked for.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Iterable, Sequence

import ezdxf
import ezdxf.enums


# Layer definitions — names spec'd by the milestone brief. Colors
# use AutoCAD Color Index values picked to match how the canvas
# renders today (black boundary, blue cut pieces, etc.). Customers
# can re-style in their CAD app; the layer NAMES are the contract.
_LAYER_DEFS: dict[str, dict] = {
    "FLOOR_BOUNDARY":  {"color": 7},   # white/black (depends on viewer bg)
    "CUT_PIECES":      {"color": 5},   # blue
    "PIECE_LABELS":    {"color": 2},   # yellow
    "SLAB_LABELS":     {"color": 4},   # cyan
    "DOORWAYS":        {"color": 30},  # orange
    "SEAMS":           {"color": 8},   # grey
}


@dataclass
class DxfPieceInput:
    """One piece in the assignment ready for DXF emission.

    Slab metadata is optional — pieces without an assignment never
    reach this code path (the route 400s before then), but the
    fields are nullable for defensive use in tests."""

    piece_id: str
    # Closed polygon: list of (x, y) tuples in mm. May be a simple
    # rectangle or a more complex clip; the DXF writer just emits
    # an LWPOLYLINE so any closed ring works.
    polygon: Sequence[tuple[float, float]]
    nominal_width_mm: float
    nominal_height_mm: float
    slab_id: str | None
    slab_width_mm: float | None
    slab_height_mm: float | None
    cut_width_mm: float | None
    cut_height_mm: float | None
    rotation_needed: bool
    waste_fraction: float | None


def _polygon_centroid(
    polygon: Sequence[tuple[float, float]],
) -> tuple[float, float]:
    """Centroid of a closed polygon ring (without depending on
    Shapely). Uses the standard area-weighted formula; falls back to
    a simple average for degenerate (zero-area) rings."""
    if not polygon:
        return (0.0, 0.0)
    pts = list(polygon)
    if pts[0] != pts[-1]:
        pts = pts + [pts[0]]
    a = 0.0
    cx = 0.0
    cy = 0.0
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        cross = x0 * y1 - x1 * y0
        a += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    a *= 0.5
    if abs(a) < 1e-9:
        xs = [p[0] for p in pts[:-1]]
        ys = [p[1] for p in pts[:-1]]
        return (sum(xs) / len(xs), sum(ys) / len(ys))
    return (cx / (6.0 * a), cy / (6.0 * a))


def _label_text_height(
    boundary: Sequence[tuple[float, float]],
) -> float:
    """Scale text to the floor size so labels stay readable from
    a tiny mockup to a 30m corridor."""
    if not boundary:
        return 80.0
    xs = [pt[0] for pt in boundary]
    ys = [pt[1] for pt in boundary]
    diag = ((max(xs) - min(xs)) ** 2 + (max(ys) - min(ys)) ** 2) ** 0.5
    return max(40.0, min(diag / 80.0, 250.0))


def build_dxf_bytes(
    demo_id: str,
    boundary: Sequence[tuple[float, float]],
    holes: Iterable[Sequence[tuple[float, float]]],
    pieces: Iterable[DxfPieceInput],
    seams: Iterable[Sequence[tuple[float, float]]] = (),
    doorways: Iterable[Sequence[tuple[float, float]]] = (),
) -> bytes:
    """Produce a complete DXF as raw bytes.

    Layers:
      * ``FLOOR_BOUNDARY`` — the target outline + any holes.
      * ``CUT_PIECES``     — one closed polyline per finalised piece.
      * ``PIECE_LABELS``   — piece_id text at each centroid.
      * ``SLAB_LABELS``    — slab metadata text under each piece
                              centroid (slab_id, original slab size,
                              final cut size, rotation, waste).
      * ``DOORWAYS``       — line segments for each doorway.
      * ``SEAMS``          — line segments for each seam.

    Units are mm. R2013 is the chosen ``dxfversion`` for broad CAD
    compatibility (Rhino, AutoCAD 2013+, LibreCAD).
    """
    doc = ezdxf.new(dxfversion="R2013", setup=True)
    doc.units = ezdxf.units.MM
    for name, attrs in _LAYER_DEFS.items():
        if name not in doc.layers:
            doc.layers.add(name=name, **attrs)
    msp = doc.modelspace()

    # 1. Floor boundary + any holes.
    if boundary:
        msp.add_lwpolyline(
            [(float(x), float(y)) for x, y in boundary],
            close=True,
            dxfattribs={"layer": "FLOOR_BOUNDARY"},
        )
    for hole in holes:
        pts = [(float(x), float(y)) for x, y in hole]
        if len(pts) >= 3:
            msp.add_lwpolyline(
                pts, close=True,
                dxfattribs={"layer": "FLOOR_BOUNDARY"},
            )

    text_h = _label_text_height(boundary)

    # 2. Each cut piece — polygon, piece label, slab label.
    for p in pieces:
        ring = [(float(x), float(y)) for x, y in p.polygon]
        if len(ring) < 3:
            continue
        msp.add_lwpolyline(
            ring, close=True,
            dxfattribs={"layer": "CUT_PIECES"},
        )
        cx, cy = _polygon_centroid(ring)

        piece_text = msp.add_text(
            p.piece_id,
            height=text_h,
            dxfattribs={"layer": "PIECE_LABELS"},
        )
        piece_text.set_placement(
            (cx, cy + text_h * 0.7),
            align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER,
        )

        if p.slab_id is not None:
            # Multi-line slab label. ezdxf MTEXT would be nicer but
            # plain TEXT entities are more universally compatible
            # with viewers. We emit two stacked lines: id on top,
            # geometry summary below.
            slab_dim = (
                f"{p.slab_width_mm:.0f}×{p.slab_height_mm:.0f} mm"
                if p.slab_width_mm and p.slab_height_mm else "?"
            )
            cut_dim = (
                f"{p.cut_width_mm:.0f}×{p.cut_height_mm:.0f} mm"
                if p.cut_width_mm and p.cut_height_mm
                else f"{p.nominal_width_mm:.0f}×{p.nominal_height_mm:.0f} mm"
            )
            rot = " ↻90°" if p.rotation_needed else ""
            waste = (
                f" · {round(p.waste_fraction * 100):d}% waste"
                if p.waste_fraction is not None else ""
            )
            id_text = msp.add_text(
                p.slab_id,
                height=text_h * 0.6,
                dxfattribs={"layer": "SLAB_LABELS"},
            )
            id_text.set_placement(
                (cx, cy - text_h * 0.4),
                align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER,
            )
            meta_text = msp.add_text(
                f"slab {slab_dim} → cut {cut_dim}{rot}{waste}",
                height=text_h * 0.45,
                dxfattribs={"layer": "SLAB_LABELS"},
            )
            meta_text.set_placement(
                (cx, cy - text_h * 1.1),
                align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER,
            )

    # 3. Doorway segments (two-point lines).
    for d in doorways:
        pts = list(d)
        if len(pts) >= 2:
            msp.add_line(
                (float(pts[0][0]), float(pts[0][1])),
                (float(pts[1][0]), float(pts[1][1])),
                dxfattribs={"layer": "DOORWAYS"},
            )

    # 4. Seams (also two-point lines today).
    for s in seams:
        pts = list(s)
        if len(pts) >= 2:
            msp.add_line(
                (float(pts[0][0]), float(pts[0][1])),
                (float(pts[1][0]), float(pts[1][1])),
                dxfattribs={"layer": "SEAMS"},
            )

    # ezdxf only writes to text streams — round-trip through io to
    # get the bytes the HTTP layer needs.
    sink = io.StringIO()
    doc.write(sink)
    payload = sink.getvalue().encode("utf-8")
    return payload
