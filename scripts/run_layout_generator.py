#!/usr/bin/env python3
"""V1 layout generator — DXF + inventory → geometric slab-sized layout.

Tiles the usable floor polygon (boundary − holes) of a standardized
DXF with equal nominal rectangles. The **default tile size is the
median slab dimension** of the inventory at ``--inventory`` — that's
the slab size the design batch will actually supply, so the geometric
layout reflects the real stock. Explicit ``--tile-width-mm`` /
``--tile-height-mm`` are kept as a debug/test override.

Cells that cross the boundary or a hole are kept as **edge pieces**
(clipped to the usable polygon). Slab inventory **identity** is NOT
assigned at this stage — only its size statistics are read.

Example (inventory-derived, default):

    python3 scripts/run_layout_generator.py \\
        --inventory      outputs/slab_ingestion_test/clean_slabs.json \\
        --dxf            examples/cad_inputs/demo/demo_l_shape_floor.dxf \\
        --output         outputs/layouts/demo_l_shape_floor

Example (explicit override for debug / testing):

    python3 scripts/run_layout_generator.py \\
        --dxf            examples/cad_inputs/demo/demo_l_shape_floor.dxf \\
        --tile-width-mm  1200 \\
        --tile-height-mm  600 \\
        --output         outputs/layouts/demo_l_shape_floor_debug

Output:

    <output>/layout.json          — canonical LayoutResult JSON
    <output>/layout_geometric.png — clean CAD-style preview
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from placement_engine.inventory import load_inventory  # noqa: E402
from placement_engine.layout import (  # noqa: E402
    generate_tile_layout,
    generate_tile_layout_from_inventory,
    render_layout_geometric,
    write_layout_json,
)
from placement_engine.target_area import load_target_geometry_from_dxf  # noqa: E402


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--dxf", required=True, type=Path,
                   help="Path to a standardized DXF target.")
    p.add_argument("--inventory", type=Path, default=None,
                   help=(
                       "Path to clean_slabs.json. The median slab "
                       "width × height is used as the layout tile "
                       "size unless --tile-width-mm AND --tile-height-mm "
                       "are also provided (explicit override)."
                   ))
    p.add_argument("--tile-width-mm", type=float, default=None,
                   help=(
                       "Explicit override for tile width (debug/testing). "
                       "Must be paired with --tile-height-mm."
                   ))
    p.add_argument("--tile-height-mm", type=float, default=None,
                   help="Explicit override for tile height. See --tile-width-mm.")
    p.add_argument("--target-name", type=str, default=None,
                   help="Override the target name (defaults to DXF filename).")
    p.add_argument("--origin-x-mm", type=float, default=None,
                   help="Grid origin x (defaults to boundary bbox xmin).")
    p.add_argument("--origin-y-mm", type=float, default=None,
                   help="Grid origin y (defaults to boundary bbox ymin).")
    p.add_argument("--output", type=Path, default=Path("outputs/layouts"),
                   help="Output directory.")
    p.add_argument("--show-labels", action="store_true",
                   help="Annotate pieces with small corner numerals (1..N).")
    p.add_argument("--no-preview", action="store_true",
                   help="Skip the PNG preview (write JSON only).")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args(argv)


def _resolve_origin(args: argparse.Namespace, bbox) -> tuple[float, float] | None:
    if args.origin_x_mm is None and args.origin_y_mm is None:
        return None
    bx0, by0, _, _ = bbox
    ox = args.origin_x_mm if args.origin_x_mm is not None else bx0
    oy = args.origin_y_mm if args.origin_y_mm is not None else by0
    return (ox, oy)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("run_layout_generator")

    # ---- input validation ------------------------------------------------
    has_explicit_w = args.tile_width_mm is not None
    has_explicit_h = args.tile_height_mm is not None
    if has_explicit_w ^ has_explicit_h:
        raise SystemExit(
            "error: --tile-width-mm and --tile-height-mm must be provided "
            "together (or neither, to derive from --inventory)."
        )
    use_explicit = has_explicit_w and has_explicit_h
    if not use_explicit and args.inventory is None:
        raise SystemExit(
            "error: provide --inventory (default: inventory-median tile size), "
            "or BOTH --tile-width-mm AND --tile-height-mm (explicit override)."
        )

    geometry = load_target_geometry_from_dxf(args.dxf, name=args.target_name)
    origin = _resolve_origin(args, geometry.bbox)

    # ---- generate layout -------------------------------------------------
    if use_explicit:
        layout = generate_tile_layout(
            geometry,
            tile_width_mm=args.tile_width_mm,
            tile_height_mm=args.tile_height_mm,
            origin=origin,
        )
        log.info(
            "Layout basis : explicit override (%.0f × %.0f mm)",
            args.tile_width_mm, args.tile_height_mm,
        )
    else:
        inventory = load_inventory(args.inventory)
        if not inventory.slabs:
            raise SystemExit(
                f"error: inventory at {args.inventory} has zero usable slabs."
            )
        layout = generate_tile_layout_from_inventory(
            geometry,
            inventory.slabs,
            source_inventory_path=str(args.inventory),
            origin=origin,
        )
        log.info(
            "Layout basis : inventory_median  (from %d slabs in %s)",
            len(inventory.slabs), args.inventory,
        )

    # ---- terminal summary ------------------------------------------------
    print()
    print("=" * 78)
    print(f"DXF target  : {geometry.name}")
    print(f"  bbox        : {geometry.bbox}")
    print(f"  usable area : {geometry.usable_area_m2:.4f} m²")
    print(f"Tile size   : {layout.tile_width_mm:.0f} × {layout.tile_height_mm:.0f} mm "
          f"(nominal area "
          f"{layout.tile_width_mm * layout.tile_height_mm / 1_000_000:.4f} m²)")
    print(f"Origin      : {layout.origin}")
    print(f"Basis       : {layout.layout_basis}"
          + (f"  (source: {layout.source_inventory_path})"
             if layout.source_inventory_path else ""))
    if layout.inventory_dimension_summary is not None:
        s = layout.inventory_dimension_summary
        print(f"Inventory   : {s.slab_count} slabs")
        print(f"  width   median={s.median_width_mm:.0f}  mean={s.mean_width_mm:.0f}  "
              f"range=[{s.min_width_mm:.0f}, {s.max_width_mm:.0f}]"
              + (f"  mode={s.mode_width_mm:.0f}×{s.mode_width_count}"
                 if s.mode_width_mm is not None else ""))
        print(f"  height  median={s.median_height_mm:.0f}  mean={s.mean_height_mm:.0f}  "
              f"range=[{s.min_height_mm:.0f}, {s.max_height_mm:.0f}]"
              + (f"  mode={s.mode_height_mm:.0f}×{s.mode_height_count}"
                 if s.mode_height_mm is not None else ""))
    print()
    print(f"Pieces             : {len(layout.pieces)}")
    print(f"  full tiles       : {layout.full_tile_count}")
    print(f"  edge pieces      : {layout.edge_piece_count}")
    print(f"  slivers          : {layout.sliver_count}")
    intersecting_holes = sum(1 for p in layout.pieces if p.intersects_hole)
    print(f"  intersect holes  : {intersecting_holes}")
    print()
    print(f"Total piece area   : {layout.total_actual_area_m2:.4f} m²")
    print(f"Usable floor area  : {geometry.usable_area_m2:.4f} m²")
    print(f"Coverage           : {layout.coverage_percentage:.2f}%")
    print("=" * 78)

    # ---- artefacts -------------------------------------------------------
    args.output.mkdir(parents=True, exist_ok=True)
    json_path = write_layout_json(layout, args.output / "layout.json")
    log.info("Wrote JSON           : %s", json_path)

    if not args.no_preview:
        png_path = render_layout_geometric(
            layout, args.output / "layout_geometric.png",
            show_labels=args.show_labels,
        )
        log.info("Wrote geometric PNG  : %s", png_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
