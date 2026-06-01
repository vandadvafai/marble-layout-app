#!/usr/bin/env python3
"""V1 geometry smoke test: load clean_slabs.json, sequentially pack, preview.

This is NOT a real placement strategy — it just walks the slabs in
``clean_slabs.json`` left-to-right, row by row, into a rectangular
project area. The point is to validate that the inventory pipeline
hands the engine the right shapes and that real slab images are
reachable for visualization.

Example:

    python3 scripts/run_smoke_placement.py \\
        --clean-slabs outputs/slab_ingestion_test/clean_slabs.json \\
        --project-width-mm 4000 \\
        --project-height-mm 3000 \\
        --output outputs/smoke_placement

Output:

    <output>/smoke_preview.png       — labelled visual preview
    <output>/smoke_placements.json   — placements + overflow + metrics
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path

# Allow `python scripts/...` from the repo root without installing.
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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--clean-slabs",
        required=True,
        type=Path,
        help="Path to clean_slabs.json (output of prepare_slab_data.py).",
    )
    p.add_argument(
        "--project-width-mm",
        type=float,
        default=4000.0,
        help="Project area width in mm (default: 4000).",
    )
    p.add_argument(
        "--project-height-mm",
        type=float,
        default=3000.0,
        help="Project area height in mm (default: 3000).",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/smoke_placement"),
        help="Output directory (default: outputs/smoke_placement).",
    )
    p.add_argument(
        "--image-metadata",
        type=Path,
        default=None,
        help=(
            "Optional path to image_metadata.json produced by "
            "scripts/process_slab_images.py. When supplied, processed "
            "(green-box cropped) images are used in the preview, with "
            "fallback to the original photo."
        ),
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
    result: ShelfPackResult,
    out_path: Path,
) -> dict[str, int]:
    """Render a labelled PNG of the smoke placement.

    Each placed slab is filled with its real photo if `image_available`
    is true, otherwise a clearly-marked grey hatched placeholder. The
    project boundary is drawn as a thick black rectangle.
    """
    import matplotlib

    matplotlib.use("Agg")  # headless
    import matplotlib.image as mpimg
    import matplotlib.patches as mpatches
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(11, 8))

    # Project boundary
    ax.add_patch(
        mpatches.Rectangle(
            (0, 0),
            result.project_width_mm,
            result.project_height_mm,
            fill=False,
            edgecolor="black",
            linewidth=2.5,
            zorder=4,
        )
    )

    source_tally: dict[str, int] = {"processed": 0, "original": 0, "placeholder": 0}
    for p in result.placements:
        source = _render_one_placement(ax, p, mpimg, mpatches)
        source_tally[source] = source_tally.get(source, 0) + 1

    # Frame the figure with a margin proportional to project size.
    margin = max(result.project_width_mm, result.project_height_mm) * 0.05
    ax.set_xlim(-margin, result.project_width_mm + margin)
    ax.set_ylim(-margin, result.project_height_mm + margin)
    ax.set_aspect("equal")
    ax.set_xlabel("x (mm)")
    ax.set_ylabel("y (mm)")
    coverage_pct = (
        100.0 * result.placed_area_m2 / result.project_area_m2
        if result.project_area_m2 > 0
        else 0.0
    )
    ax.set_title(
        f"V1 smoke placement — {len(result.placements)} placed, "
        f"{len(result.overflow)} overflow, "
        f"coverage {coverage_pct:.1f}% "
        f"({result.placed_area_m2:.2f} / {result.project_area_m2:.2f} m²)"
    )
    ax.grid(True, linestyle=":", color="#cccccc", linewidth=0.5)

    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return source_tally


def _resolve_preview_image(p: Placement) -> tuple[Path | None, str]:
    """Pick which image (if any) should fill this placement's rectangle.

    Returns ``(path, source)`` where ``source`` is one of:
        "processed"   — a green-box cropped image was attached and exists
        "original"    — fell back to the raw ingestion photo
        "placeholder" — no usable image on disk
    """
    if p.processed_image_path is not None and p.processed_image_path.exists():
        return p.processed_image_path, "processed"
    if p.image_available and p.image_path is not None:
        return p.image_path, "original"
    return None, "placeholder"


def _render_one_placement(ax, p: Placement, mpimg, mpatches) -> str:
    """Draw a single placed slab: photo fill or labelled placeholder.

    Returns the source label used ("processed", "original", or
    "placeholder") so the caller can tally per-source usage.
    """
    image_path, source = _resolve_preview_image(p)
    image_drawn = False
    if image_path is not None:
        try:
            img = mpimg.imread(str(image_path))
            # extent = (left, right, bottom, top); origin='upper' keeps
            # the photo right-side-up.
            ax.imshow(
                img,
                extent=(p.x, p.x + p.width_mm, p.y, p.y + p.height_mm),
                aspect="auto",
                origin="upper",
                zorder=1,
            )
            image_drawn = True
        except Exception as exc:  # noqa: BLE001 — log and continue
            logging.getLogger(__name__).warning(
                "Could not load image %s for %s: %s",
                image_path, p.slab_id, exc,
            )
            source = "placeholder"
    if not image_drawn:
        source = "placeholder"
        # Clearly-marked placeholder: grey, hatched, with a "[no image]" tag.
        ax.add_patch(
            mpatches.Rectangle(
                (p.x, p.y),
                p.width_mm,
                p.height_mm,
                facecolor="#dcdcdc",
                edgecolor="#888888",
                hatch="//",
                linewidth=0.8,
                zorder=1,
            )
        )
        ax.text(
            p.x + p.width_mm * 0.5,
            p.y + p.height_mm * 0.85,
            "[no image]",
            ha="center", va="center",
            color="#444444", fontsize=8, fontstyle="italic",
            zorder=3,
        )

    # Outline over the image so edges are visible regardless of photo content.
    ax.add_patch(
        mpatches.Rectangle(
            (p.x, p.y),
            p.width_mm,
            p.height_mm,
            fill=False,
            edgecolor="white" if image_drawn else "#666666",
            linewidth=1.6,
            zorder=2,
        )
    )
    # Slab label in a contrasting pill.
    ax.text(
        p.x + p.width_mm * 0.5,
        p.y + p.height_mm * 0.5,
        p.slab_id,
        ha="center", va="center",
        fontsize=8, color="white",
        bbox=dict(boxstyle="round,pad=0.25", facecolor="black", alpha=0.55),
        zorder=3,
    )
    return source


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------


def _placements_json(result: ShelfPackResult) -> dict:
    return {
        "project_width_mm": result.project_width_mm,
        "project_height_mm": result.project_height_mm,
        "project_area_m2": round(result.project_area_m2, 4),
        "placed_area_m2": round(result.placed_area_m2, 4),
        "uncovered_area_m2": round(result.uncovered_area_m2, 4),
        "coverage_percentage": round(
            100.0 * result.placed_area_m2 / result.project_area_m2, 2
        )
        if result.project_area_m2 > 0
        else 0.0,
        "placements": [
            {
                "slab_id": p.slab_id,
                "x_mm": p.x,
                "y_mm": p.y,
                "width_mm": p.width_mm,
                "height_mm": p.height_mm,
                "image_path": str(p.image_path) if p.image_path else None,
                "image_available": p.image_available,
            }
            for p in result.placements
        ],
        "overflow": [
            {
                "slab_id": s.slab_id,
                "width_mm": s.width_mm,
                "height_mm": s.height_mm,
            }
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
    log = logging.getLogger("run_smoke_placement")

    log.info("Clean slabs : %s", args.clean_slabs)
    log.info("Project area: %.0f x %.0f mm", args.project_width_mm, args.project_height_mm)

    inventory = load_inventory(args.clean_slabs)
    issues = validate_inventory(inventory)

    attached = 0
    if args.image_metadata is not None:
        attached = attach_processed_images(inventory, args.image_metadata)
        log.info(
            "Attached processed images: %d / %d slabs from %s",
            attached, len(inventory.slabs), args.image_metadata,
        )

    # --- terminal summary -------------------------------------------------
    print()
    print("=" * 70)
    print(f"Inventory loaded from {inventory.source_json}")
    print(f"  usable slabs : {len(inventory.slabs)}")
    print(f"  skipped rows : {len(inventory.skipped_records)} (missing dimensions)")
    if args.image_metadata is not None:
        print(f"  processed images attached : {attached} / {len(inventory.slabs)}  "
              f"(from {args.image_metadata})")
    if issues:
        print(f"  validation issues: {len(issues)}")
        for iss in issues:
            print(f"    ! {iss.code:24s} {iss.slab_id}  — {iss.message}")
    else:
        print("  validation issues: none")
    print()

    result = shelf_pack(
        inventory.slabs,
        project_width_mm=args.project_width_mm,
        project_height_mm=args.project_height_mm,
    )

    print(f"Project area : {args.project_width_mm:.0f} x {args.project_height_mm:.0f} mm  "
          f"({result.project_area_m2:.2f} m²)")
    print(f"Placed slabs : {len(result.placements)}")
    for p in result.placements:
        img_marker = "img" if p.image_available else "no-img"
        print(
            f"  {p.slab_id:30s}  pos=({p.x:>6.0f},{p.y:>6.0f})  "
            f"size={p.width_mm:>5.0f}x{p.height_mm:<5.0f} mm  [{img_marker}]"
        )
    print(f"Overflow     : {len(result.overflow)} slab(s) did not fit")
    for s in result.overflow:
        print(f"  {s.slab_id:30s}  size={s.width_mm:.0f}x{s.height_mm:.0f} mm")
    print()
    coverage_pct = (
        100.0 * result.placed_area_m2 / result.project_area_m2
        if result.project_area_m2 > 0
        else 0.0
    )
    print(f"Placed area      : {result.placed_area_m2:.2f} m²")
    print(f"Uncovered area   : {result.uncovered_area_m2:.2f} m²")
    print(f"Coverage         : {coverage_pct:.1f}%")
    print("=" * 70)

    # --- artefacts --------------------------------------------------------
    args.output.mkdir(parents=True, exist_ok=True)
    json_path = args.output / "smoke_placements.json"
    json_path.write_text(
        json.dumps(_placements_json(result), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    log.info("Wrote placements JSON: %s", json_path)

    if not args.no_preview:
        preview_path = args.output / "smoke_preview.png"
        source_tally = render_preview(result, preview_path)
        log.info("Wrote preview PNG    : %s", preview_path)
        print()
        print(f"Preview image sources (of {len(result.placements)} placed slabs):")
        print(f"  processed crop   : {source_tally.get('processed', 0)}")
        print(f"  original photo   : {source_tally.get('original', 0)}")
        print(f"  placeholder      : {source_tally.get('placeholder', 0)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
