#!/usr/bin/env python3
"""V1 polygon-aware DXF placement — with bbox comparison.

Loads a V1 slab inventory + optional processed images, reads a
standardized DXF as the target geometry, and runs **two** packers
side-by-side:

    1. ``shelf_pack`` against the DXF bounding rectangle (bbox-only)
    2. ``polygon_pack`` against the irregular boundary minus holes

Outputs two PNG previews and one comparison JSON so the difference
between bbox-only smoke and true polygon-aware acceptance is obvious.
Neither packer does rotation, optimization, seam scoring, or offcut
reuse — V1 acceptance correctness only.

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
from placement_engine.inventory.polygon_pack import (  # noqa: E402
    PolygonPackResult,
    PolygonPlacement,
    polygon_pack,
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
    p.add_argument("--inventory", required=True, type=Path,
                   help="Path to clean_slabs.json.")
    p.add_argument("--dxf", required=True, type=Path,
                   help="Path to a standardized DXF target.")
    p.add_argument("--image-metadata", type=Path, default=None,
                   help="Optional image_metadata.json for processed images.")
    p.add_argument("--target-name", type=str, default=None,
                   help="Override the target name (defaults to DXF filename).")
    p.add_argument("--output", type=Path,
                   default=Path("outputs/dxf_polygon_placement"))
    p.add_argument("--no-preview", action="store_true",
                   help="Skip the matplotlib preview PNGs.")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Preview rendering — shared core for both packers
# ---------------------------------------------------------------------------


def _resolve_preview_image(
    image_path: Path | None,
    image_available: bool,
    processed_image_path: Path | None,
) -> tuple[Path | None, str]:
    if processed_image_path is not None and processed_image_path.exists():
        return processed_image_path, "processed"
    if image_available and image_path is not None:
        return image_path, "original"
    return None, "placeholder"


def _draw_placement(
    ax, slab_id: str,
    x: float, y: float, w: float, h: float,
    image_path: Path | None, image_available: bool,
    processed_image_path: Path | None,
    mpimg, mpatches,
) -> str:
    resolved, source = _resolve_preview_image(
        image_path, image_available, processed_image_path
    )
    image_drawn = False
    if resolved is not None:
        try:
            img = mpimg.imread(str(resolved))
            ax.imshow(
                img, extent=(x, x + w, y, y + h),
                aspect="auto", origin="upper", zorder=1,
            )
            image_drawn = True
        except Exception as exc:  # noqa: BLE001
            logging.getLogger(__name__).warning(
                "Could not load image %s for %s: %s", resolved, slab_id, exc,
            )
            source = "placeholder"
    if not image_drawn:
        source = "placeholder"
        ax.add_patch(mpatches.Rectangle(
            (x, y), w, h, facecolor="#dcdcdc", edgecolor="#888888",
            hatch="//", linewidth=0.8, zorder=1,
        ))
        ax.text(
            x + w * 0.5, y + h * 0.85, "[no image]",
            ha="center", va="center", color="#444444",
            fontsize=8, fontstyle="italic", zorder=3,
        )
    ax.add_patch(mpatches.Rectangle(
        (x, y), w, h, fill=False,
        edgecolor="white" if image_drawn else "#666666",
        linewidth=1.6, zorder=2,
    ))
    ax.text(
        x + w * 0.5, y + h * 0.5, slab_id,
        ha="center", va="center", fontsize=8, color="white",
        bbox=dict(boxstyle="round,pad=0.25", facecolor="black", alpha=0.55),
        zorder=3,
    )
    return source


def _draw_target_overlay(ax, geometry: TargetGeometry, mpatches, *,
                         show_bbox_guide: bool = True) -> None:
    """Draw the bbox dashed guide, boundary polygon, and hole hatches."""
    bx0, by0, bx1, by1 = geometry.bbox
    if show_bbox_guide:
        ax.add_patch(mpatches.Rectangle(
            (bx0, by0), bx1 - bx0, by1 - by0, fill=False,
            edgecolor="#888888", linestyle="--", linewidth=1.0, zorder=4,
        ))
    ax.add_patch(mpatches.Polygon(
        geometry.boundary, closed=True, fill=False,
        edgecolor="black", linewidth=3.0, zorder=6,
    ))
    for hole in geometry.holes:
        ax.add_patch(mpatches.Polygon(
            hole, closed=True, facecolor="#ffe6e6",
            edgecolor="#cc3333", hatch="xxx", linewidth=1.5,
            alpha=0.85, zorder=5,
        ))


def render_bbox_preview(
    geometry: TargetGeometry,
    result: ShelfPackResult,
    out_path: Path,
) -> dict[str, int]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.image as mpimg
    import matplotlib.patches as mpatches
    import matplotlib.pyplot as plt

    bx0, by0, bx1, by1 = geometry.bbox
    fig, ax = plt.subplots(figsize=(13, 9))
    source_tally = {"processed": 0, "original": 0, "placeholder": 0}
    for p in result.placements:
        src = _draw_placement(
            ax, p.slab_id, p.x, p.y, p.width_mm, p.height_mm,
            p.image_path, p.image_available, p.processed_image_path,
            mpimg, mpatches,
        )
        source_tally[src] = source_tally.get(src, 0) + 1
    _draw_target_overlay(ax, geometry, mpatches)
    margin = max(bx1 - bx0, by1 - by0) * 0.05
    ax.set_xlim(bx0 - margin, bx1 + margin)
    ax.set_ylim(by0 - margin, by1 + margin)
    ax.set_aspect("equal")
    ax.set_xlabel("x (mm)")
    ax.set_ylabel("y (mm)")
    ax.set_title(
        f"BBOX-ONLY smoke (shelf_pack) — {geometry.name}  "
        f"[slabs may overlap holes / overflow boundary]\n"
        f"{len(result.placements)} placed, {len(result.overflow)} overflow, "
        f"{result.coverage_percentage:.1f}% bbox coverage  "
        f"({result.placed_area_m2:.2f} / {result.target_area_m2:.2f} m² bbox)"
    )
    ax.grid(True, linestyle=":", color="#cccccc", linewidth=0.5)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=110)
    plt.close(fig)
    return source_tally


def render_polygon_preview(
    geometry: TargetGeometry,
    result: PolygonPackResult,
    out_path: Path,
) -> dict[str, int]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.image as mpimg
    import matplotlib.patches as mpatches
    import matplotlib.pyplot as plt

    bx0, by0, bx1, by1 = geometry.bbox
    fig, ax = plt.subplots(figsize=(13, 9))
    source_tally = {"processed": 0, "original": 0, "placeholder": 0}
    for p in result.placements:
        src = _draw_placement(
            ax, p.slab_id, p.x, p.y, p.width_mm, p.height_mm,
            p.image_path, p.image_available, p.processed_image_path,
            mpimg, mpatches,
        )
        source_tally[src] = source_tally.get(src, 0) + 1
    # Show rejected positions as a faint orange-dashed ghost rectangle
    # at the attempted (x, y), with a short reason badge. Skips when
    # attempted position is unknown.
    for r in result.rejected:
        if r.attempted_x is None or r.attempted_y is None:
            continue
        ax.add_patch(mpatches.Rectangle(
            (r.attempted_x, r.attempted_y), r.width_mm, r.height_mm,
            fill=False, edgecolor="#ff8800",
            linestyle=":", linewidth=1.2, zorder=2,
        ))
        ax.text(
            r.attempted_x + r.width_mm * 0.5,
            r.attempted_y + r.height_mm * 0.5,
            f"{r.slab_id}\n[{r.reason}]",
            ha="center", va="center", color="#cc4400",
            fontsize=7, fontstyle="italic",
            bbox=dict(boxstyle="round,pad=0.2",
                      facecolor="#fff4e6", edgecolor="#ff8800",
                      alpha=0.85),
            zorder=3,
        )
    _draw_target_overlay(ax, geometry, mpatches)
    margin = max(bx1 - bx0, by1 - by0) * 0.05
    ax.set_xlim(bx0 - margin, bx1 + margin)
    ax.set_ylim(by0 - margin, by1 + margin)
    ax.set_aspect("equal")
    ax.set_xlabel("x (mm)")
    ax.set_ylabel("y (mm)")
    ax.set_title(
        f"POLYGON-AWARE acceptance (polygon_pack) — {geometry.name}\n"
        f"{result.placed_count} placed, {result.rejected_count} rejected "
        f"(holes: {result.rejected_holes_count}, "
        f"outside: {result.rejected_outside_count}, "
        f"oversize: {result.rejected_oversize_count})  "
        f"|  real coverage {result.real_coverage_percentage:.1f}% "
        f"({result.placed_area_m2:.2f} / {result.usable_area_m2:.2f} m² usable)"
    )
    ax.grid(True, linestyle=":", color="#cccccc", linewidth=0.5)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=110)
    plt.close(fig)
    return source_tally


# ---------------------------------------------------------------------------
# JSON writer (single comparison file)
# ---------------------------------------------------------------------------


def _comparison_json(
    geometry: TargetGeometry,
    bbox_result: ShelfPackResult,
    polygon_result: PolygonPackResult,
    bbox_tally: dict[str, int],
    polygon_tally: dict[str, int],
) -> dict:
    return {
        "target": {
            "target_id": geometry.target_id,
            "name": geometry.name,
            "source_dxf_path": (
                str(geometry.source_dxf_path) if geometry.source_dxf_path else None
            ),
            "source_bbox": list(geometry.source_bbox) if geometry.source_bbox else None,
            "bbox": list(geometry.bbox),
            "boundary_vertex_count": len(geometry.boundary),
            "hole_count": len(geometry.holes),
            "boundary_area_m2": round(geometry.boundary_area_m2, 4),
            "holes_area_m2": round(geometry.holes_area_m2, 4),
            "usable_area_m2": round(geometry.usable_area_m2, 4),
            "bbox_area_m2": round(
                (geometry.bbox[2] - geometry.bbox[0])
                * (geometry.bbox[3] - geometry.bbox[1]) / 1_000_000.0,
                4,
            ),
        },
        "bbox_smoke": {
            "packer": "shelf_pack",
            "caveat": "may place slabs over holes / outside boundary",
            "placed_count": len(bbox_result.placements),
            "overflow_count": len(bbox_result.overflow),
            "placed_area_m2": round(bbox_result.placed_area_m2, 4),
            "bbox_coverage_percentage": round(bbox_result.coverage_percentage, 2),
            "preview_image_sources": bbox_tally,
            "placements": [
                {
                    "slab_id": p.slab_id, "x_mm": p.x, "y_mm": p.y,
                    "width_mm": p.width_mm, "height_mm": p.height_mm,
                }
                for p in bbox_result.placements
            ],
            "overflow_ids": [s.slab_id for s in bbox_result.overflow],
        },
        "polygon_aware": {
            "packer": "polygon_pack",
            "placed_count": polygon_result.placed_count,
            "rejected_count": polygon_result.rejected_count,
            "rejected_outside_count": polygon_result.rejected_outside_count,
            "rejected_holes_count": polygon_result.rejected_holes_count,
            "rejected_oversize_count": polygon_result.rejected_oversize_count,
            "placed_area_m2": round(polygon_result.placed_area_m2, 4),
            "usable_area_m2": round(polygon_result.usable_area_m2, 4),
            "real_coverage_percentage": round(
                polygon_result.real_coverage_percentage, 2
            ),
            "preview_image_sources": polygon_tally,
            "placements": [
                {
                    "slab_id": p.slab_id, "x_mm": p.x, "y_mm": p.y,
                    "width_mm": p.width_mm, "height_mm": p.height_mm,
                }
                for p in polygon_result.placements
            ],
            "rejected": [
                {
                    "slab_id": r.slab_id, "reason": r.reason,
                    "width_mm": r.width_mm, "height_mm": r.height_mm,
                    "attempted_x_mm": r.attempted_x,
                    "attempted_y_mm": r.attempted_y,
                }
                for r in polygon_result.rejected
            ],
        },
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
    log = logging.getLogger("run_dxf_polygon_placement")

    geometry = load_target_geometry_from_dxf(args.dxf, name=args.target_name)
    bbox_target = geometry.as_bounding_target_area()
    inventory = load_inventory(args.inventory)
    issues = validate_inventory(inventory)
    if args.image_metadata is not None:
        attach_processed_images(inventory, args.image_metadata)

    bbox_result = shelf_pack(inventory.slabs, bbox_target)
    polygon_result = polygon_pack(inventory.slabs, geometry)

    # --- terminal summary ---
    print()
    print("=" * 80)
    print(f"DXF target  : {geometry.name}")
    print(f"  source              : {geometry.source_dxf_path}")
    print(f"  bbox (mm)           : {geometry.bbox}")
    print(f"  boundary area       : {geometry.boundary_area_m2:.2f} m²")
    print(f"  holes area          : {geometry.holes_area_m2:.2f} m²")
    print(f"  usable area         : {geometry.usable_area_m2:.2f} m²")
    print(f"Inventory : {len(inventory.slabs)} slabs"
          + (f" ({len(issues)} validation issues)" if issues else ""))
    print()
    print("─" * 80)
    print("BBOX-ONLY smoke (shelf_pack):")
    print(f"  placed              : {len(bbox_result.placements)}")
    print(f"  overflow            : {len(bbox_result.overflow)}")
    print(f"  bbox coverage       : {bbox_result.coverage_percentage:.1f}%  "
          f"({bbox_result.placed_area_m2:.2f} / "
          f"{bbox_result.target_area_m2:.2f} m² bbox)")
    print("─" * 80)
    print("POLYGON-AWARE acceptance (polygon_pack):")
    print(f"  placed              : {polygon_result.placed_count}")
    print(f"  rejected (total)    : {polygon_result.rejected_count}")
    print(f"    intersects_hole   : {polygon_result.rejected_holes_count}")
    print(f"    outside_boundary  : {polygon_result.rejected_outside_count}")
    print(f"    oversize          : {polygon_result.rejected_oversize_count}")
    print(f"  real coverage       : {polygon_result.real_coverage_percentage:.1f}%  "
          f"({polygon_result.placed_area_m2:.2f} / "
          f"{polygon_result.usable_area_m2:.2f} m² usable)")
    for r in polygon_result.rejected:
        pos = (
            f"@({r.attempted_x:.0f},{r.attempted_y:.0f})"
            if r.attempted_x is not None else ""
        )
        print(f"    ! {r.slab_id:30s} {r.reason:20s} {pos}")
    print("=" * 80)

    # --- artefacts ---
    args.output.mkdir(parents=True, exist_ok=True)
    bbox_tally = {"processed": 0, "original": 0, "placeholder": 0}
    polygon_tally = {"processed": 0, "original": 0, "placeholder": 0}
    if not args.no_preview:
        bbox_png = args.output / "preview_bbox.png"
        bbox_tally = render_bbox_preview(geometry, bbox_result, bbox_png)
        log.info("Wrote bbox-only preview    : %s", bbox_png)
        polygon_png = args.output / "preview_polygon.png"
        polygon_tally = render_polygon_preview(geometry, polygon_result, polygon_png)
        log.info("Wrote polygon-aware preview: %s", polygon_png)

    json_path = args.output / "placement_comparison.json"
    json_path.write_text(
        json.dumps(_comparison_json(
            geometry, bbox_result, polygon_result, bbox_tally, polygon_tally
        ), indent=2),
        encoding="utf-8",
    )
    log.info("Wrote comparison JSON      : %s", json_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
