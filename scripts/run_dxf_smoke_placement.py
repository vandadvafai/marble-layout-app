#!/usr/bin/env python3
"""V1 DXF-target smoke placement (bbox-only).

Loads a real V1 slab inventory, optionally attaches processed
green-box images, reads a standardized DXF as the target geometry,
runs the existing V1 shelf packer against the DXF's **bounding
rectangle** (not the irregular boundary), and renders a preview that
overlays the real boundary + holes on top of the bbox placement.

This is intentionally NOT a real placement strategy. Slabs may visually
fall over holes or outside the irregular boundary — the preview labels
that explicitly. Polygon-clipped placement is a later milestone.

Example:

    python3 scripts/run_dxf_smoke_placement.py \\
        --inventory      outputs/slab_ingestion_test/clean_slabs.json \\
        --image-metadata outputs/image_intake/image_metadata.json \\
        --dxf            examples/cad_inputs/demo/demo_l_shape_floor.dxf \\
        --output         outputs/dxf_smoke_placement
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
from placement_engine.inventory.shelf_pack import (  # noqa: E402
    Placement,
    ShelfPackResult,
    shelf_pack,
)
from placement_engine.target_area import (  # noqa: E402
    TargetGeometry,
    load_target_geometry_from_dxf,
)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--inventory",
        required=True,
        type=Path,
        help="Path to clean_slabs.json (output of prepare_slab_data.py).",
    )
    p.add_argument(
        "--dxf",
        required=True,
        type=Path,
        help="Path to a standardized DXF target (cad_inputs/* layout).",
    )
    p.add_argument(
        "--image-metadata",
        type=Path,
        default=None,
        help=(
            "Optional path to image_metadata.json — when supplied, "
            "processed (green-box cropped) images are used in the "
            "preview with fallback to the original photo."
        ),
    )
    p.add_argument(
        "--target-name",
        type=str,
        default=None,
        help="Override the target name. Defaults to the DXF filename.",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/dxf_smoke_placement"),
        help="Output directory (default: outputs/dxf_smoke_placement).",
    )
    p.add_argument(
        "--no-preview",
        action="store_true",
        help="Skip the matplotlib preview PNG.",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Preview rendering
# ---------------------------------------------------------------------------


def render_preview(
    geometry: TargetGeometry,
    result: ShelfPackResult,
    out_path: Path,
) -> dict[str, int]:
    """Render the bbox-smoke preview with the real DXF outline overlaid."""
    import matplotlib

    matplotlib.use("Agg")  # headless
    import matplotlib.image as mpimg
    import matplotlib.patches as mpatches
    import matplotlib.pyplot as plt

    bx0, by0, bx1, by1 = geometry.bbox
    width = bx1 - bx0
    height = by1 - by0

    fig, ax = plt.subplots(figsize=(13, 9))

    # 1. Bbox guide (thin dashed) — what the V1 packer actually used.
    ax.add_patch(
        mpatches.Rectangle(
            (0, 0), width, height,
            fill=False, edgecolor="#888888",
            linestyle="--", linewidth=1.0, zorder=4,
        )
    )

    # 2. Placed slabs (under the boundary so the outline reads on top).
    source_tally: dict[str, int] = {"processed": 0, "original": 0, "placeholder": 0}
    for p in result.placements:
        source = _render_one_placement(ax, p, mpimg, mpatches)
        source_tally[source] = source_tally.get(source, 0) + 1

    # 3. DXF boundary polygon (thick black) — the real floor outline.
    ax.add_patch(
        mpatches.Polygon(
            geometry.boundary,
            closed=True, fill=False,
            edgecolor="black", linewidth=3.0, zorder=6,
        )
    )

    # 4. Holes — hatched red "no-go" zones.
    for hole in geometry.holes:
        ax.add_patch(
            mpatches.Polygon(
                hole,
                closed=True, facecolor="#ffe6e6",
                edgecolor="#cc3333", hatch="xxx",
                linewidth=1.5, alpha=0.85, zorder=5,
            )
        )

    # Frame with a margin.
    margin = max(width, height) * 0.05
    ax.set_xlim(-margin, width + margin)
    ax.set_ylim(-margin, height + margin)
    ax.set_aspect("equal")
    ax.set_xlabel("x (mm)")
    ax.set_ylabel("y (mm)")
    ax.set_title(
        f"V1 DXF bbox-smoke — {geometry.name}\n"
        f"bbox {width:.0f} × {height:.0f} mm "
        f"(usable {geometry.usable_area_m2:.2f} m² / bbox {result.target_area_m2:.2f} m²)  "
        f"— {len(result.placements)} placed, "
        f"{len(result.overflow)} overflow, "
        f"{result.coverage_percentage:.1f}% bbox coverage  "
        "[bbox-only smoke — NOT a real strategy; slabs may overlap holes/outside]"
    )
    ax.grid(True, linestyle=":", color="#cccccc", linewidth=0.5)

    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=110)
    plt.close(fig)
    return source_tally


def _resolve_preview_image(p: Placement) -> tuple[Path | None, str]:
    if p.processed_image_path is not None and p.processed_image_path.exists():
        return p.processed_image_path, "processed"
    if p.image_available and p.image_path is not None:
        return p.image_path, "original"
    return None, "placeholder"


def _render_one_placement(ax, p: Placement, mpimg, mpatches) -> str:
    image_path, source = _resolve_preview_image(p)
    image_drawn = False
    if image_path is not None:
        try:
            img = mpimg.imread(str(image_path))
            ax.imshow(
                img,
                extent=(p.x, p.x + p.width_mm, p.y, p.y + p.height_mm),
                aspect="auto",
                origin="upper",
                zorder=1,
            )
            image_drawn = True
        except Exception as exc:  # noqa: BLE001
            logging.getLogger(__name__).warning(
                "Could not load image %s for %s: %s",
                image_path, p.slab_id, exc,
            )
            source = "placeholder"
    if not image_drawn:
        source = "placeholder"
        ax.add_patch(
            mpatches.Rectangle(
                (p.x, p.y), p.width_mm, p.height_mm,
                facecolor="#dcdcdc", edgecolor="#888888",
                hatch="//", linewidth=0.8, zorder=1,
            )
        )
        ax.text(
            p.x + p.width_mm * 0.5, p.y + p.height_mm * 0.85,
            "[no image]", ha="center", va="center",
            color="#444444", fontsize=8, fontstyle="italic", zorder=3,
        )
    ax.add_patch(
        mpatches.Rectangle(
            (p.x, p.y), p.width_mm, p.height_mm,
            fill=False, edgecolor="white" if image_drawn else "#666666",
            linewidth=1.6, zorder=2,
        )
    )
    ax.text(
        p.x + p.width_mm * 0.5, p.y + p.height_mm * 0.5, p.slab_id,
        ha="center", va="center", fontsize=8, color="white",
        bbox=dict(boxstyle="round,pad=0.25", facecolor="black", alpha=0.55),
        zorder=3,
    )
    return source


# ---------------------------------------------------------------------------
# Output writer
# ---------------------------------------------------------------------------


def _placements_json(
    geometry: TargetGeometry,
    result: ShelfPackResult,
    source_tally: dict[str, int],
) -> dict:
    return {
        "smoke_mode": "bbox_only",  # flag that this is NOT a real strategy
        "target": {
            "target_id": geometry.target_id,
            "name": geometry.name,
            "source_dxf_path": (
                str(geometry.source_dxf_path) if geometry.source_dxf_path else None
            ),
            "source_bbox": list(geometry.source_bbox) if geometry.source_bbox else None,
            "bbox": list(geometry.bbox),
            "width_mm": geometry.width_mm,
            "height_mm": geometry.height_mm,
            "boundary_area_m2": round(geometry.boundary_area_m2, 4),
            "holes_area_m2": round(geometry.holes_area_m2, 4),
            "usable_area_m2": round(geometry.usable_area_m2, 4),
            "boundary_vertex_count": len(geometry.boundary),
            "hole_count": len(geometry.holes),
        },
        "bbox_coverage_percentage": round(result.coverage_percentage, 2),
        "placed_area_m2": round(result.placed_area_m2, 4),
        "uncovered_bbox_area_m2": round(result.uncovered_area_m2, 4),
        "preview_image_sources": dict(source_tally),
        "placements": [
            {
                "slab_id": p.slab_id,
                "x_mm": p.x,
                "y_mm": p.y,
                "width_mm": p.width_mm,
                "height_mm": p.height_mm,
                "processed_image_path": (
                    str(p.processed_image_path) if p.processed_image_path else None
                ),
                "image_path": str(p.image_path) if p.image_path else None,
                "image_available": p.image_available,
            }
            for p in result.placements
        ],
        "overflow": [
            {"slab_id": s.slab_id, "width_mm": s.width_mm, "height_mm": s.height_mm}
            for s in result.overflow
        ],
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("run_dxf_smoke_placement")

    log.info("Inventory : %s", args.inventory)
    log.info("DXF       : %s", args.dxf)

    geometry = load_target_geometry_from_dxf(args.dxf, name=args.target_name)
    target = geometry.as_bounding_target_area()

    inventory = load_inventory(args.inventory)
    validation_issues = validate_inventory(inventory)

    attached = 0
    if args.image_metadata is not None:
        attached = attach_processed_images(inventory, args.image_metadata)
        log.info(
            "Attached processed images: %d / %d slabs from %s",
            attached, len(inventory.slabs), args.image_metadata,
        )

    result = shelf_pack(inventory.slabs, target)

    # --- terminal summary -------------------------------------------------
    print()
    print("=" * 78)
    print(f"DXF target  : {geometry.name}")
    print(f"  source              : {geometry.source_dxf_path}")
    print(f"  source bbox (mm)    : {geometry.source_bbox}")
    print(f"  normalized bbox (mm): {geometry.bbox}")
    print(f"  boundary vertices   : {len(geometry.boundary)}")
    print(f"  holes               : {len(geometry.holes)}")
    print(f"  boundary area       : {geometry.boundary_area_m2:.2f} m²")
    print(f"  holes area          : {geometry.holes_area_m2:.2f} m²")
    print(f"  usable area         : {geometry.usable_area_m2:.2f} m²  "
          f"(boundary − holes)")
    print(f"  bbox area           : {target.calculated_area_m2:.2f} m²  "
          f"(what the V1 packer actually fills)")
    print()
    print(f"Inventory : {args.inventory}")
    print(f"  slabs loaded        : {len(inventory.slabs)}")
    print(f"  skipped rows        : {len(inventory.skipped_records)}")
    if args.image_metadata is not None:
        print(f"  processed images    : {attached} / {len(inventory.slabs)}")
    if validation_issues:
        print(f"  validation issues   : {len(validation_issues)}")
        for iss in validation_issues:
            print(f"    ! {iss.code:22s} {iss.slab_id}  — {iss.message}")
    else:
        print("  validation issues   : none")
    print()
    print(f"Placed (into bbox) : {len(result.placements)}")
    for p in result.placements:
        print(
            f"  {p.slab_id:30s}  pos=({p.x:>6.0f},{p.y:>6.0f})  "
            f"size={p.width_mm:>5.0f}x{p.height_mm:<5.0f} mm"
        )
    print(f"Overflow           : {len(result.overflow)}")
    for s in result.overflow:
        print(f"  {s.slab_id:30s}  size={s.width_mm:.0f}x{s.height_mm:.0f} mm")
    print()
    print(f"Bbox coverage      : {result.coverage_percentage:.1f}%  "
          f"({result.placed_area_m2:.2f} / {result.target_area_m2:.2f} m²)")
    print()
    print("⚠  This is bbox-only smoke placement.")
    print("   Slabs may visually fall over holes or outside the irregular boundary.")
    print("   Polygon-aware placement is a later milestone.")
    print("=" * 78)

    args.output.mkdir(parents=True, exist_ok=True)
    source_tally: dict[str, int] = {"processed": 0, "original": 0, "placeholder": 0}
    if not args.no_preview:
        preview_path = args.output / "smoke_preview.png"
        source_tally = render_preview(geometry, result, preview_path)
        log.info("Wrote preview PNG    : %s", preview_path)
        print()
        print(f"Preview image sources (of {len(result.placements)} placed slabs):")
        print(f"  processed crop   : {source_tally.get('processed', 0)}")
        print(f"  original photo   : {source_tally.get('original', 0)}")
        print(f"  placeholder      : {source_tally.get('placeholder', 0)}")

    json_path = args.output / "smoke_placements.json"
    json_path.write_text(
        json.dumps(_placements_json(geometry, result, source_tally), indent=2),
        encoding="utf-8",
    )
    log.info("Wrote placements JSON: %s", json_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
