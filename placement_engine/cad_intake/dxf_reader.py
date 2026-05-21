"""DXF file open + per-layer entity selection.

Wraps `ezdxf.readfile` so the rest of the intake never imports ezdxf
directly. The reader's job is structural: open the file, look up
entities on the requested layer, raise a clean `CADIntakeError` with a
designer-actionable message when anything is wrong.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import ezdxf
from ezdxf.document import Drawing
from ezdxf.entities import DXFEntity


# Layer names the intake recognises. New layers can be added here.
LAYER_PROJECT_BOUNDARY = "AI_PROJECT_BOUNDARY"
LAYER_HOLES_CUTOUTS = "AI_HOLES_CUTOUTS"
LAYER_IGNORE = "AI_IGNORE"


class CADIntakeError(ValueError):
    """Raised when the standardized DXF can't be read or doesn't meet
    the intake contract. Messages are written for the designer."""


def read_dxf(path: str | Path) -> Drawing:
    """Open a standardized DXF and return the ezdxf Drawing."""
    p = Path(path)
    if not p.exists():
        raise CADIntakeError(f"CAD file does not exist: {p}")
    if not p.is_file():
        raise CADIntakeError(f"CAD path is not a file: {p}")
    try:
        return ezdxf.readfile(p)
    except ezdxf.DXFStructureError as exc:
        raise CADIntakeError(
            f"DXF file {p.name!r} is malformed. Re-export it cleanly from "
            f"Rhino/AutoCAD and try again. (Underlying error: {exc})"
        ) from exc


def entities_on_layer(doc: Drawing, layer_name: str) -> list[DXFEntity]:
    """All modelspace entities whose `layer` attribute matches `layer_name`."""
    msp = doc.modelspace()
    return [e for e in msp if e.dxf.layer == layer_name]


def layer_summary(doc: Drawing) -> dict[str, dict[str, int]]:
    """Return `{layer: {entity_type: count, ...}, ...}` for every modelspace layer.

    Used by the inspection report; not part of the conversion path.
    """
    summary: dict[str, dict[str, int]] = {}
    for entity in doc.modelspace():
        layer = entity.dxf.layer
        kind = entity.dxftype()
        summary.setdefault(layer, {}).setdefault(kind, 0)
        summary[layer][kind] += 1
    return summary


def known_layers() -> Sequence[str]:
    return (LAYER_PROJECT_BOUNDARY, LAYER_HOLES_CUTOUTS, LAYER_IGNORE)
