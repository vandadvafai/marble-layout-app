#!/usr/bin/env python3
"""V1 optimised slab-to-cut-piece assignment + greedy comparison.

Solves the V1 assignment as a min-cost bipartite matching (Hungarian
algorithm). Same constraints as the greedy layer — one slab per piece,
no rotation — but the matching is chosen globally to maximise the
count of high-priority assigned pieces, then total assigned floor
area, then minimise waste.

Always runs the greedy baseline at the same time and prints a side-by-
side comparison so designers can see exactly what the optimisation
buys them.

Example:

    python3 scripts/run_optimised_assignment.py \\
        --cut-list  outputs/cut_lists/demo_l_shape_floor/cut_list.json \\
        --inventory outputs/slab_ingestion_test/clean_slabs.json \\
        --layout    outputs/layouts/demo_l_shape_floor/layout.json \\
        --output    outputs/optimised_assignments/demo_l_shape_floor

Default output:

    <output>/optimised_assignment.json
    <output>/optimised_assignment_summary.json
    <output>/optimised_assignment_preview.png
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
)
from placement_engine.assignment.schema import Assignment, AssignmentSummary  # noqa: E402
from placement_engine.optimisation import (  # noqa: E402
    OPTIMISATION_STRATEGY_MIN_WASTE_GLOBAL,
    SUPPORTED_STRATEGIES,
    optimise_assignment,
    write_optimised_assignment_json,
    write_optimised_summary_json,
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
                   help="Optional path to layout.json — for the preview's "
                        "floor boundary + holes.")
    p.add_argument("--strategy", type=str,
                   default=OPTIMISATION_STRATEGY_MIN_WASTE_GLOBAL,
                   choices=sorted(SUPPORTED_STRATEGIES),
                   help="Optimisation strategy (default: min_waste_global).")
    p.add_argument("--output", required=True, type=Path,
                   help="Output directory.")
    p.add_argument("--no-preview", action="store_true",
                   help="Skip the PNG preview.")
    p.add_argument("--hide-slab-ids", action="store_true",
                   help="Show only piece IDs (no slab IDs) in the preview.")
    p.add_argument("--no-greedy-comparison", action="store_true",
                   help="Skip computing the greedy baseline.")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args(argv)


def _row(label: str, opt: float, greedy: float, fmt: str = "{:.4f}") -> str:
    delta = opt - greedy
    sign = "+" if delta > 0 else ""
    return (
        f"  {label:<24s}  "
        f"{fmt.format(greedy):>12s}    "
        f"{fmt.format(opt):>12s}    "
        f"{sign}{fmt.format(delta):>12s}"
    )


def _summary_row(
    label: str, opt: int | float, greedy: int | float,
    fmt: str = "{:d}",
) -> str:
    delta = opt - greedy
    sign = "+" if delta > 0 else ""
    return (
        f"  {label:<24s}  "
        f"{fmt.format(greedy):>12s}    "
        f"{fmt.format(opt):>12s}    "
        f"{sign}{fmt.format(delta):>12s}"
    )


def _print_comparison(
    optimised: Assignment, greedy: Assignment,
) -> None:
    o = optimised.summary
    g = greedy.summary
    print()
    print("=" * 78)
    print(f"Optimisation comparison — {optimised.target_name}")
    print("-" * 78)
    print(f"  {'metric':<24s}  {'greedy':>12s}    {'optimised':>12s}    "
          f"{'delta':>12s}")
    print("-" * 78)
    print(_summary_row("assigned_pieces", o.assigned_pieces, g.assigned_pieces))
    print(_summary_row("  full", o.full_assigned, g.full_assigned))
    print(_summary_row("  edge", o.edge_assigned, g.edge_assigned))
    print(_summary_row("  hole", o.hole_assigned, g.hole_assigned))
    print(_summary_row("  sliver", o.sliver_assigned, g.sliver_assigned))
    print(_summary_row("unassigned_pieces", o.unassigned_pieces, g.unassigned_pieces))
    print(_summary_row("slabs_used", o.slabs_used, g.slabs_used))
    print(_summary_row("unused_slabs", o.unused_slabs, g.unused_slabs))
    print(_row("assigned_area_m2",
               o.assigned_area_m2, g.assigned_area_m2))
    print(_row("unassigned_area_m2",
               o.unassigned_area_m2, g.unassigned_area_m2))
    print(_row("slab_area_used_m2",
               o.slab_area_used_m2, g.slab_area_used_m2))
    print(_row("estimated_waste_m2",
               o.estimated_waste_m2, g.estimated_waste_m2))
    print(f"  {'main_unassigned_reason':<24s}  "
          f"{(g.main_unassigned_reason or '-'):>12s}    "
          f"{(o.main_unassigned_reason or '-'):>12s}")
    print("=" * 78)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("run_optimised_assignment")

    result = optimise_assignment(
        args.cut_list, args.inventory, strategy=args.strategy,
    )
    log.info("Strategy : %s", result.strategy)

    if not args.no_greedy_comparison:
        greedy = build_assignment(args.cut_list, args.inventory)
        _print_comparison(result.assignment, greedy)
    else:
        s = result.assignment.summary
        print()
        print("=" * 78)
        print(f"Optimised assignment — {result.assignment.target_name}")
        print(f"  strategy            : {result.strategy}")
        print(f"  total_pieces        : {s.total_pieces}")
        print(f"  assigned            : {s.assigned_pieces}")
        print(f"  unassigned          : {s.unassigned_pieces}")
        print(f"  slabs used / total  : {s.slabs_used} / {s.total_slab_count}")
        print(f"  assigned area       : {s.assigned_area_m2:.4f} m²")
        print(f"  unassigned area     : {s.unassigned_area_m2:.4f} m²")
        print(f"  slab area used      : {s.slab_area_used_m2:.4f} m²")
        print(f"  estimated waste     : {s.estimated_waste_m2:.4f} m²")
        if s.main_unassigned_reason:
            print(f"  main reason         : {s.main_unassigned_reason}")
        print("=" * 78)

    # --- artefacts ---
    args.output.mkdir(parents=True, exist_ok=True)
    asg_path = write_optimised_assignment_json(
        result, args.output / "optimised_assignment.json",
    )
    sum_path = write_optimised_summary_json(
        result, args.output / "optimised_assignment_summary.json",
    )
    log.info("Wrote optimised JSON : %s", asg_path)
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
            result.assignment,
            args.output / "optimised_assignment_preview.png",
            boundary=boundary, holes=holes,
            show_slab_ids=not args.hide_slab_ids,
        )
        log.info("Wrote preview PNG    : %s", png_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
