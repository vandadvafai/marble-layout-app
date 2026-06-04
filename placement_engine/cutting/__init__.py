"""Slab cutting / offcut-aware planning (V1).

This layer sits one level below ``assignment`` / ``optimisation``.
Where those packages answer *"which slab supplies which piece"* under
a 1:1 rule, this layer answers *"how do we actually cut the chosen
slabs"* — and therefore lets a single slab supply many pieces, while
tracking the remaining usable rectangles (offcuts) and the estimated
fabrication waste.

V1 scope (intentionally simple, additive):

* No rotation — pieces are packed at their bounding-box orientation.
* Axis-aligned guillotine-style splits — each placed piece partitions
  its host rectangle into a "right strip" and a "top strip"; both
  strips are kept as offcuts for future pieces to consider.
* Priority order matches the rest of the engine: full → edge → hole
  → sliver; within a class, largest area first.
* For each piece, choose the candidate rectangle that yields the
  smallest *leftover area* (least-waste fit). Ties are broken by
  smaller rectangle area to keep big rectangles available for big
  pieces.
* Pieces that don't fit any remaining rectangle are recorded as
  unassigned with a reason — never silently dropped.

Out of scope (later milestones):
  * rotation, kerf widths, true 2-D bin packing, offcut reuse across
    slabs, designer-driven seam minimisation.

Public API:

    CuttingPlan, CuttingSlab, CutPlacement, Offcut, CuttingSummary
    build_cutting_plan(cut_list, inventory) -> CuttingPlan
    write_cutting_plan_json(plan, path)
    write_cutting_plan_summary_json(plan, path)
    render_cutting_plan_preview(plan, out_path)
"""

from placement_engine.cutting.planner import build_cutting_plan
from placement_engine.cutting.renderer import render_cutting_plan_preview
from placement_engine.cutting.schema import (
    CUTTING_ASSIGNED,
    CUTTING_UNASSIGNED,
    CUTTING_UNASSIGNED_ALL_FITTING_USED,
    CUTTING_UNASSIGNED_NO_SLAB_FITS,
    CutPlacement,
    CuttingPlan,
    CuttingSlab,
    CuttingSummary,
    Offcut,
    UnassignedPiece,
    write_cutting_plan_json,
    write_cutting_plan_summary_json,
)

__all__ = [
    "CUTTING_ASSIGNED",
    "CUTTING_UNASSIGNED",
    "CUTTING_UNASSIGNED_ALL_FITTING_USED",
    "CUTTING_UNASSIGNED_NO_SLAB_FITS",
    "CutPlacement",
    "CuttingPlan",
    "CuttingSlab",
    "CuttingSummary",
    "Offcut",
    "UnassignedPiece",
    "build_cutting_plan",
    "render_cutting_plan_preview",
    "write_cutting_plan_json",
    "write_cutting_plan_summary_json",
]
