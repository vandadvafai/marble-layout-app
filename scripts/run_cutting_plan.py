#!/usr/bin/env python3
"""V1 cutting / offcut-aware planning CLI.

Reads a ``cut_list.json`` and a ``clean_slabs.json`` inventory and
writes a cutting plan that lets a single slab supply multiple cut
pieces. Tracks placements, offcuts, and estimated waste per slab.

V1 rules:

  * priority: full → edge → hole → sliver (largest area first inside
    a class)
  * axis-aligned, no rotation
  * guillotine-style split: each placed piece partitions the host
    rectangle into a right strip + a top strip; both become offcuts
  * place into the rectangle that leaves the least area behind
  * pieces that don't fit any remaining rectangle are unassigned
    with a reason (``no_slab_fits`` / ``all_fitting_slabs_used``)

Example:

    python3 scripts/run_cutting_plan.py \\
        --cut-list  outputs/cut_lists/demo_l_shape_floor/cut_list.json \\
        --inventory outputs/slab_ingestion_test/clean_slabs.json \\
        --output    outputs/cutting_plans/demo_l_shape_floor

Output:

    <output>/cutting_plan.json
    <output>/cutting_plan_summary.json
    <output>/cutting_plan_preview.png
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from placement_engine.cutting import (  # noqa: E402
    build_cutting_plan,
    render_cutting_plan_preview,
    write_cutting_plan_json,
    write_cutting_plan_summary_json,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--cut-list", required=True, type=Path,
                   help="Path to cut_list.json.")
    p.add_argument("--inventory", required=True, type=Path,
                   help="Path to clean_slabs.json.")
    p.add_argument("--output", required=True, type=Path,
                   help="Output directory.")
    p.add_argument("--no-preview", action="store_true",
                   help="Skip the PNG preview.")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("run_cutting_plan")

    plan = build_cutting_plan(args.cut_list, args.inventory)
    summary = plan.summary

    print()
    print("=" * 76)
    print(f"Source cut list : {plan.source_cut_list_path}")
    print(f"Source inventory: {plan.source_inventory_path}")
    print(f"Target          : {plan.target_name} ({plan.target_id})")
    print()
    print(f"  total cut pieces    : {summary.total_cut_pieces}")
    print(f"  assigned            : {summary.assigned_cut_pieces}")
    print(f"  unassigned          : {summary.unassigned_cut_pieces}")
    print()
    print(f"  slabs used          : {summary.slabs_used}")
    print(f"  slabs unused        : {summary.unused_slabs}")
    print()
    print(f"  total slab area     : {summary.total_slab_area_m2:.4f} m²  (used slabs only)")
    print(f"  used cut area       : {summary.used_cut_area_m2:.4f} m²  (sum of placed pieces)")
    print(f"  estimated waste     : {summary.estimated_waste_m2:.4f} m²  (used slab − placed)")
    print()
    print("  per-slab breakdown:")
    for slab in plan.slabs:
        used_pct = (
            100.0 * slab.used_area_m2 / slab.original_area_m2
            if slab.original_area_m2 > 0 else 0.0
        )
        print(
            f"    {slab.slab_id}: "
            f"{len(slab.placements)} piece(s), "
            f"used {slab.used_area_m2:.2f}/{slab.original_area_m2:.2f} m² "
            f"({used_pct:.0f}%), waste {slab.waste_area_m2:.2f} m², "
            f"{len(slab.offcuts)} offcut(s)"
        )
    if plan.unassigned:
        print()
        print("  unassigned pieces:")
        for u in plan.unassigned:
            print(
                f"    {u.cut_piece_id} ({u.classification}) "
                f"{int(u.width_mm)}×{int(u.height_mm)} mm "
                f"→ {u.reason}"
            )
    print("=" * 76)

    args.output.mkdir(parents=True, exist_ok=True)
    plan_path = write_cutting_plan_json(plan, args.output / "cutting_plan.json")
    sum_path = write_cutting_plan_summary_json(plan, args.output / "cutting_plan_summary.json")
    log.info("Wrote cutting plan JSON: %s", plan_path)
    log.info("Wrote summary JSON     : %s", sum_path)

    if not args.no_preview:
        png_path = render_cutting_plan_preview(
            plan, args.output / "cutting_plan_preview.png",
        )
        log.info("Wrote preview PNG      : %s", png_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
