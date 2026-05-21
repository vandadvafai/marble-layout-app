"""Standardized CAD input intake.

The intake intentionally does **not** parse arbitrary customer DWG
files. Designers prepare a clean DXF with the surface to be clad
placed on the conventional layers below:

    AI_PROJECT_BOUNDARY   exactly one closed polyline (outer boundary)
    AI_HOLES_CUTOUTS      zero or more closed polylines (holes/cutouts)
    AI_IGNORE             ignored on read (helper lines, notes, etc.)

Everything else in the DXF is silently ignored.

Submodules:
    dxf_reader          file open + per-layer entity selection
    geometry_extractor  entity → JSON-style polygon coords
    input_builder       polygon coords → `ProjectInput`
    inspection          structured + Markdown report of what was found
"""

from placement_engine.cad_intake.dxf_reader import CADIntakeError
from placement_engine.cad_intake.input_builder import (
    build_project_input,
    build_project_input_dict,
    project_usable_area,
)

__all__ = [
    "CADIntakeError",
    "build_project_input",
    "build_project_input_dict",
    "project_usable_area",
]
