"""Designer-facing preview rendering for V1 placements.

This subpackage is a thin, view-only layer over the placement
algorithms. Packers (``shelf_pack``, ``polygon_pack``, ``blf_pack``)
remain untouched; their result types are converted into a normalized
`PlacementView` dataclass, and the renderers below take only that.

Three output modes:

* ``geometric`` — clean CAD-style plan view: thin boundary, slab
  rectangles with thin outlines, red seam lines, optional small corner
  numerals, optional dimensions. No photos.
* ``textured``  — photoreal composite: real cropped slab photos clipped
  to the boundary, hairline grey seams, holes "punched out" white. No
  default labels.
* ``debug``     — engineer-facing matplotlib view with axes, gridlines,
  bbox guide, rejection ghosts, full slab IDs. Opt-in only.

The placement JSON (the `PlacementView` serialized) is the contract;
PNG previews are derived views over it. Anything downstream (PDF,
DXF, Rhino, Blender, Streamlit) reads the JSON, not the renderers.
"""

from placement_engine.preview.comparison import (
    render_geometric_comparison,
    render_textured_comparison,
)
from placement_engine.preview.debug import render_debug
from placement_engine.preview.geometric import render_geometric
from placement_engine.preview.schema import (
    PlacedSlabView,
    PlacementView,
    RejectedSlabView,
    SeamView,
    TargetView,
    view_from_blf_pack_result,
    view_from_polygon_pack_result,
    view_from_shelf_pack_result,
    write_placement_json,
)
from placement_engine.preview.seam_detect import detect_seams
from placement_engine.preview.textured import render_textured

__all__ = [
    "PlacedSlabView",
    "PlacementView",
    "RejectedSlabView",
    "SeamView",
    "TargetView",
    "detect_seams",
    "render_debug",
    "render_geometric",
    "render_geometric_comparison",
    "render_textured",
    "render_textured_comparison",
    "view_from_blf_pack_result",
    "view_from_polygon_pack_result",
    "view_from_shelf_pack_result",
    "write_placement_json",
]
