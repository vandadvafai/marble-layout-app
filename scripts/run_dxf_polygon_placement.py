#!/usr/bin/env python3
"""V1 DXF packer comparison — bbox vs polygon-aware vs BLF.

Loads a V1 slab inventory + optional processed images, reads a
standardized DXF as the target geometry, and runs **three** packers
side-by-side:

    1. ``shelf_pack``    against the DXF bounding rectangle (bbox-only)
    2. ``polygon_pack``  against the irregular boundary minus holes
    3. ``blf_pack``      Bottom-Left Fill grid scan + polygon acceptance

Default output (designer-facing):

    <output>/placement_comparison.json   — all three views in one file
    <output>/comparison_geometric.png    — 3-panel contact sheet, geometric
    <output>/comparison_textured.png     — 3-panel contact sheet, textured

Opt-in (engineer-only):

    <output>/debug_<packer>.png          — one per packer with --include-debug

Example:

    python3 scripts/run_dxf_polygon_placement.py \\
        --inventory      outputs/slab_ingestion_test/clean_slabs.json \\
        --image-metadata outputs/image_intake/image_metadata.json \\
        --dxf            examples/cad_inputs/demo/demo_l_shape_floor.dxf \\
        --output         outputs/dxf_polygon_placement
"""

from __future__ import annotations

import argparse
import json
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
from placement_engine.inventory.blf_pack import (  # noqa: E402
    DEFAULT_GRID_STEP_MM,
    blf_pack,
)
from placement_engine.inventory.polygon_pack import polygon_pack  # noqa: E402
from placement_engine.inventory.shelf_pack import shelf_pack  # noqa: E402
from placement_engine.preview import (  # noqa: E402
    render_debug,
    render_geometric_comparison,
    render_textured_comparison,
    view_from_blf_pack_result,
    view_from_polygon_pack_result,
    view_from_shelf_pack_result,
)
from placement_engine.preview.schema import _target_view_from_geometry  # noqa: E402
from placement_engine.target_area import load_target_geometry_from_dxf  # noqa: E402


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--inventory", required=True, type=Path)
    p.add_argument("--dxf", required=True, type=Path)
    p.add_argument("--image-metadata", type=Path, default=None)
    p.add_argument("--target-name", type=str, default=None)
    p.add_argument("--output", type=Path,
                   default=Path("outputs/dxf_polygon_placement"))
    p.add_argument("--blf-grid-step-mm", type=float, default=DEFAULT_GRID_STEP_MM,
                   help=f"BLF grid step in mm (default: {DEFAULT_GRID_STEP_MM}).")
    p.add_argument("--include-debug", action="store_true",
                   help="Also write one debug PNG per packer.")
    p.add_argument("--show-dimensions", action="store_true",
                   help="Annotate dimensions in geometric panels.")
    p.add_argument("--no-preview", action="store_true")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("run_dxf_polygon_placement")

    geometry = load_target_geometry_from_dxf(args.dxf, name=args.target_name)
    bbox_target = geometry.as_bounding_target_area()
    inventory = load_inventory(args.inventory)
    validate_inventory(inventory)
    if args.image_metadata is not None:
        attach_processed_images(inventory, args.image_metadata)

    bbox_result = shelf_pack(inventory.slabs, bbox_target)
    polygon_result = polygon_pack(inventory.slabs, geometry)
    blf_result = blf_pack(
        inventory.slabs, geometry, grid_step_mm=args.blf_grid_step_mm,
    )

    # Build views. The shelf-pack view is built against the rectangular
    # TargetArea; replace its target with the DXF geometry so previews
    # draw the irregular boundary on top of the bbox-placed slabs (the
    # "smoke caveat" visualisation).
    bbox_view = view_from_shelf_pack_result(bbox_result, metadata={
        "smoke_mode": "bbox_only",
        "dxf_source": str(geometry.source_dxf_path) if geometry.source_dxf_path else None,
    })
    bbox_view.target = _target_view_from_geometry(geometry)
    polygon_view = view_from_polygon_pack_result(polygon_result)
    blf_view = view_from_blf_pack_result(blf_result)

    views = {
        "shelf_pack (bbox)": bbox_view,
        "polygon_pack": polygon_view,
        "blf_pack": blf_view,
    }

    # --- terminal summary ---
    print()
    print("=" * 80)
    print(f"DXF target  : {geometry.name}")
    print(f"  usable area : {geometry.usable_area_m2:.2f} m²")
    print(f"Inventory   : {len(inventory.slabs)} slabs")
    print()
    for name, view in views.items():
        print(f"  {name:24s}  "
              f"{view.placed_count} placed, "
              f"{view.rejected_count} rejected, "
              f"{view.coverage_percentage:.1f}% coverage, "
              f"{len(view.seams)} seams")
    print("=" * 80)

    # --- artefacts ---
    args.output.mkdir(parents=True, exist_ok=True)
    json_path = args.output / "placement_comparison.json"
    json_path.write_text(
        json.dumps(
            {name: view.to_dict() for name, view in views.items()},
            indent=2, ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    log.info("Wrote comparison JSON : %s", json_path)

    if not args.no_preview:
        geo_path = render_geometric_comparison(
            views, args.output / "comparison_geometric.png",
            show_dimensions=args.show_dimensions,
        )
        log.info("Wrote geometric sheet : %s", geo_path)
        tex_path = render_textured_comparison(
            views, args.output / "comparison_textured.png",
        )
        log.info("Wrote textured sheet  : %s", tex_path)

        if args.include_debug:
            for name, view in views.items():
                slug = name.replace(" ", "_").replace("(", "").replace(")", "")
                p = render_debug(view, args.output / f"debug_{slug}.png")
                log.info("Wrote debug PNG       : %s", p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
