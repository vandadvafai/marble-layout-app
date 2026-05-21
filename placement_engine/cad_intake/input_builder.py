"""Build a `ProjectInput` from a standardized DXF.

Pipeline:

  read_dxf(path)
    → entities_on_layer(doc, AI_PROJECT_BOUNDARY) → exactly one closed polyline
    → entities_on_layer(doc, AI_HOLES_CUTOUTS)    → zero+ closed polylines
    → validate holes fit inside the boundary and don't overlap
    → wrap into a `ProjectInput` (when a slab inventory is supplied) or
      a raw dict (geometry-only, for hand-editing by the designer)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from shapely.geometry import Polygon as ShPolygon

from placement_engine.cad_intake.dxf_reader import (
    CADIntakeError,
    LAYER_HOLES_CUTOUTS,
    LAYER_PROJECT_BOUNDARY,
    entities_on_layer,
    read_dxf,
)
from placement_engine.cad_intake.geometry_extractor import (
    extract_closed_polylines,
)
from placement_engine.models import (
    PolygonCoords,
    ProjectInput,
    SourceFile,
    StrategyName,
)
from placement_engine.utils.test_inventory import SlabInventorySpec


# A default test inventory matched roughly to typical 3,200 × 1,800 mm
# Italian quarry slabs. Six slabs, deterministic IDs. Used when the
# CLI is invoked with `--include-test-slabs`.
_TEST_SLABS: tuple[dict, ...] = (
    {"slab_id": "S001", "width": 3200, "height": 1800, "thickness": 20,
     "image_path": "images/slab_S001.png", "vein_direction": "horizontal"},
    {"slab_id": "S002", "width": 3200, "height": 1800, "thickness": 20,
     "image_path": "images/slab_S002.png", "vein_direction": "horizontal"},
    {"slab_id": "S003", "width": 3200, "height": 1800, "thickness": 20,
     "image_path": "images/slab_S003.png", "vein_direction": "horizontal"},
    {"slab_id": "S004", "width": 3200, "height": 1800, "thickness": 20,
     "image_path": "images/slab_S004.png", "vein_direction": "horizontal"},
    {"slab_id": "S005", "width": 3200, "height": 1800, "thickness": 20,
     "image_path": "images/slab_S005.png", "vein_direction": "horizontal"},
    {"slab_id": "S006", "width": 3200, "height": 1800, "thickness": 20,
     "image_path": "images/slab_S006.png", "vein_direction": "horizontal"},
)


_DEFAULT_RULES: dict[str, Any] = {
    "allowed_rotations": [0, 90],
    "min_piece_width": 120,
    "min_piece_height": 120,
    "min_piece_area": 25000,
    "seam_tolerance": 2,
    "allow_partial_slab_use": True,
    "allow_piece_reuse_from_offcuts": False,
    "max_waste_percentage_target": 30,
}

_DEFAULT_DESIGN_REQUIREMENTS: dict[str, Any] = {
    "general_notes": "Generated from standardized CAD input.",
    "priority": "balanced",
    "avoid_high_visibility_seams": False,
    "avoid_defects": True,
}


# ---------------------------------------------------------------------------
# Geometry validation specific to CAD intake — runs before downstream
# Pydantic / engine validation so error messages reference layers and
# entity counts the designer can act on.
# ---------------------------------------------------------------------------


def _validate_boundary(boundary: PolygonCoords) -> ShPolygon:
    poly = ShPolygon(boundary)
    if not poly.is_valid:
        raise CADIntakeError(
            f"Project boundary on layer {LAYER_PROJECT_BOUNDARY!r} is "
            f"self-intersecting or otherwise invalid. Open it in "
            f"Rhino/AutoCAD and clean the polyline."
        )
    if poly.area <= 0:
        raise CADIntakeError(
            f"Project boundary on layer {LAYER_PROJECT_BOUNDARY!r} has "
            f"zero area. Did the export collapse it onto a single line?"
        )
    return poly


def _validate_holes(
    holes: Sequence[PolygonCoords], boundary_poly: ShPolygon
) -> None:
    hole_polys: list[ShPolygon] = []
    for i, hole_coords in enumerate(holes):
        h = ShPolygon(hole_coords)
        if not h.is_valid:
            raise CADIntakeError(
                f"Hole #{i + 1} on layer {LAYER_HOLES_CUTOUTS!r} is "
                f"self-intersecting or otherwise invalid."
            )
        if h.area <= 0:
            raise CADIntakeError(
                f"Hole #{i + 1} on layer {LAYER_HOLES_CUTOUTS!r} has "
                f"zero area."
            )
        if not boundary_poly.contains(h):
            raise CADIntakeError(
                f"Hole #{i + 1} on layer {LAYER_HOLES_CUTOUTS!r} is not "
                f"fully inside the project boundary. Move it inside "
                f"before exporting."
            )
        hole_polys.append(h)

    for i in range(len(hole_polys)):
        for j in range(i + 1, len(hole_polys)):
            inter = hole_polys[i].intersection(hole_polys[j])
            # 1 mm² tolerance matches the rest of the engine.
            if not inter.is_empty and inter.area > 1.0:
                raise CADIntakeError(
                    f"Holes #{i + 1} and #{j + 1} on layer "
                    f"{LAYER_HOLES_CUTOUTS!r} overlap by "
                    f"{inter.area:.0f} mm². Merge or separate them."
                )


def _extract_boundary_and_holes(
    doc, source_path: Path
) -> tuple[PolygonCoords, list[PolygonCoords]]:
    """Pull the boundary and holes from the DXF, enforcing the contract."""
    boundary_entities = entities_on_layer(doc, LAYER_PROJECT_BOUNDARY)
    if not boundary_entities:
        raise CADIntakeError(
            f"Layer {LAYER_PROJECT_BOUNDARY!r} is empty or missing in "
            f"{source_path.name!r}. Draw exactly one closed polyline "
            f"on this layer for the project's usable surface."
        )

    boundaries = extract_closed_polylines(
        boundary_entities, LAYER_PROJECT_BOUNDARY
    )
    if len(boundaries) != 1:
        raise CADIntakeError(
            f"Expected exactly one closed polyline on layer "
            f"{LAYER_PROJECT_BOUNDARY!r}. Found {len(boundaries)}. "
            f"Please isolate one final project boundary before running "
            f"the tool."
        )
    boundary = boundaries[0]

    hole_entities = entities_on_layer(doc, LAYER_HOLES_CUTOUTS)
    holes = extract_closed_polylines(hole_entities, LAYER_HOLES_CUTOUTS)
    return boundary, holes


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def project_usable_area(
    boundary: PolygonCoords, holes: Sequence[PolygonCoords]
) -> float:
    """Boundary area minus the area of every hole (mm²)."""
    area = ShPolygon(boundary).area
    for hole in holes:
        area -= ShPolygon(hole).area
    return float(area)


def build_project_input_dict(
    cad_path: str | Path,
    *,
    project_id: str,
    project_type: str = "floor",
    include_test_slabs: bool = False,
    test_slab_spec: SlabInventorySpec | None = None,
    options_requested: Sequence[StrategyName] | None = None,
    random_seed: int = 42,
) -> dict[str, Any]:
    """Read a standardized DXF and return a raw engine-input dict.

    Returns the same shape as `ProjectInput.model_dump()` would: ready
    to write to disk as JSON.

    Slab inventory resolution, in priority order:
      1. `test_slab_spec` given → resolve it against the project usable
         area (this is how the validation suite and the CLI's
         `--test-slab-count` flag attach slabs).
      2. `include_test_slabs=True` (and no spec) → the fixed legacy
         6 × 3 200 × 1 800 inventory.
      3. neither → `slabs` is empty; the designer fills it in.
    """
    cad_path = Path(cad_path)
    doc = read_dxf(cad_path)
    boundary, holes = _extract_boundary_and_holes(doc, cad_path)

    boundary_poly = _validate_boundary(boundary)
    _validate_holes(holes, boundary_poly)

    if test_slab_spec is not None:
        usable_area = project_usable_area(boundary, holes)
        slabs: list[dict] = test_slab_spec.resolve(usable_area)
    elif include_test_slabs:
        slabs = list(_TEST_SLABS)
    else:
        slabs = []

    return {
        "project_id": project_id,
        "project_type": project_type,
        "units": "mm",
        "layout": {
            "source_file": SourceFile(
                type="dxf",
                path=str(cad_path),
                notes="Generated from standardized CAD layers (AI_PROJECT_BOUNDARY + AI_HOLES_CUTOUTS).",
            ).model_dump(),
            "boundary": boundary,
            "holes": holes,
            "zones": [],
        },
        "slabs": slabs,
        "design_requirements": dict(_DEFAULT_DESIGN_REQUIREMENTS),
        "rules": dict(_DEFAULT_RULES),
        "options_requested": list(options_requested) if options_requested else ["balanced"],
        "random_seed": random_seed,
    }


def build_project_input(
    cad_path: str | Path,
    *,
    project_id: str,
    project_type: str = "floor",
    options_requested: Sequence[StrategyName] | None = None,
    random_seed: int = 42,
) -> ProjectInput:
    """Same as `build_project_input_dict` with the default test slab
    inventory attached and the result validated through Pydantic, so
    the returned `ProjectInput` is immediately runnable.
    """
    payload = build_project_input_dict(
        cad_path,
        project_id=project_id,
        project_type=project_type,
        include_test_slabs=True,
        options_requested=options_requested,
        random_seed=random_seed,
    )
    return ProjectInput.model_validate(payload)
