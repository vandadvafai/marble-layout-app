"""Helpers shared by the Markdown and PDF report exporters.

Both reports describe the same layout option to the same designer, so
the action texts, severity-to-action mappings, and the small geometry
formatters live here once. The writers (`markdown_report.write_report`
and `pdf_report.write_pdf_report`) each consume structured layout data
and emit their own format — neither parses the other.
"""

from __future__ import annotations

from shapely.geometry import Polygon

from placement_engine.models import PlacedPiece, ReviewMarker, RiskFlag, Seam


# ---------------------------------------------------------------------------
# Suggested-action lookups
# ---------------------------------------------------------------------------

MARKER_ACTIONS: dict[str, str] = {
    "incomplete_coverage": (
        "Add more slabs to inventory or accept the partial coverage if "
        "the uncovered area is acceptable for this project."
    ),
    "insufficient_inventory": (
        "Increase slab inventory or reduce project scope. Re-run the "
        "engine with the updated input."
    ),
    "empty_slab_placement_skipped": (
        "No action required — the engine retried at the next cursor "
        "position. The marker is informational."
    ),
    "piece_risk": (
        "Review the flagged piece in Rhino/AutoCAD. Decide whether to "
        "keep, replace, merge, or re-cut."
    ),
}

RISK_ACTIONS: dict[str, str] = {
    "small_piece": "Confirm the piece is large enough to fabricate cleanly.",
    "narrow_piece": "Confirm the strip is wide enough to handle and install safely.",
    "short_piece": "Confirm the strip is tall enough to handle and install safely.",
    "thin_aspect_ratio": (
        "Confirm the piece can be cut without snapping along its long "
        "axis; consider rotating or re-cutting."
    ),
    "irregular_piece": (
        "Confirm the non-rectangular cut is feasible at the workshop. "
        "Consider simplifying the geometry."
    ),
}


def suggested_marker_action(marker: ReviewMarker) -> str:
    return MARKER_ACTIONS.get(marker.type, "Designer review required.")


def suggested_risk_action(flag: RiskFlag) -> str:
    return RISK_ACTIONS.get(flag.type, "Designer review required.")


# ---------------------------------------------------------------------------
# Geometry / formatting helpers
# ---------------------------------------------------------------------------


def piece_bbox(piece: PlacedPiece) -> tuple[float, float, float, float]:
    """(xmin, ymin, xmax, ymax) of the placed piece in project space."""
    return Polygon(piece.project_polygon).bounds


def piece_centroid(piece: PlacedPiece) -> tuple[float, float]:
    c = Polygon(piece.project_polygon).centroid
    return float(c.x), float(c.y)


def fmt_int_mm(v: float) -> str:
    """Whole-millimetre formatting used in both reports."""
    return f"{v:.0f}"


def seam_endpoints(seam: Seam) -> tuple[tuple[float, float], tuple[float, float]]:
    return tuple(seam.line[0]), tuple(seam.line[-1])


# ---------------------------------------------------------------------------
# Notes / limitations block (same wording in both reports)
# ---------------------------------------------------------------------------

NOTES_AND_LIMITATIONS: tuple[str, ...] = (
    "This is an AI-generated first-draft layout. A designer must review "
    "and approve the geometry before it is shared with a customer or "
    "sent to production.",
    "Visual matching and vein-direction scoring are not yet "
    "implemented; the strategy ranks layouts by geometry, not aesthetics.",
    "Production / factory cut counting and complexity scoring are MVP "
    "placeholders. Treat cut_count_estimate, cutting_complexity_score, "
    "and estimated_production_difficulty as advisory only until the "
    "production team defines the formula.",
    "The DXF is intended as an editable review draft for Rhino/AutoCAD, "
    "not a final factory cutting file.",
    "DWG export is not provided directly; use Rhino/AutoCAD to save-as "
    "DWG if a customer needs that format.",
    "The current slab inventory is synthetic test data. Real Avandad "
    "slab database integration is future work.",
)
