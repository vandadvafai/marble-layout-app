"""HTTP route handlers.

The foundation milestone exposes two read-only endpoints:

  * ``GET /api/demo-layouts`` — list available demo IDs the
    frontend's demo picker can offer.
  * ``GET /api/demo-layouts/{demo_id}`` — return the layout + plan
    for one demo, generated on demand from the existing engine.

On-demand generation keeps the source of truth in Python (no
pre-baked JSON files to drift). The engine call is fast enough
(< 1 s for any V1 demo) that caching isn't worth the complexity
yet — add an in-memory cache here if profiling later disagrees.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from placement_engine.api.inventory_matching import (
    candidate_to_dict,
    load_inventory_slabs,
    match_piece,
)
from placement_engine.api.inventory_source import (
    resolve_inventory_source,
    source_label_description,
)
from placement_engine.api.dxf_export import (
    DxfPieceInput, build_dxf_bytes,
)
from placement_engine.api.inventory_upload import (
    clear_active_upload, get_active_upload, process_upload,
)
from placement_engine.api.serializers import (
    serialize_layout_for_editor,
    serialize_plan_for_editor,
    serialize_rule_report_for_editor,
)
from placement_engine.architectural import (
    ArchitecturalPlan,
    Column,
    Doorway,
    GuideLine,
    Space,
    evaluate_layout,
    load_architectural_plan,
)
from placement_engine.inventory import load_inventory
from placement_engine.layout import generate_tile_layout_from_inventory
from placement_engine.target_area import load_target_geometry_from_dxf

log = logging.getLogger(__name__)

# Resolve project root once so demo paths don't depend on where the
# server is launched from. ``__file__`` is …/placement_engine/api/routes.py
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Inventory is now resolved per-request via
# ``placement_engine.api.inventory_source.resolve_inventory_source``
# — see that module for the search order (env override → real
# project export → demo fixture). The legacy ``DEMO_INVENTORY_PATH``
# constant below is kept ONLY for the demo-layout generator below,
# which still loads slabs to seed the initial layout. New callers
# (the matcher and the inventory-info endpoint) MUST go through the
# resolver so the source label reaches the UI.
DEMO_INVENTORY_PATH = PROJECT_ROOT / "outputs/slab_ingestion_test/clean_slabs.json"

# Demo registry. Keyed by the URL slug the frontend uses. ``label``
# is the human-readable name the picker shows.
DEMOS: dict[str, dict[str, str | Path]] = {
    "l_shape": {
        "label": "L-shape floor",
        "dxf": PROJECT_ROOT / "examples/cad_inputs/demo/demo_l_shape_floor.dxf",
        "plan": PROJECT_ROOT / "examples/architectural/demo_l_shape_floor.json",
    },
    "apartment": {
        "label": "Irregular apartment floor",
        "dxf": PROJECT_ROOT / "examples/cad_inputs/demo/demo_irregular_apartment_floor.dxf",
        "plan": PROJECT_ROOT / "examples/architectural/demo_irregular_apartment_floor.json",
    },
    "rectangle": {
        "label": "Rectangle floor",
        "dxf": PROJECT_ROOT / "examples/cad_inputs/demo/demo_rectangle_floor.dxf",
        "plan": PROJECT_ROOT / "examples/architectural/demo_rectangle_floor.json",
    },
}


router = APIRouter()


@router.get("/api/demo-layouts")
def list_demos() -> dict:
    """Demo index — used by the frontend's picker so it doesn't
    hard-code the available demos."""
    return {
        "demos": [
            {"demo_id": demo_id, "label": meta["label"]}
            for demo_id, meta in DEMOS.items()
        ],
    }


@router.get("/api/demo-layouts/{demo_id}")
def get_demo_layout(demo_id: str) -> dict:
    """Generate one demo's layout + plan and return both in the
    flattened editor shape.

    The layout is generated using the existing
    ``generate_tile_layout_from_inventory`` with default settings —
    same code path the engine has shipped since 0.1.24. The future
    interactive editor will start from this seed and let the
    designer mutate it.
    """
    meta = DEMOS.get(demo_id)
    if meta is None:
        raise HTTPException(
            status_code=404,
            detail=f"unknown demo_id {demo_id!r}; "
                   f"available: {sorted(DEMOS.keys())}",
        )

    dxf_path = Path(meta["dxf"])
    plan_path = Path(meta["plan"])

    if not dxf_path.exists():
        raise HTTPException(
            status_code=500,
            detail=f"demo DXF missing on disk: {dxf_path}",
        )
    if not DEMO_INVENTORY_PATH.exists():
        raise HTTPException(
            status_code=500,
            detail=f"demo inventory missing: {DEMO_INVENTORY_PATH}",
        )

    geometry = load_target_geometry_from_dxf(dxf_path)
    inventory = load_inventory(DEMO_INVENTORY_PATH)
    plan = (
        load_architectural_plan(plan_path) if plan_path.exists()
        # Plans are optional — return an empty placeholder if the
        # demo only has a DXF. Validation overlays will then be empty.
        else None
    )
    layout = generate_tile_layout_from_inventory(
        geometry, inventory.slabs,
        source_inventory_path=str(DEMO_INVENTORY_PATH),
    )

    log.info(
        "demo %s loaded: %d pieces, plan=%s",
        demo_id, len(layout.pieces), "yes" if plan else "no",
    )

    response: dict = {
        "demo_id": demo_id,
        "label": meta["label"],
        "layout": serialize_layout_for_editor(layout),
    }
    if plan is not None:
        response["plan"] = serialize_plan_for_editor(plan)
    return response


# ---------------------------------------------------------------------------
# POST /api/demo-layouts/{demo_id}/regenerate — re-grid with custom tile
# ---------------------------------------------------------------------------


class RegenerateLayoutRequest(BaseModel):
    """Custom-tile regeneration body.

    When ``tile_width_mm`` / ``tile_height_mm`` are provided, the
    layout grid is built using exactly those dimensions (typically
    the median of the active uploaded inventory). When both are
    omitted, the regeneration falls back to the inventory-median
    behaviour ``GET /api/demo-layouts/{id}`` already uses.
    """
    tile_width_mm: float | None = None
    tile_height_mm: float | None = None


@router.post("/api/demo-layouts/{demo_id}/regenerate")
def regenerate_demo_layout(
    demo_id: str, body: RegenerateLayoutRequest,
) -> dict:
    """Re-tile the demo's geometry using a caller-supplied tile size.

    Used by the Step-3 "Generate layout from inventory size" button:
    the frontend posts ``{tile_width_mm, tile_height_mm}`` derived
    from the uploaded inventory's median dimensions and gets back a
    fresh layout to seed Step 2 with. The pristine ``GET`` endpoint
    is unchanged — this is an EXPLICIT regeneration that the
    designer triggers.

    Falls back to inventory-median sizing when both dimensions are
    omitted, so the endpoint is safe to call without a body.
    """
    from placement_engine.layout import generate_tile_layout

    meta = DEMOS.get(demo_id)
    if meta is None:
        raise HTTPException(
            status_code=404,
            detail=f"unknown demo_id {demo_id!r}; "
                   f"available: {sorted(DEMOS.keys())}",
        )

    dxf_path = Path(meta["dxf"])
    plan_path = Path(meta["plan"])
    if not dxf_path.exists():
        raise HTTPException(
            status_code=500,
            detail=f"demo DXF missing on disk: {dxf_path}",
        )

    geometry = load_target_geometry_from_dxf(dxf_path)

    # Pull the ACTIVE inventory (uploaded if present, else demo) for
    # the "no override" path and to surface in the response — that's
    # the source the frontend will display next to the canvas.
    try:
        source = resolve_inventory_source(PROJECT_ROOT)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    inventory = load_inventory(source.path)

    plan = (
        load_architectural_plan(plan_path) if plan_path.exists()
        else None
    )

    tw = body.tile_width_mm
    th = body.tile_height_mm
    if tw is not None and th is not None:
        # Designer-picked explicit tile. The grid generator clamps to
        # >0 for us; relay any ValueError as a 400 so the UI can show
        # a meaningful message.
        if tw <= 0 or th <= 0:
            raise HTTPException(
                status_code=400,
                detail=f"tile dimensions must be positive: {tw}×{th}",
            )
        layout = generate_tile_layout(
            geometry,
            tile_width_mm=float(tw),
            tile_height_mm=float(th),
        )
        chosen = {
            "tile_width_mm": float(tw),
            "tile_height_mm": float(th),
            "basis": "explicit_override",
        }
    else:
        layout = generate_tile_layout_from_inventory(
            geometry, inventory.slabs,
            source_inventory_path=str(source.path),
        )
        chosen = {
            "tile_width_mm": float(layout.tile_width_mm),
            "tile_height_mm": float(layout.tile_height_mm),
            "basis": "inventory_median",
        }

    log.info(
        "regenerated demo %s: %d pieces · tile %s × %s mm (%s)",
        demo_id, len(layout.pieces),
        chosen["tile_width_mm"], chosen["tile_height_mm"], chosen["basis"],
    )

    response: dict = {
        "demo_id": demo_id,
        "label": meta["label"],
        "layout": serialize_layout_for_editor(layout),
        "tile_choice": chosen,
        "inventory_source_label": source.source_label,
    }
    if plan is not None:
        response["plan"] = serialize_plan_for_editor(plan)
    return response


# ---------------------------------------------------------------------------
# POST /api/demo-layouts/{demo_id}/validate
# ---------------------------------------------------------------------------


class EditedPiece(BaseModel):
    """One piece in the editor's current state.

    V1 pieces are axis-aligned rectangles. ``polygon`` is optional —
    when omitted the backend reconstructs a rectangle from
    ``nominal_x/y_mm`` + ``nominal_width/height_mm``. The frontend's
    seam-drag mechanic never produces non-rectangular pieces, but
    keeping ``polygon`` as an explicit override leaves the door open
    for future shape-aware edits without breaking the contract.
    """
    piece_id: str
    zone_id: str = ""
    nominal_x_mm: float
    nominal_y_mm: float
    nominal_width_mm: float
    nominal_height_mm: float
    polygon: Optional[list[list[float]]] = None
    notes: list[str] = Field(default_factory=list)


class EditedPlanDoorway(BaseModel):
    """One doorway in the editor's plan-edit state.

    ``segment`` is two [x, y] points; ``width_mm`` documents the
    opening width for designer reference (the rule layer doesn't
    consume it today). ``is_main_entrance`` switches R2's penalty
    multiplier (-50 vs -25).
    """
    doorway_id: str
    segment: list[list[float]]
    is_main_entrance: bool = False
    width_mm: float = 0.0
    name: str = ""


class EditedPlanColumn(BaseModel):
    column_id: str
    polygon: list[list[float]]
    name: str = ""


class EditedPlanGuideLine(BaseModel):
    guide_line_id: str
    segment: list[list[float]]
    priority: int = 0
    name: str = ""


class EditedPlanSpace(BaseModel):
    space_id: str
    name: str = ""
    polygon: list[list[float]]
    visibility: str = "medium"


class EditedPlan(BaseModel):
    """Plan-edit state from the annotation tools.

    When present in the request body, replaces the demo's
    file-backed plan for the duration of THIS validation call —
    the original plan JSON on disk is never modified.
    """
    target_id: str = ""
    spaces: list[EditedPlanSpace] = Field(default_factory=list)
    doorways: list[EditedPlanDoorway] = Field(default_factory=list)
    columns: list[EditedPlanColumn] = Field(default_factory=list)
    guide_lines: list[EditedPlanGuideLine] = Field(default_factory=list)


class ValidateLayoutRequest(BaseModel):
    """Request body for the validate endpoint.

    ``pieces`` is the only required field. ``plan`` is optional —
    when omitted the demo's file-backed architectural plan is used
    (same behaviour the foundation milestone shipped); when
    provided, the frontend's annotation-tool edits override the
    file plan for THIS validation only.

    Target geometry is always pinned by ``demo_id``; the editor
    does not currently let designers redraw the floor boundary.
    """
    pieces: list[EditedPiece]
    plan: Optional[EditedPlan] = None


@router.post("/api/demo-layouts/{demo_id}/validate")
def validate_demo_layout(
    demo_id: str, body: ValidateLayoutRequest,
) -> dict:
    """Run the existing architectural rule layer against an edited
    layout.

    The endpoint is stateless: each POST loads the demo's target
    geometry + plan from the registry, builds a layout dict from the
    request's pieces, computes coverage from those pieces, and runs
    ``evaluate_layout`` from ``architectural.rules``. The same code
    path that scored layouts in the retired selector now scores
    designer-edited layouts behind this endpoint.
    """
    meta = DEMOS.get(demo_id)
    if meta is None:
        raise HTTPException(
            status_code=404,
            detail=f"unknown demo_id {demo_id!r}; "
                   f"available: {sorted(DEMOS.keys())}",
        )
    dxf_path = Path(meta["dxf"])
    plan_path = Path(meta["plan"])
    if not dxf_path.exists():
        raise HTTPException(
            status_code=500,
            detail=f"demo DXF missing on disk: {dxf_path}",
        )

    geometry = load_target_geometry_from_dxf(dxf_path)

    # Plan resolution:
    #   * request carries ``plan`` → use the editor's annotation state
    #     directly (lets the designer validate plans that include
    #     freshly-added doorways/columns/guides that aren't on disk).
    #   * otherwise → fall back to the demo's file-backed plan.
    if body.plan is not None:
        plan = _build_plan_from_edits(body.plan, fallback_target_id=demo_id)
    else:
        plan = (
            load_architectural_plan(plan_path) if plan_path.exists()
            else None
        )
        if plan is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"demo {demo_id!r} has no architectural plan on "
                    f"disk and the request did not include one; "
                    f"validation needs a plan to evaluate R2/R5/R6/R7."
                ),
            )

    layout_dict = _build_layout_dict_from_edits(
        geometry, body.pieces,
    )
    report = evaluate_layout(layout_dict, {"pieces": []}, plan)
    return serialize_rule_report_for_editor(report)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _build_layout_dict_from_edits(
    geometry, edited: list[EditedPiece],
) -> dict:
    """Assemble the dict shape ``evaluate_layout`` expects from the
    request's edited pieces + the target geometry.

    Polygons default to axis-aligned rectangles from the nominal
    rect. ``coverage_percentage`` is computed from rectangle areas
    divided by the target's pre-computed ``usable_area_m2`` — a
    cheap approximation that ignores boundary clipping but is
    accurate enough for the V1 editor (pieces don't extend outside
    the boundary under the snap-to-50mm seam drag).
    """
    pieces_out: list[dict] = []
    total_piece_area_mm2 = 0.0
    for ep in edited:
        polygon = ep.polygon or _rect_polygon(
            ep.nominal_x_mm, ep.nominal_y_mm,
            ep.nominal_width_mm, ep.nominal_height_mm,
        )
        bbox_w = ep.nominal_width_mm
        bbox_h = ep.nominal_height_mm
        total_piece_area_mm2 += bbox_w * bbox_h
        pieces_out.append({
            "piece_id": ep.piece_id,
            "zone_id": ep.zone_id,
            "row": 0, "col": 0,
            "nominal_x_mm": ep.nominal_x_mm,
            "nominal_y_mm": ep.nominal_y_mm,
            "nominal_width_mm": ep.nominal_width_mm,
            "nominal_height_mm": ep.nominal_height_mm,
            "bounding_width_mm": bbox_w,
            "bounding_height_mm": bbox_h,
            "actual_cut_polygon": polygon,
            "actual_area_m2": (bbox_w * bbox_h) / 1_000_000.0,
            "is_full_tile": True, "is_edge_piece": False,
            "intersects_hole": False, "interior_holes": [],
            "notes": list(ep.notes),
        })

    usable_m2 = geometry.usable_area_m2 or 0.0
    coverage_pct = (
        (total_piece_area_mm2 / 1_000_000.0) / usable_m2 * 100.0
        if usable_m2 > 0 else 0.0
    )

    return {
        "target": {
            "target_id": geometry.target_id,
            "name": geometry.name,
            "bbox": list(geometry.bbox),
            "boundary": [list(pt) for pt in geometry.boundary],
            "holes": [
                [list(pt) for pt in hole]
                for hole in (geometry.holes or [])
            ],
        },
        "grid": {},
        "pieces": pieces_out,
        "derived": {
            "coverage_percentage": round(coverage_pct, 4),
            "piece_count": len(pieces_out),
        },
    }


def _rect_polygon(x: float, y: float, w: float, h: float) -> list[list[float]]:
    return [
        [x, y],
        [x + w, y],
        [x + w, y + h],
        [x, y + h],
        [x, y],
    ]


def _build_plan_from_edits(
    edited: EditedPlan, *, fallback_target_id: str,
) -> ArchitecturalPlan:
    """Construct an ``ArchitecturalPlan`` from the request's edited
    plan state.

    The frontend sends segments / polygons as ``list[list[float]]``
    because JSON doesn't have tuples; the dataclasses want tuples
    so the conversion happens here. Default thresholds
    (``min_piece_width_mm``, ``min_coverage_ratio``, …) come from
    the dataclass defaults — the editor does not yet expose those
    to designers.
    """
    return ArchitecturalPlan(
        target_id=edited.target_id or fallback_target_id,
        spaces=[
            Space(
                space_id=s.space_id,
                name=s.name,
                polygon=[tuple(pt) for pt in s.polygon],
                visibility=s.visibility,
            )
            for s in edited.spaces
        ],
        doorways=[
            Doorway(
                doorway_id=d.doorway_id,
                segment=(tuple(d.segment[0]), tuple(d.segment[1])),
                is_main_entrance=d.is_main_entrance,
                width_mm=d.width_mm,
                name=d.name,
            )
            for d in edited.doorways
        ],
        columns=[
            Column(
                column_id=c.column_id,
                polygon=[tuple(pt) for pt in c.polygon],
                name=c.name,
            )
            for c in edited.columns
        ],
        guide_lines=[
            GuideLine(
                guide_line_id=g.guide_line_id,
                segment=(tuple(g.segment[0]), tuple(g.segment[1])),
                priority=g.priority,
                name=g.name,
            )
            for g in edited.guide_lines
        ],
    )


# ---------------------------------------------------------------------------
# POST /api/demo-layouts/{demo_id}/match-inventory
# ---------------------------------------------------------------------------


class InventoryMatchPiece(BaseModel):
    """One piece in the matcher request body. Only the nominal
    dimensions are required — the matcher doesn't consume polygons.
    """
    piece_id: str
    nominal_width_mm: float
    nominal_height_mm: float


class MatchInventoryRequest(BaseModel):
    """Request body for the inventory-matcher endpoint.

    Same demo_id pinning as ``/validate`` — the target geometry and
    architectural plan don't affect matching at this layer (it's
    purely dimensional), so they're not in the body.

    ``top_k`` controls how many candidates per piece are returned.
    The default of 3 keeps the Step-2 preview compact; Step-4 auto-
    assignment passes a much larger value so it can pick unique
    slabs across many pieces with identical dimensions (the matcher
    otherwise returns the same 3 lowest-waste slabs for every piece
    in a tile-uniform layout, starving the assignment of choices).
    Clamped at 200 to keep response bodies bounded.
    """
    pieces: list[InventoryMatchPiece]
    allow_rotation: bool = True
    top_k: int | None = None


@router.post("/api/demo-layouts/{demo_id}/match-inventory")
def match_demo_inventory(
    demo_id: str, body: MatchInventoryRequest,
) -> dict:
    """For each piece in the request, return the top inventory slabs
    that can cover it.

    This is a READ-ONLY preview — no slab is reserved, the same
    slab can match multiple pieces. The final assignment layer
    (later milestone) will tighten this into one-slab-per-piece
    with global waste optimisation.

    Inventory source is pinned to the project-wide clean-slabs JSON
    (see ``DEMO_INVENTORY_PATH``). When the inventory layer becomes
    per-project this resolution will move into the demo registry.
    """
    if demo_id not in DEMOS:
        raise HTTPException(
            status_code=404,
            detail=f"unknown demo_id {demo_id!r}; "
                   f"available: {sorted(DEMOS.keys())}",
        )
    try:
        source = resolve_inventory_source(PROJECT_ROOT)
    except FileNotFoundError as exc:
        # No usable inventory file at all — surfaced as a 500 since
        # this is a server-side configuration problem, not the
        # frontend's fault.
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    result = load_inventory_slabs(source.path)
    # Resolve top_k. None / unset → matcher default (3). Anything >0
    # is clamped to 200 (matches what any sensible UI could display
    # without choking on the response body).
    effective_top_k: int | None = None
    if body.top_k is not None and body.top_k > 0:
        effective_top_k = min(int(body.top_k), 200)

    pieces_out = []
    by_status: dict[str, int] = {}
    for p in body.pieces:
        kwargs: dict = {"allow_rotation": body.allow_rotation}
        if effective_top_k is not None:
            kwargs["top_k"] = effective_top_k
        status, candidates = match_piece(
            p.nominal_width_mm, p.nominal_height_mm, result.slabs,
            **kwargs,
        )
        by_status[status] = by_status.get(status, 0) + 1
        pieces_out.append({
            "piece_id": p.piece_id,
            "required_width_mm": p.nominal_width_mm,
            "required_height_mm": p.nominal_height_mm,
            "required_area_m2": round(
                (p.nominal_width_mm * p.nominal_height_mm) / 1_000_000.0, 4,
            ),
            "status": status,
            "candidates": [candidate_to_dict(c) for c in candidates],
        })

    return {
        "demo_id": demo_id,
        "inventory": _inventory_info_dict(source, result),
        # Legacy top-level fields, kept until the frontend stops
        # reading them. New frontend code should use ``inventory``.
        "inventory_path": _relative_to_root(source.path),
        "inventory_count": result.valid_count,
        "allow_rotation": body.allow_rotation,
        "pieces": pieces_out,
        "summary": {
            "exact_fit": by_status.get("exact_fit", 0),
            "multiple_options": by_status.get("multiple_options", 0),
            "matched": by_status.get("matched", 0),
            "no_match": by_status.get("no_match", 0),
            "total_pieces": len(body.pieces),
        },
    }


# ---------------------------------------------------------------------------
# GET /api/inventory/info
# ---------------------------------------------------------------------------


@router.get("/api/inventory/info")
def get_inventory_info() -> dict:
    """Report which inventory the API is currently using.

    Called by the editor on boot so the panel can show "Inventory:
    real project export · 8 valid / 0 invalid" without having to
    POST a matcher request first. The endpoint runs the same resolver
    + loader the matcher uses — so if this says "demo fallback", the
    matcher will too.

    Returns a 500 when no usable inventory file is on disk; surfaces
    the configured env override path even when broken so operators
    can see what was attempted.
    """
    try:
        source = resolve_inventory_source(PROJECT_ROOT)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    result = load_inventory_slabs(source.path)
    return _inventory_info_dict(source, result)


def _inventory_info_dict(source, result) -> dict:
    """Shared serialiser for the inventory header block. Both the
    matcher response and the info endpoint use it so the frontend
    sees the same fields in both places.

    0.1.44 — also embeds dimensional statistics (median / mean /
    min / max width × height + slab count) so the Step-3 panel can
    display "median 1790 × 1730 mm · 8 valid slabs" and the user
    can pick the layout-grid size from real numbers, not assumed
    defaults. Stats are skipped (set to None) when the inventory
    has no valid slabs — the engine would refuse to compute them
    in that case anyway.
    """
    from placement_engine.layout.inventory_stats import (
        compute_inventory_dimension_summary,
    )

    stats: dict | None = None
    if result.valid_count > 0:
        try:
            summary = compute_inventory_dimension_summary(result.slabs)
            stats = summary.to_dict()
            # Consistency hint: if the spread between min and max is
            # huge (more than 2× the median), the inventory is bi-modal
            # or contains outliers. Layout sizing from median is still
            # the right call but the UI should warn the designer.
            spread_w = (
                (stats["max_width_mm"] - stats["min_width_mm"])
                / stats["median_width_mm"]
                if stats["median_width_mm"] > 0 else 0.0
            )
            spread_h = (
                (stats["max_height_mm"] - stats["min_height_mm"])
                / stats["median_height_mm"]
                if stats["median_height_mm"] > 0 else 0.0
            )
            stats["is_inconsistent"] = spread_w > 1.0 or spread_h > 1.0
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("inventory stats failed: %s", exc)
            stats = None

    return {
        "source_label": source.source_label,
        "source_description": source_label_description(source.source_label),
        "source_path": _relative_to_root(source.path),
        "valid_count": result.valid_count,
        "skipped_count": result.skipped_count,
        "total_records": result.total_records,
        "stats": stats,
    }


def _relative_to_root(path: Path) -> str:
    """Return ``path`` relative to PROJECT_ROOT when possible, else as
    its full POSIX form. The relative form is shorter (good for UI)
    but env-override paths can sit outside the project, in which case
    we just show the absolute path so the operator can find it."""
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# POST /api/inventory/upload — Step 3 real upload (Excel + photos)
# ---------------------------------------------------------------------------


@router.post("/api/inventory/upload")
async def upload_inventory(
    excel: UploadFile = File(...),
    images: list[UploadFile] = File(default=[]),
) -> dict:
    """Receive an Excel file + slab photos, run the slab-intake
    pipeline, and make the result the active inventory.

    Multipart form fields:
      * ``excel`` — single Excel file (.xlsx / .xls)
      * ``images`` — zero or more image files (jpg / png / etc.)

    Photo matching: the pipeline reads the image filenames (NOT the
    bytes). Filenames carry the suffix-based identifier the project
    has shipped with since V1 — see
    ``placement_engine.slab_intake.pipeline._pick_suffix_candidate``
    for the canonical algorithm. Files whose stems don't map to any
    Excel row come back in ``summary.unmatched_photos`` so the
    designer can fix the naming and re-upload.

    Returns the summary the Step-3 panel renders directly + the
    session id (kept for traceability; not required by other
    endpoints since only one upload is active at a time).
    """
    if not excel.filename:
        raise HTTPException(status_code=400, detail="missing excel file")
    excel_bytes = await excel.read()
    if not excel_bytes:
        raise HTTPException(status_code=400, detail="excel file is empty")

    image_payloads: list[tuple[str, bytes]] = []
    for f in images:
        if not f.filename:
            continue
        data = await f.read()
        if not data:
            continue
        image_payloads.append((f.filename, data))

    try:
        session = process_upload(
            excel_bytes=excel_bytes,
            excel_filename=excel.filename,
            images=image_payloads,
        )
    except Exception as exc:
        log.exception("inventory upload failed")
        raise HTTPException(
            status_code=400,
            detail=f"failed to parse upload: {exc}",
        ) from exc

    return {
        "session_id": session.session_id,
        "uploaded_at": session.uploaded_at,
        "excel_filename": session.excel_filename,
        "image_count": session.image_count,
        "summary": session.summary,
    }


@router.get("/api/inventory/current")
def get_current_inventory() -> dict:
    """Report the currently active uploaded inventory, if any.

    Frontend uses this on boot to restore the Step-3 panel state after
    a refresh — the upload itself doesn't survive a server restart
    (tempdir-based storage), but while the server is up the panel can
    show the same summary the upload returned.
    """
    session = get_active_upload()
    if session is None:
        return {"active": False}
    return {
        "active": True,
        "session_id": session.session_id,
        "uploaded_at": session.uploaded_at,
        "excel_filename": session.excel_filename,
        "image_count": session.image_count,
        "summary": session.summary,
    }


@router.delete("/api/inventory/current")
def delete_current_inventory() -> dict:
    """Discard the active uploaded inventory, returning the matcher
    to the demo / real fallback. Used by the Step-3 "remove
    uploaded" button (not yet exposed in the UI but the route lives
    here so the cleanup path is testable)."""
    clear_active_upload()
    return {"active": False}


# ---------------------------------------------------------------------------
# GET /api/inventory/slab-image/{slab_id}
# ---------------------------------------------------------------------------


@router.get("/api/inventory/slab-crop-info/{slab_id}")
def get_slab_crop_info(slab_id: str) -> dict:
    """Tell the frontend whether the green-box safe crop is available
    for this slab — used by the Step-4 properties card to flip the
    "Safe crop not detected — using full image" warning on or off.

    Returns ``{available: false}`` when no upload session is active
    OR when this slab's record isn't in the metadata map OR when its
    ``green_box_detected`` flag is false. Doesn't 404 (clients can
    call this for any slab id safely)."""
    active = get_active_upload()
    if active is None:
        return {"available": False, "reason": "no_active_upload"}
    meta = active.image_metadata_by_slab.get(slab_id)
    if meta is None:
        return {"available": False, "reason": "no_metadata"}
    if not meta.green_box_detected:
        return {
            "available": False,
            "reason": "green_box_not_detected",
            "warnings": list(meta.warnings),
        }
    return {
        "available": True,
        "crop_x": meta.crop_x,
        "crop_y": meta.crop_y,
        "crop_width": meta.crop_width,
        "crop_height": meta.crop_height,
        "confidence_score": meta.confidence_score,
    }


@router.get("/api/inventory/slab-image/{slab_id}")
def get_slab_image(slab_id: str, crop: str | None = None):
    """Serve the on-disk photo for one slab in the active inventory.

    Used by the Step-4 candidate panel to show thumbnails. Returns
    404 when the slab id is unknown OR when the slab has no image
    on disk (the matcher's no-photo flag).

    Query parameter ``crop=safe-area`` (0.1.47) returns the cropped
    "green-box" usable area instead of the raw photo. When the green
    box could not be detected (or the crop pass didn't run), the
    endpoint falls back to the original image and sets
    ``X-Slab-Image-Crop: fallback`` so the UI can show a "safe crop
    not detected" warning. Without the query parameter, behaviour is
    unchanged — the full original image is served.
    """
    try:
        source = resolve_inventory_source(PROJECT_ROOT)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    result = load_inventory_slabs(source.path)
    slab = next((s for s in result.slabs if s.slab_id == slab_id), None)
    if slab is None:
        raise HTTPException(
            status_code=404,
            detail=f"unknown slab_id {slab_id!r}",
        )
    if not slab.image_path:
        raise HTTPException(
            status_code=404,
            detail=f"slab {slab_id!r} has no image",
        )
    img_path = Path(slab.image_path)
    if not img_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"image file missing: {slab.image_path}",
        )

    if crop == "safe-area":
        active = get_active_upload()
        if active is not None:
            meta = active.image_metadata_by_slab.get(slab_id)
            if (
                meta is not None
                and meta.green_box_detected
                and meta.processed_image_path
            ):
                processed = Path(meta.processed_image_path)
                if processed.exists():
                    return FileResponse(
                        processed,
                        headers={"X-Slab-Image-Crop": "safe-area"},
                    )
        # Safe-crop requested but unavailable — fall back to the
        # original and flag it in a response header. The image
        # endpoint never 404s on this path because returning a
        # usable picture is better than a hard failure here.
        return FileResponse(
            img_path,
            headers={"X-Slab-Image-Crop": "fallback"},
        )

    return FileResponse(img_path)


# ---------------------------------------------------------------------------
# POST /api/demo-layouts/{demo_id}/export-dxf — factory cut plan
# ---------------------------------------------------------------------------


class ExportDxfPiece(BaseModel):
    """One finalised piece in the export request.

    Polygon is REQUIRED for the DXF (the boundary outline + cuts
    are the whole point of the file). All other geometric fields
    are nominal-rect data the route uses to label the piece and
    fall back to when the matcher response doesn't list this slab
    (e.g. user assigned a slab that has since been replaced)."""
    piece_id: str
    polygon: list[list[float]]
    nominal_width_mm: float
    nominal_height_mm: float


class ExportDxfRequest(BaseModel):
    """POST body for the DXF export endpoint.

    The editor sends the FINALISED pieces (those the designer
    locked in at Step 2 → 3) plus the piece_id → slab_id map. The
    backend resolves slab metadata from the active inventory so the
    request stays small and the file stays consistent with whatever
    inventory the matcher is currently using."""
    pieces: list[ExportDxfPiece]
    assignments: dict[str, str | None]
    # Optional plan annotations — emitted on their own DXF layers
    # when present.
    doorways: list[list[list[float]]] = Field(default_factory=list)
    seams: list[list[list[float]]] = Field(default_factory=list)
    allow_rotation: bool = True


@router.post("/api/demo-layouts/{demo_id}/export-dxf")
def export_demo_dxf(demo_id: str, body: ExportDxfRequest):
    """Build a factory-readable DXF for the current Step-4 assignment.

    Refuses (400) when ANY piece has no slab assigned — the export
    is meant to be the production handoff, not a preview. Use the
    matcher's response only as a metadata fallback; piece geometry
    comes directly from the request body so dragged seams flow into
    the export.
    """
    meta = DEMOS.get(demo_id)
    if meta is None:
        raise HTTPException(
            status_code=404,
            detail=f"unknown demo_id {demo_id!r}; "
                   f"available: {sorted(DEMOS.keys())}",
        )
    dxf_path = Path(meta["dxf"])
    if not dxf_path.exists():
        raise HTTPException(
            status_code=500,
            detail=f"demo DXF missing on disk: {dxf_path}",
        )

    # Refuse incomplete assignments. We let the FRONTEND disable the
    # button preemptively but still guard here so a malformed request
    # can't slip through.
    unassigned = [
        p.piece_id for p in body.pieces
        if not body.assignments.get(p.piece_id)
    ]
    if not body.pieces:
        raise HTTPException(
            status_code=400, detail="no pieces supplied for export",
        )
    if unassigned:
        raise HTTPException(
            status_code=400,
            detail=(
                "cannot export DXF: "
                f"{len(unassigned)} of {len(body.pieces)} pieces are "
                f"unassigned (first few: {unassigned[:5]})"
            ),
        )

    geometry = load_target_geometry_from_dxf(dxf_path)

    # Resolve slab metadata for label text. Looking it up via the
    # matcher gives us the same width / cut / waste numbers the UI
    # is showing.
    try:
        source = resolve_inventory_source(PROJECT_ROOT)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    result = load_inventory_slabs(source.path)
    slab_by_id = {s.slab_id: s for s in result.slabs}

    # Walk pieces, build DxfPieceInputs. Re-run match_piece per
    # piece so we get the rotation_needed + waste_fraction even if
    # the matcher response was stale.
    dxf_pieces: list[DxfPieceInput] = []
    for p in body.pieces:
        slab_id = body.assignments[p.piece_id]
        # ``slab_id`` is non-empty here (we returned 400 earlier).
        assert slab_id is not None
        slab = slab_by_id.get(slab_id)
        slab_w = float(slab.width_mm) if slab else None
        slab_h = float(slab.height_mm) if slab else None
        # Re-derive the candidate's rotation + waste from the
        # matcher so the DXF labels match what the UI showed.
        rotation_needed = False
        waste_fraction: float | None = None
        if slab is not None:
            status, candidates = match_piece(
                p.nominal_width_mm, p.nominal_height_mm,
                [slab],
                allow_rotation=body.allow_rotation,
            )
            if candidates:
                rotation_needed = candidates[0].rotation_needed
                waste_fraction = candidates[0].waste_fraction
        dxf_pieces.append(DxfPieceInput(
            piece_id=p.piece_id,
            polygon=[(x, y) for x, y in p.polygon],
            nominal_width_mm=p.nominal_width_mm,
            nominal_height_mm=p.nominal_height_mm,
            slab_id=slab_id,
            slab_width_mm=slab_w,
            slab_height_mm=slab_h,
            cut_width_mm=p.nominal_width_mm,
            cut_height_mm=p.nominal_height_mm,
            rotation_needed=rotation_needed,
            waste_fraction=waste_fraction,
        ))

    payload = build_dxf_bytes(
        demo_id=demo_id,
        boundary=[(pt[0], pt[1]) for pt in geometry.boundary],
        holes=[[(pt[0], pt[1]) for pt in hole] for hole in (geometry.holes or [])],
        pieces=dxf_pieces,
        seams=[[(pt[0], pt[1]) for pt in seg] for seg in body.seams],
        doorways=[[(pt[0], pt[1]) for pt in seg] for seg in body.doorways],
    )

    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"factory_cut_plan_{demo_id}_{ts}.dxf"
    from fastapi.responses import Response
    return Response(
        content=payload,
        media_type="application/dxf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
