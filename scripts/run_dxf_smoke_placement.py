#!/usr/bin/env python3
"""V1 DXF bbox-smoke placement runner.

Loads a V1 slab inventory + optional processed images, reads a
standardized DXF as the target geometry, runs the bbox-only shelf
packer against the DXF's bounding rectangle, and writes designer-
facing previews + a canonical placement JSON.

This is NOT polygon-aware placement — slabs may extend outside the
irregular boundary or overlap holes. The geometric/textured previews
will show that visually (boundary outline drawn over the bbox-placed
slabs). For polygon-aware placement see
``scripts/run_dxf_polygon_placement.py``.

Example:

    python3 scripts/run_dxf_smoke_placement.py \\
        --inventory      outputs/slab_ingestion_test/clean_slabs.json \\
        --image-metadata outputs/image_intake/image_metadata.json \\
        --dxf            examples/cad_inputs/demo/demo_l_shape_floor.dxf \\
        --output         outputs/dxf_smoke_placement

Default output:

    <output>/placement.json
    <output>/preview_geometric.png
    <output>/preview_textured.png
    (<output>/preview_debug.png — only with --include-debug)
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from placement_engine.inventory import (  # noqa: E402
    attach_processed_images,
    load_inventory,
    validate_inventory,
)
from placement_engine.inventory.shelf_pack import shelf_pack  # noqa: E402
from placement_engine.preview import (  # noqa: E402
    render_debug,
    render_geometric,
    render_textured,
    view_from_shelf_pack_result,
    write_placement_json,
)
from placement_engine.preview.schema import _target_view_from_geometry  # noqa: E402
from placement_engine.target_area import load_target_geometry_from_dxf  # noqa: E402


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--inventory", required=True, type=Path,
                   help="Path to clean_slabs.json.")
    p.add_argument("--dxf", required=True, type=Path,
                   help="Path to a standardized DXF target.")
    p.add_argument("--image-metadata", type=Path, default=None,
                   help="Optional image_metadata.json for processed images.")
    p.add_argument("--target-name", type=str, default=None,
                   help="Override the target name (defaults to DXF filename).")
    p.add_argument("--output", type=Path,
                   default=Path("outputs/dxf_smoke_placement"))
    p.add_argument("--include-debug", action="store_true",
                   help="Also write preview_debug.png (engineer-only).")
    p.add_argument("--show-dimensions", action="store_true",
                   help="Annotate slab dimensions in the geometric preview.")
    p.add_argument("--no-preview", action="store_true")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("run_dxf_smoke_placement")

    geometry = load_target_geometry_from_dxf(args.dxf, name=args.target_name)
    bbox_target = geometry.as_bounding_target_area()

    inventory = load_inventory(args.inventory)
    issues = validate_inventory(inventory)
    if args.image_metadata is not None:
        attach_processed_images(inventory, args.image_metadata)

    result = shelf_pack(inventory.slabs, bbox_target)

    # Build the view from the shelf-pack result, then *replace* its
    # rectangular TargetView with the real DXF geometry so previews
    # draw the irregular boundary + holes (not the bbox).
    view = view_from_shelf_pack_result(result, metadata={
        "smoke_mode": "bbox_only",
        "dxf_source": str(geometry.source_dxf_path) if geometry.source_dxf_path else None,
        "inventory_source": str(args.inventory),
        "validation_issues": [
            {"slab_id": i.slab_id, "code": i.code, "message": i.message}
            for i in issues
        ],
    })
    view.target = _target_view_from_geometry(geometry)

    # --- terminal summary ---
    print()
    print("=" * 78)
    print(f"DXF target  : {geometry.name}")
    print(f"  bbox (mm)    : {geometry.bbox}")
    print(f"  boundary area: {geometry.boundary_area_m2:.2f} m²")
    print(f"  holes area   : {geometry.holes_area_m2:.2f} m²")
    print(f"  usable area  : {geometry.usable_area_m2:.2f} m²")
    print(f"Inventory   : {len(inventory.slabs)} slabs")
    print(f"Placed (bbox): {view.placed_count}")
    print(f"Overflow     : {view.rejected_count}")
    print(f"Seams        : {len(view.seams)}")
    print(f"Coverage     : {view.coverage_percentage:.1f}%  "
          f"({view.placed_area_m2:.2f} / {geometry.usable_area_m2:.2f} m² usable)")
    print()
    print("⚠  This is bbox-only smoke placement. Slabs may extend outside the")
    print("   irregular boundary or overlap holes — visible in the previews.")
    print("=" * 78)

    args.output.mkdir(parents=True, exist_ok=True)
    json_path = write_placement_json(view, args.output / "placement.json")
    log.info("Wrote JSON           : %s", json_path)

    if not args.no_preview:
        render_geometric(
            view, args.output / "preview_geometric.png",
            show_dimensions=args.show_dimensions,
        )
        log.info("Wrote geometric PNG  : %s", args.output / "preview_geometric.png")
        render_textured(view, args.output / "preview_textured.png")
        log.info("Wrote textured PNG   : %s", args.output / "preview_textured.png")
        if args.include_debug:
            render_debug(view, args.output / "preview_debug.png")
            log.info("Wrote debug PNG      : %s", args.output / "preview_debug.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
