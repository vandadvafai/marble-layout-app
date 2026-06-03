#!/usr/bin/env python3
"""V1 slab-to-cut-piece assignment.

Reads a ``cut_list.json`` and a ``clean_slabs.json`` inventory and
writes a 1:1 mapping from cut pieces to slabs. V1 rules:

  * priority: full → edge → hole → sliver
  * a slab fits a piece iff width × height covers the piece (no
    rotation)
  * smallest-fitting slab wins (least waste)
  * one slab → one piece in V1
  * pieces with no fitting slab are reported with a reason

Layout JSON is optional but recommended — when provided, the preview
draws the actual floor boundary + holes under the pieces.

Example:

    python3 scripts/run_assignment_generator.py \\
        --cut-list  outputs/cut_lists/demo_l_shape_floor/cut_list.json \\
        --inventory outputs/slab_ingestion_test/clean_slabs.json \\
        --layout    outputs/layouts/demo_l_shape_floor/layout.json \\
        --output    outputs/assignments/demo_l_shape_floor

Output:

    <output>/assignment.json
    <output>/assignment_summary.json
    <output>/assignment_preview.png
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

from placement_engine.assignment import (  # noqa: E402
    build_assignment,
    render_assignment_preview,
    write_assignment_json,
    write_summary_json,
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
    p.add_argument("--layout", type=Path, default=None,
                   help=(
                       "Optional path to the source layout.json — used "
                       "to draw the floor boundary + holes in the "
                       "preview. Without it, the preview shows pieces "
                       "only (no outline)."
                   ))
    p.add_argument("--output", required=True, type=Path,
                   help="Output directory.")
    p.add_argument("--no-preview", action="store_true",
                   help="Skip the PNG preview.")
    p.add_argument("--hide-slab-ids", action="store_true",
                   help="Show only piece IDs (no slab IDs) in the preview.")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("run_assignment_generator")

    assignment = build_assignment(args.cut_list, args.inventory)
    summary = assignment.summary

    # ---- terminal summary --------------------------------------------------
    print()
    print("=" * 76)
    print(f"Source cut list : {assignment.source_cut_list_path}")
    print(f"Source inventory: {assignment.source_inventory_path}")
    print(f"Target          : {assignment.target_name} ({assignment.target_id})")
    print()
    print(f"  total pieces        : {summary.total_pieces}")
    print(f"  assigned            : {summary.assigned_pieces}")
    print(f"    full              : {summary.full_assigned}")
    print(f"    edge              : {summary.edge_assigned}")
    print(f"    hole              : {summary.hole_assigned}")
    print(f"    sliver            : {summary.sliver_assigned}")
    print(f"  unassigned          : {summary.unassigned_pieces}")
    print()
    print(f"  slabs total         : {summary.total_slab_count}")
    print(f"  slabs used          : {summary.slabs_used}")
    print(f"  slabs unused        : {summary.unused_slabs}")
    print()
    print(f"  assigned area       : {summary.assigned_area_m2:.4f} m²  (floor covered)")
    print(f"  unassigned area     : {summary.unassigned_area_m2:.4f} m²  (floor NOT covered)")
    print(f"  slab area used      : {summary.slab_area_used_m2:.4f} m²  (total slab surface consumed)")
    print(f"  estimated waste     : {summary.estimated_waste_m2:.4f} m²  (slab area − assigned piece area)")
    if summary.main_unassigned_reason:
        print(f"  main reason         : {summary.main_unassigned_reason}")
    if summary.unassigned_pieces > 0:
        reasons: dict[str, int] = {}
        for piece in assignment.pieces:
            if piece.reason:
                reasons[piece.reason] = reasons.get(piece.reason, 0) + 1
        print()
        print("  unassigned reasons:")
        for reason, count in sorted(reasons.items()):
            print(f"    {reason}: {count}")
    print("=" * 76)

    # ---- artefacts ---------------------------------------------------------
    args.output.mkdir(parents=True, exist_ok=True)
    asg_path = write_assignment_json(assignment, args.output / "assignment.json")
    sum_path = write_summary_json(assignment, args.output / "assignment_summary.json")
    log.info("Wrote assignment JSON: %s", asg_path)
    log.info("Wrote summary JSON   : %s", sum_path)

    if not args.no_preview:
        boundary = None
        holes = None
        if args.layout is not None:
            layout_dict = json.loads(args.layout.read_text(encoding="utf-8"))
            target = layout_dict.get("target", {})
            boundary = [tuple(pt) for pt in target.get("boundary", [])]
            holes = [
                [tuple(pt) for pt in hole]
                for hole in target.get("holes", [])
            ]
        png_path = render_assignment_preview(
            assignment, args.output / "assignment_preview.png",
            boundary=boundary, holes=holes,
            show_slab_ids=not args.hide_slab_ids,
        )
        log.info("Wrote preview PNG    : %s", png_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
