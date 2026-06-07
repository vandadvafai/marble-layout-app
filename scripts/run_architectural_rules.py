#!/usr/bin/env python3
"""V1 architectural rule evaluation CLI.

Reads a layout.json + cut_list.json + an architectural plan JSON, and
writes a structured rule report describing which rules passed, which
were violated, where the offending pieces/seams sit, and the overall
design score.

Example:

    python3 scripts/run_architectural_rules.py \\
        --layout    outputs/layouts/demo_l_shape_floor/layout.json \\
        --cut-list  outputs/cut_lists/demo_l_shape_floor/cut_list.json \\
        --plan      examples/architectural/demo_l_shape_floor.json \\
        --output    outputs/architectural_reports/demo_l_shape_floor

Output:

    <output>/architectural_report.json
    <output>/architectural_report_summary.json
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
    evaluate_layout,
    load_architectural_plan,
    write_rule_report_json,
    write_rule_report_summary_json,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--layout", required=True, type=Path,
                   help="Path to layout.json.")
    p.add_argument("--cut-list", required=True, type=Path,
                   help="Path to cut_list.json.")
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
    log = logging.getLogger("run_architectural_rules")

    plan = load_architectural_plan(args.plan)
    report = evaluate_layout(args.layout, args.cut_list, plan)

    print()
    print("=" * 78)
    print(f"Source layout    : {report.source_layout_path}")
    print(f"Source cut list  : {report.source_cut_list_path}")
    print(f"Architectural plan: {args.plan}")
    print(f"Target           : {report.target_name} ({report.target_id})")
    print(f"Spaces / doorways / columns: "
          f"{len(plan.spaces)} / {len(plan.doorways)} / {len(plan.columns)}")
    print(f"Matching mode    : {plan.matching_mode}")
    print()
    print(f"Pieces evaluated : {len(report.pieces)}")
    print(f"Seams detected   : {len(report.seams)}")
    print()
    print(f"DESIGN SCORE     : {report.design_score:.1f} / 100 baseline "
          f"(soft cap 200)")
    print(f"  hard violations: {report.hard_violation_count}")
    print(f"  soft violations: {report.soft_violation_count}")
    print(f"  rewards earned : {report.reward_count}")
    print()
    print("Rules:")
    for r in report.rules:
        delta = f"{r.score_delta:+.1f}" if r.score_delta else "  —  "
        print(f"  [{r.status:<14}] {r.rule_id:<45} {delta}")
        if r.message:
            print(f"      → {r.message}")
    print()
    print("Score breakdown:")
    for k, v in report.score_breakdown.items():
        print(f"  {v:+7.2f}  {k}")
    print("=" * 78)

    args.output.mkdir(parents=True, exist_ok=True)
    full_path = write_rule_report_json(
        report, args.output / "architectural_report.json",
    )
    sum_path = write_rule_report_summary_json(
        report, args.output / "architectural_report_summary.json",
    )
    log.info("Wrote full report   : %s", full_path)
    log.info("Wrote summary report: %s", sum_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
