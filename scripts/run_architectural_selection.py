#!/usr/bin/env python3
"""V1 architectural-aware candidate selection CLI.

Generates one candidate layout per anchor mode (bottom_left,
bottom_right, top_left, top_right), evaluates each against the
architectural plan, hard-rejects any candidate that contains a piece
below the plan's minimum cuttable side, and writes the winner's
artefacts plus a side-by-side candidate comparison.

Example:

    python3 scripts/run_architectural_selection.py \\
        --dxf       examples/cad_inputs/demo/demo_l_shape_floor.dxf \\
        --inventory outputs/slab_ingestion_test/clean_slabs.json \\
        --plan      examples/architectural/demo_l_shape_floor.json \\
        --output    outputs/architectural_selection/demo_l_shape_floor

Outputs:

    <output>/candidates_summary.json   per-candidate comparison
    <output>/selected_layout.json      winner's layout (canonical shape)
    <output>/selected_report.json      winner's rule report
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from placement_engine.architectural import (  # noqa: E402
    load_architectural_plan,
    select_best_layout,
    write_selected_artifacts,
)
from placement_engine.inventory import load_inventory  # noqa: E402
from placement_engine.target_area import load_target_geometry_from_dxf  # noqa: E402


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--dxf", required=True, type=Path,
                   help="Path to a standardized DXF target.")
    p.add_argument("--inventory", required=True, type=Path,
                   help="Path to clean_slabs.json.")
    p.add_argument("--plan", required=True, type=Path,
                   help="Path to architectural plan JSON.")
    p.add_argument("--output", required=True, type=Path,
                   help="Output directory.")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("run_architectural_selection")

    geometry = load_target_geometry_from_dxf(args.dxf)
    inventory = load_inventory(args.inventory)
    plan = load_architectural_plan(args.plan)

    result = select_best_layout(
        geometry, inventory.slabs, plan,
        source_inventory_path=str(args.inventory),
    )

    # ---- terminal summary -----------------------------------------------
    print()
    print("=" * 78)
    print(f"DXF target  : {geometry.name}")
    print(f"Plan        : {args.plan}")
    print(f"Inventory   : {args.inventory}")
    print()
    print(f"Candidates : {len(result.candidates)}    "
          f"valid: {result.valid_candidate_count}")
    print(f"WINNER     : {result.selected_candidate_id}")
    print(f"Reason     : {result.selection_reason}")
    print()
    print(f"{'candidate':<30} {'strategy':<18} "
          f"{'score':>7} {'valid':<6} {'doorR':>6} {'colR':>5} {'minV':>5}")
    print("-" * 88)
    for c in result.candidates:
        s = c.summary_dict()
        marker = "★" if c.candidate_id == result.selected_candidate_id else " "
        print(
            f"{marker} {c.candidate_id:<28} {c.strategy:<18} "
            f"{s['design_score']:>7.1f} "
            f"{'yes' if c.is_valid else 'no':<6} "
            f"{s['score_breakdown'].get('R7_full_slabs_in_doorways', 0):>+6.1f} "
            f"{s['score_breakdown'].get('R5_seams_near_columns', 0):>+5.1f} "
            f"{len(s['pieces_below_minimum']):>5d}"
        )
    print()
    print("V1 limitations (surfaced in candidates_summary.json):")
    for limit in result.v1_limitations:
        print(f"  • {limit}")
    print("=" * 78)

    paths = write_selected_artifacts(result, args.output)
    for name, path in paths.items():
        log.info("Wrote %s : %s", name, path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
