#!/usr/bin/env python3
"""V1 geometry smoke test: load clean_slabs.json, sequentially pack, preview.

This is NOT a real placement strategy — it just walks the slabs in
``clean_slabs.json`` left-to-right, row by row, into a rectangular
target area defined by the V1 ``TargetArea`` model. The point is to
validate that the inventory pipeline hands the engine the right shapes
and that real slab images are reachable for visualization.

Example:

    python3 scripts/run_smoke_placement.py \\
        --inventory      outputs/slab_ingestion_test/clean_slabs.json \\
        --image-metadata outputs/image_intake/image_metadata.json \\
        --target-width-mm  5000 \\
        --target-height-mm 3000 \\
        --target-name      "Test Room" \\
        --output           outputs/smoke_placement

Output:

    <output>/smoke_preview.png       — labelled visual preview
    <output>/smoke_placements.json   — placements + overflow + metrics
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
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
from placement_engine.target_area import (  # noqa: E402
    TargetArea,
    target_area_warnings,
)


# Used when neither --target-width-mm nor --target-height-mm is supplied.
# The smoke run still works out of the box; the report flags the use of
# demo dimensions so callers don't mistake them for real inputs.
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
    p.add_argument(
        "--inventory",
        required=True,
        type=Path,
        help="Path to clean_slabs.json (output of prepare_slab_data.py).",
    )
    p.add_argument(
        "--target-width-mm",
        type=float,
        default=None,
        help=(
            "Target area width in mm. If omitted (together with "
            "--target-height-mm), the script uses 4000 × 3000 mm demo "
            "defaults and marks the run as such in the report."
        ),
    )
    p.add_argument(
        "--target-height-mm",
        type=float,
        default=None,
        help="Target area height in mm. See --target-width-mm.",
    )
    p.add_argument(
        "--target-name",
        type=str,
        default=None,
        help="Human-readable target name (e.g. 'Living room floor').",
    )
    p.add_argument(
        "--target-id",
        type=str,
        default=None,
        help="Stable identifier for the target (defaults to a slugified name).",
    )
    p.add_argument(
        "--target-required-area-m2",
        type=float,
        default=None,
        help=(
            "Designer-stated usable area in m². Cross-checked against the "
            "rectangle area; mismatch beyond 5%% adds a warning."
        ),
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
        "--output",
        type=Path,
        default=Path("outputs/smoke_placement"),
        help="Output directory (default: outputs/smoke_placement).",
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


def _resolve_target(args: argparse.Namespace) -> tuple[TargetArea, bool]:
    """Build a TargetArea from CLI args; flag whether demo defaults were used.

    Demo defaults apply only when *both* dimension flags are absent. If
    the user passes one but not the other, that's a CLI error (we don't
    want to silently mix a custom width with a default height).
    """
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
    name = args.target_name or (DEMO_TARGET_NAME if is_demo else f"{width:.0f} × {height:.0f} mm")
    target_id = args.target_id or (DEMO_TARGET_ID if is_demo else _slug(name))
    target = TargetArea(
        target_id=target_id,
        name=name,
        width_mm=width,
        height_mm=height,
        required_area_m2=args.target_required_area_m2,
    )
    return target, is_demo


def _slug(value: str) -> str:
    """Tiny slugifier good enough for filenames / IDs in V1."""
    out = "".join(c if c.isalnum() else "_" for c in value).strip("_")
    return out.lower() or "target"


# ---------------------------------------------------------------------------
# Preview rendering
# ---------------------------------------------------------------------------


def render_preview(
    result: ShelfPackResult,
    out_path: Path,
) -> dict[str, int]:
    """Render a labelled PNG of the smoke placement.

    Each placed slab is filled with its processed (green-box cropped)
    photo when available, falling back to the original photo, then to a
    clearly-marked grey hatched placeholder. The target boundary is
    drawn as a thick black rectangle.
    """
    import matplotlib

    matplotlib.use("Agg")  # headless
    import matplotlib.image as mpimg
    import matplotlib.patches as mpatches
    import matplotlib.pyplot as plt

    target = result.target
    fig, ax = plt.subplots(figsize=(11, 8))

    # Target boundary.
    ax.add_patch(
        mpatches.Rectangle(
            (0, 0),
            target.width_mm,
            target.height_mm,
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

    # Frame the figure with a margin proportional to target size.
    margin = max(target.width_mm, target.height_mm) * 0.05
    ax.set_xlim(-margin, target.width_mm + margin)
    ax.set_ylim(-margin, target.height_mm + margin)
    ax.set_aspect("equal")
    ax.set_xlabel("x (mm)")
    ax.set_ylabel("y (mm)")
    ax.set_title(
        f"V1 smoke placement — {target.name} "
        f"({target.width_mm:.0f} × {target.height_mm:.0f} mm)\n"
        f"{len(result.placements)} placed, "
        f"{len(result.overflow)} overflow, "
        f"coverage {result.coverage_percentage:.1f}% "
        f"({result.placed_area_m2:.2f} / {result.target_area_m2:.2f} m²)"
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
    """Draw a single placed slab. Return the source label used."""
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

    # Outline + label over the image.
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


def _placements_json(result: ShelfPackResult, is_demo: bool) -> dict:
    target = result.target
    return {
        "target": {
            "target_id": target.target_id,
            "name": target.name,
            "width_mm": target.width_mm,
            "height_mm": target.height_mm,
            "calculated_area_m2": round(target.calculated_area_m2, 4),
            "required_area_m2": target.required_area_m2,
            "notes": target.notes,
            "is_demo_default": is_demo,
        },
        "placed_area_m2": round(result.placed_area_m2, 4),
        "uncovered_area_m2": round(result.uncovered_area_m2, 4),
        "coverage_percentage": round(result.coverage_percentage, 2),
        "placements": [
            {
                "slab_id": p.slab_id,
                "x_mm": p.x,
                "y_mm": p.y,
                "width_mm": p.width_mm,
                "height_mm": p.height_mm,
                "image_path": str(p.image_path) if p.image_path else None,
                "image_available": p.image_available,
                "processed_image_path": (
                    str(p.processed_image_path) if p.processed_image_path else None
                ),
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
            "Attached processed images: %d / %d slabs from %s",
            attached, len(inventory.slabs), args.image_metadata,
        )

    result = shelf_pack(inventory.slabs, target)

    # --- terminal summary -------------------------------------------------
    print()
    print("=" * 70)
    print(f"Target area : {target.name}")
    if is_demo:
        print("              [demo default — pass --target-width-mm / "
              "--target-height-mm to override]")
    print(f"  target_id        : {target.target_id}")
    print(f"  width × height   : {target.width_mm:.0f} × {target.height_mm:.0f} mm")
    print(f"  calculated area  : {target.calculated_area_m2:.2f} m²")
    if target.required_area_m2 is not None:
        print(f"  required area    : {target.required_area_m2:.2f} m²")
    if target_warnings:
        print("  target warnings  :", ", ".join(target_warnings))
    print()
    print(f"Inventory loaded from {inventory.source_json}")
    print(f"  slabs loaded     : {len(inventory.slabs)}")
    print(f"  skipped rows     : {len(inventory.skipped_records)} (missing dimensions)")
    if args.image_metadata is not None:
        print(f"  processed images : {attached} / {len(inventory.slabs)}  "
              f"(from {args.image_metadata})")
    if issues:
        print(f"  validation issues: {len(issues)}")
        for iss in issues:
            print(f"    ! {iss.code:24s} {iss.slab_id}  — {iss.message}")
    else:
        print("  validation issues: none")
    print()
    print(f"Placed slabs  : {len(result.placements)}")
    for p in result.placements:
        img_marker = "img" if p.image_available else "no-img"
        print(
            f"  {p.slab_id:30s}  pos=({p.x:>6.0f},{p.y:>6.0f})  "
            f"size={p.width_mm:>5.0f}x{p.height_mm:<5.0f} mm  [{img_marker}]"
        )
    print(f"Overflow      : {len(result.overflow)} slab(s) did not fit")
    for s in result.overflow:
        print(f"  {s.slab_id:30s}  size={s.width_mm:.0f}x{s.height_mm:.0f} mm")
    print()
    print(f"Placed area      : {result.placed_area_m2:.2f} m²")
    print(f"Uncovered area   : {result.uncovered_area_m2:.2f} m²")
    print(f"Coverage         : {result.coverage_percentage:.1f}%")
    print("=" * 70)

    # --- artefacts --------------------------------------------------------
    args.output.mkdir(parents=True, exist_ok=True)
    json_path = args.output / "smoke_placements.json"
    json_path.write_text(
        json.dumps(_placements_json(result, is_demo), indent=2, ensure_ascii=False),
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
