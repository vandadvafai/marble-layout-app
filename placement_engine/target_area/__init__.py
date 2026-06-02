"""V1 client target-area model.

A `TargetArea` is the rectangular floor/wall area the slabs are being
placed onto. V1 is intentionally minimal — a single axis-aligned
rectangle in millimetres — so the placement engine has a concrete,
user-supplied target without yet depending on the standardized-DXF
intake. The DXF path (`placement_engine.cad_intake`) is still the
future home for richer client geometry; this module is the simple
hand-off ramp.

Public API:

    TargetArea                  dataclass + validation
    target_area_warnings(t)     non-blocking consistency checks

Edit this module when you need extra fields (multi-region targets,
holes/cut-outs, rotation, ...). For V1, do **not** add business
metadata here.
"""

from placement_engine.target_area.dxf_target import (
    TargetGeometry,
    load_target_geometry_from_dxf,
)
from placement_engine.target_area.model import (
    TargetArea,
    target_area_warnings,
)

__all__ = [
    "TargetArea",
    "TargetGeometry",
    "load_target_geometry_from_dxf",
    "target_area_warnings",
]
