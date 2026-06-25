"""Conversion helpers between engine objects and HTTP-friendly JSON.

The frontend consumes a flatter shape than the engine's full layout
JSON (which carries debug metadata the editor doesn't need). These
helpers narrow the payload to exactly what the canvas needs:

  * ``target`` — bounding box + boundary polygon + interior holes
  * ``pieces`` — id + cut polygon + a few flags the renderer styles
  * ``plan``  — doorways, columns, spaces (for overlay rendering)

If a future milestone needs the full layout dict (e.g. for an
"export raw" debug view) the engine's ``LayoutResult.to_dict()``
output is still available — these helpers are an additive surface,
not a replacement.
"""

from __future__ import annotations

from typing import Any

from placement_engine.architectural.rules import RuleReport
from placement_engine.architectural.schema import ArchitecturalPlan
from placement_engine.layout import LayoutResult


def serialize_layout_for_editor(layout: LayoutResult) -> dict[str, Any]:
    """Narrow the layout to what the editor canvas needs.

    ``target`` carries the boundary polygon + bbox + holes so the
    canvas can frame the view. ``pieces`` is a flat list — no zone
    tree, no candidate evaluations — keyed by ``piece_id`` so future
    edits can address pieces individually.
    """
    target = layout.target
    pieces = [
        {
            "piece_id": p.piece_id,
            "zone_id": p.zone_id,
            "nominal_x_mm": p.nominal_x_mm,
            "nominal_y_mm": p.nominal_y_mm,
            "nominal_width_mm": p.nominal_width_mm,
            "nominal_height_mm": p.nominal_height_mm,
            "polygon": [list(pt) for pt in (p.actual_cut_polygon or [])],
            "is_full_tile": p.is_full_tile,
            "is_edge_piece": p.is_edge_piece,
            "intersects_hole": p.intersects_hole,
            "notes": list(p.notes or []),
        }
        for p in layout.pieces
    ]
    return {
        "target": {
            "target_id": target.target_id,
            "name": target.name,
            "bbox": list(target.bbox),
            "boundary": [list(pt) for pt in target.boundary],
            "holes": [
                [list(pt) for pt in hole]
                for hole in (target.holes or [])
            ],
        },
        "grid": {
            "tile_width_mm": layout.tile_width_mm,
            "tile_height_mm": layout.tile_height_mm,
            "origin": list(layout.origin) if layout.origin is not None else None,
            "anchor_mode": layout.anchor_mode,
        },
        "pieces": pieces,
        "piece_count": len(pieces),
    }


def serialize_plan_for_editor(plan: ArchitecturalPlan) -> dict[str, Any]:
    """Narrow the architectural plan to what the canvas overlays need.

    Designer-facing fields only. Internal rule thresholds
    (``min_piece_*_mm``, ``min_coverage_ratio``) are NOT exposed —
    those drive validation, not rendering, so they live behind the
    validation endpoint when that ships.
    """
    return {
        "target_id": plan.target_id,
        "spaces": [
            {
                "space_id": s.space_id,
                "name": s.name,
                "polygon": [list(pt) for pt in s.polygon],
                "visibility": s.visibility,
            }
            for s in plan.spaces
        ],
        "doorways": [
            {
                "doorway_id": d.doorway_id,
                "segment": [list(d.segment[0]), list(d.segment[1])],
                "width_mm": d.width_mm,
                "is_main_entrance": d.is_main_entrance,
            }
            for d in plan.doorways
        ],
        "columns": [
            {
                "column_id": c.column_id,
                "polygon": [list(pt) for pt in c.polygon],
            }
            for c in plan.columns
        ],
        "guide_lines": [
            {
                "guide_line_id": g.guide_line_id,
                "segment": [list(g.segment[0]), list(g.segment[1])],
                "priority": g.priority,
                "name": g.name,
            }
            for g in plan.guide_lines
        ],
    }


# ---------------------------------------------------------------------------
# rule report → editor JSON
# ---------------------------------------------------------------------------


def serialize_rule_report_for_editor(report: RuleReport) -> dict[str, Any]:
    """Flatten the architectural rule report into what the editor's
    validation panel renders.

    Drops the embedded ``architectural_plan`` (the frontend already
    has it from the demo fetch) and the source paths (irrelevant in
    a live-edit loop). Keeps the per-rule status + affected IDs so
    the canvas can highlight problem pieces and seams.

    Adds an ``is_valid`` boolean — true iff there are zero hard
    violations (R1 + R9 in 0.1.30). This is the single field the
    UI's "valid / INVALID" badge reads.
    """
    return {
        "target_id": report.target_id,
        "is_valid": report.hard_violation_count == 0,
        "design_score": round(report.design_score, 3),
        "hard_violation_count": report.hard_violation_count,
        "soft_violation_count": report.soft_violation_count,
        "reward_count": report.reward_count,
        "score_breakdown": {
            k: round(v, 3) for k, v in report.score_breakdown.items()
        },
        "rules": [
            {
                "rule_id": r.rule_id,
                "status": r.status,
                "count": r.count,
                "message": r.message,
                "affected_ids": list(r.affected_ids),
                "score_delta": round(r.score_delta, 3),
            }
            for r in report.rules
        ],
        # Per-piece classification — the canvas reads is_below_min /
        # is_absorbed_holder to colour pieces red / amber.
        "pieces": [
            {
                "piece_id": p.piece_id,
                "zone_id": p.zone_id,
                "space_id": p.space_id,
                "visibility": p.visibility,
                "bbox_width_mm": round(p.bbox_width_mm, 3),
                "bbox_height_mm": round(p.bbox_height_mm, 3),
                "is_below_min": p.is_below_min,
                "is_small": p.is_small,
                "is_absorbed_holder": p.is_absorbed_holder,
                "crosses_doorway": p.crosses_doorway,
            }
            for p in report.pieces
        ],
        # Per-seam classification — the canvas reads crosses_doorways
        # to flag R2 violations on the live overlay.
        "seams": [
            {
                "seam_id": s.seam_id,
                "piece_a_id": s.piece_a_id,
                "piece_b_id": s.piece_b_id,
                "length_mm": round(s.length_mm, 3),
                "crosses_doorways": list(s.crosses_doorways),
                "crosses_main_entrance": s.crosses_main_entrance,
                "near_columns": list(s.near_columns),
            }
            for s in report.seams
        ],
    }
