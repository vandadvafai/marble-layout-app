"""V1 slab-to-cut-piece assignment.

Consumes a cut list (``cut_list.json``) and a slab inventory
(``clean_slabs.json``) and produces a 1:1 mapping from cut pieces to
slabs:

    cut piece (required dimensions) → slab (1590 × 2200, etc.)

V1 rules — minimal, no optimisation:

* assignment priority: ``full → edge → hole → sliver`` (within each,
  largest piece first to grab the best-fitting slab while it's still
  available)
* a slab can supply a piece iff ``slab.width ≥ piece.bounding_width``
  AND ``slab.height ≥ piece.bounding_height`` (no rotation)
* among valid candidates, pick the **smallest** by area to minimise
  waste
* every slab can supply at most one piece in V1
* pieces that don't find a fit are kept with a clear reason
  (``no_slab_fits`` / ``all_fitting_slabs_used``)
* unused slabs are tracked alongside the assignments

This layer explicitly does NOT:

* run any placement packer (shelf / polygon / BLF unchanged)
* perform offcut reuse, rotation, or seam optimisation
* render slab photos onto the floor preview

Public API:

    Assignment             container with summary, JSON I/O
    AssignmentRecord       one piece-to-slab record
    AssignmentSummary      aggregate counts + total waste
    build_assignment       cut_list + inventory → Assignment
    write_assignment_json  → assignment.json
    write_summary_json     → assignment_summary.json
    render_assignment_preview  → assignment_preview.png
"""

from placement_engine.assignment.builder import build_assignment
from placement_engine.assignment.renderer import render_assignment_preview
from placement_engine.assignment.schema import (
    ASSIGNMENT_ASSIGNED,
    ASSIGNMENT_UNASSIGNED,
    UNASSIGNED_ALL_FITTING_USED,
    UNASSIGNED_NO_SLAB_FITS,
    Assignment,
    AssignmentRecord,
    AssignmentSummary,
    write_assignment_json,
    write_summary_json,
)

__all__ = [
    "ASSIGNMENT_ASSIGNED",
    "ASSIGNMENT_UNASSIGNED",
    "Assignment",
    "AssignmentRecord",
    "AssignmentSummary",
    "UNASSIGNED_ALL_FITTING_USED",
    "UNASSIGNED_NO_SLAB_FITS",
    "build_assignment",
    "render_assignment_preview",
    "write_assignment_json",
    "write_summary_json",
]
