"""Convert DXF polyline entities into JSON-style polygon coordinates.

Supports the two polyline types Rhino and AutoCAD emit by default:

  * `LWPOLYLINE` — modern lightweight polyline; closed flag = `entity.closed`
  * `POLYLINE`   — older 2D/3D polyline; closed flag = `entity.is_closed`

All other entity types (LINE, ARC, SPLINE, HATCH, BLOCK, TEXT, …) are
intentionally **not** supported in MVP. The intake module raises a
clear error when it sees them on a required layer; the designer is
expected to convert curves to closed polylines in Rhino/AutoCAD
before exporting.
"""

from __future__ import annotations

from typing import Iterable

from ezdxf.entities import DXFEntity, LWPolyline, Polyline

from placement_engine.cad_intake.dxf_reader import CADIntakeError
from placement_engine.models import PolygonCoords


# Entity types we know are unsupported but worth naming in error messages.
_UNSUPPORTED_HINTS = {
    "LINE":     "Join individual lines into a single closed polyline.",
    "ARC":      "Convert arcs to polyline segments (Rhino: _Convert / AutoCAD: PEDIT).",
    "CIRCLE":   "Convert the circle to a closed polyline approximation.",
    "ELLIPSE":  "Convert the ellipse to a closed polyline approximation.",
    "SPLINE":   "Flatten the spline to a closed polyline.",
    "HATCH":    "Hatches are decorative — re-draw the boundary as a closed polyline.",
    "INSERT":   "Explode the block reference so its geometry lives on the layer directly.",
    "TEXT":     "Move text to a different layer; the intake only reads geometry.",
    "MTEXT":    "Move text to a different layer; the intake only reads geometry.",
    "DIMENSION": "Move dimensions to a different layer; the intake only reads geometry.",
}


def _is_closed(entity: LWPolyline | Polyline) -> bool:
    """Return True if the polyline forms a closed ring."""
    if isinstance(entity, LWPolyline):
        return bool(entity.closed)
    if isinstance(entity, Polyline):
        return bool(entity.is_closed)
    return False


def _lwpolyline_to_coords(entity: LWPolyline) -> PolygonCoords:
    """Extract (x, y) vertices from an LWPOLYLINE."""
    pts = [(float(x), float(y)) for x, y, *_ in entity.get_points("xy")]
    return _strip_trailing_duplicate(pts)


def _polyline_to_coords(entity: Polyline) -> PolygonCoords:
    """Extract (x, y) vertices from a (heavy) POLYLINE."""
    pts: PolygonCoords = []
    for vertex in entity.vertices:
        loc = vertex.dxf.location
        pts.append((float(loc[0]), float(loc[1])))
    return _strip_trailing_duplicate(pts)


def _strip_trailing_duplicate(coords: PolygonCoords) -> PolygonCoords:
    """Some exporters repeat the first vertex at the end; the engine
    schema doesn't (the ring is implicitly closed). Drop the repeat."""
    if len(coords) >= 2 and coords[0] == coords[-1]:
        return coords[:-1]
    return coords


def extract_closed_polylines(
    entities: Iterable[DXFEntity], layer_name: str
) -> list[PolygonCoords]:
    """Return one `PolygonCoords` per closed polyline on the layer.

    Raises `CADIntakeError` on the first unsupported entity type — the
    designer has to clean the layer before any conversion can run.
    Non-closed polylines also raise (they almost always indicate
    forgotten "close" command in Rhino/AutoCAD).
    """
    polygons: list[PolygonCoords] = []
    for entity in entities:
        kind = entity.dxftype()
        if isinstance(entity, (LWPolyline, Polyline)):
            if not _is_closed(entity):
                raise CADIntakeError(
                    f"{kind} on layer {layer_name!r} is not closed. "
                    f"Close the polyline in Rhino/AutoCAD before exporting."
                )
            if isinstance(entity, LWPolyline):
                coords = _lwpolyline_to_coords(entity)
            else:
                coords = _polyline_to_coords(entity)
            if len(coords) < 3:
                raise CADIntakeError(
                    f"{kind} on layer {layer_name!r} has fewer than 3 "
                    f"distinct vertices. Re-draw it as a real polygon."
                )
            polygons.append(coords)
        else:
            hint = _UNSUPPORTED_HINTS.get(
                kind,
                f"{kind} is not supported by the intake. "
                f"Convert it to a closed polyline.",
            )
            raise CADIntakeError(
                f"Unsupported entity {kind!r} found on layer {layer_name!r}. "
                f"{hint}"
            )
    return polygons
