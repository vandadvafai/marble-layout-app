#!/usr/bin/env python3
"""V1 rectangular-target placement runner.

Loads a V1 slab inventory + optional processed images, runs the
shelf packer against a user-specified rectangular ``TargetArea``, and
writes designer-facing previews + a canonical placement JSON.

Example:

    python3 scripts/run_smoke_placement.py \\
        --inventory      outputs/slab_ingestion_test/clean_slabs.json \\
        --image-metadata outputs/image_intake/image_metadata.json \\
        --target-width-mm  5000 \\
        --target-height-mm 3000 \\
        --target-name      "Test Room" \\
        --output           outputs/smoke_placement

Default output (designer-facing):

    <output>/placement.json          — canonical PlacementView, JSON
    <output>/preview_geometric.png   — CAD-style plan view
    <output>/preview_textured.png    — photoreal mockup

Opt-in (engineer-only):

    <output>/preview_debug.png       — only if --include-debug
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
from placement_engine.target_area import (  # noqa: E402
    TargetArea,
    target_area_warnings,
)


DEMO_TARGET_WIDTH_MM: float = 4000.0
DEMO_TARGET_HEIGHT_MM: float = 3000.0
DEMO_TARGET_NAME: str = "demo default (4 m × 3 m)"
DEMO_TARGET_ID: str = "demo_target"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--inventory", required=True, type=Path,
                   help="Path to clean_slabs.json.")
    p.add_argument("--target-width-mm", type=float, default=None,
                   help="Target width in mm (with --target-height-mm).")
    p.add_argument("--target-height-mm", type=float, default=None,
                   help="Target height in mm.")
    p.add_argument("--target-name", type=str, default=None,
                   help="Human-readable target name.")
    p.add_argument("--target-id", type=str, default=None,
                   help="Stable identifier.")
    p.add_argument("--target-required-area-m2", type=float, default=None,
                   help="Designer-stated usable area in m² (cross-check).")
    p.add_argument("--image-metadata", type=Path, default=None,
                   help="Optional path to image_metadata.json.")
    p.add_argument("--output", type=Path,
                   default=Path("outputs/smoke_placement"))
    p.add_argument("--include-debug", action="store_true",
                   help="Also write preview_debug.png (engineer-only).")
    p.add_argument("--show-dimensions", action="store_true",
                   help="Annotate slab dimensions in the geometric preview.")
    p.add_argument("--no-preview", action="store_true",
                   help="Skip all PNG previews (write JSON only).")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args(argv)


def _resolve_target(args: argparse.Namespace) -> tuple[TargetArea, bool]:
    has_w = args.target_width_mm is not None
    has_h = args.target_height_mm is not None
    if has_w ^ has_h:
        raise SystemExit(
            "error: --target-width-mm and --target-height-mm must be "
            "provided together (or both omitted to use the demo defaults)."
        )
    is_demo = not has_w and not has_h
    width = args.target_width_mm if has_w else DEMO_TARGET_WIDTH_MM
    height = args.target_height_mm if has_h else DEMO_TARGET_HEIGHT_MM
    name = args.target_name or (
        DEMO_TARGET_NAME if is_demo else f"{width:.0f} × {height:.0f} mm"
    )
    target_id = args.target_id or (
        DEMO_TARGET_ID if is_demo else _slug(name)
    )
    target = TargetArea(
        target_id=target_id, name=name,
        width_mm=width, height_mm=height,
        required_area_m2=args.target_required_area_m2,
    )
    return target, is_demo


def _slug(value: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in value).strip("_").lower() or "target"


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("run_smoke_placement")

    target, is_demo = _resolve_target(args)
    log.info("Inventory   : %s", args.inventory)
    log.info(
        "Target area : %s (%.0f × %.0f mm)%s",
        target.name, target.width_mm, target.height_mm,
        "  [DEMO DEFAULT]" if is_demo else "",
    )

    inventory = load_inventory(args.inventory)
    issues = validate_inventory(inventory)
    target_warnings = target_area_warnings(target)

    attached = 0
    if args.image_metadata is not None:
        attached = attach_processed_images(inventory, args.image_metadata)
        log.info(
            "Attached processed images: %d / %d slabs",
            attached, len(inventory.slabs),
        )

    result = shelf_pack(inventory.slabs, target)
    view = view_from_shelf_pack_result(result, metadata={
        "is_demo_default": is_demo,
        "target_warnings": target_warnings,
        "validation_issues": [
            {"slab_id": i.slab_id, "code": i.code, "message": i.message}
            for i in issues
        ],
        "inventory_source": str(args.inventory),
    })

    # --- terminal summary ---
    print()
    print("=" * 70)
    print(f"Target area  : {target.name}"
          + ("  [demo default]" if is_demo else ""))
    print(f"  {target.width_mm:.0f} × {target.height_mm:.0f} mm  "
          f"({target.calculated_area_m2:.2f} m²)")
    if target_warnings:
        print(f"  target warnings : {', '.join(target_warnings)}")
    print(f"Inventory    : {len(inventory.slabs)} slabs")
    if args.image_metadata is not None:
        print(f"  processed images attached : {attached} / {len(inventory.slabs)}")
    print(f"Placed       : {view.placed_count}")
    print(f"Rejected     : {view.rejected_count}")
    print(f"Seams        : {len(view.seams)} "
          f"(total {view.total_seam_length_mm:.0f} mm)")
    print(f"Coverage     : {view.coverage_percentage:.1f}%  "
          f"({view.placed_area_m2:.2f} / {target.calculated_area_m2:.2f} m²)")
    print("=" * 70)

    # --- artefacts ---
    args.output.mkdir(parents=True, exist_ok=True)
    json_path = write_placement_json(view, args.output / "placement.json")
    log.info("Wrote JSON           : %s", json_path)

    if not args.no_preview:
        geo_path = render_geometric(
            view, args.output / "preview_geometric.png",
            show_dimensions=args.show_dimensions,
        )
        log.info("Wrote geometric PNG  : %s", geo_path)
        tex_path = render_textured(view, args.output / "preview_textured.png")
        log.info("Wrote textured PNG   : %s", tex_path)
        if args.include_debug:
            dbg_path = render_debug(view, args.output / "preview_debug.png")
            log.info("Wrote debug PNG      : %s", dbg_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
