"""CLI: inspect a standardized DXF and report what the intake will see.

Usage:
    python inspect_cad.py \\
        --cad examples/cad_inputs/basic_floor_standardized.dxf \\
        --out outputs/cad_inspections/basic_floor_report.md

If --out is omitted the Markdown report is printed to stdout. Useful
for verifying a DXF before running `cad_to_input.py`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from placement_engine.cad_intake.dxf_reader import CADIntakeError
from placement_engine.cad_intake.inspection import (
    format_report_markdown,
    inspect_dxf,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect a standardized DXF and report what the CAD intake will "
            "see. Useful for spotting layer typos or unsupported entities "
            "before running cad_to_input.py."
        )
    )
    parser.add_argument("--cad", "-c", required=True, type=Path,
                        help="Path to the standardized DXF file.")
    parser.add_argument("--out", "-o", type=Path, default=None,
                        help="Optional Markdown output path. If omitted, "
                             "the report is printed to stdout.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = inspect_dxf(args.cad)
    except CADIntakeError as exc:
        print(f"CAD intake error: {exc}", file=sys.stderr)
        return 2

    body = format_report_markdown(report)
    if args.out is None:
        print(body)
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(body)
        print(f"Wrote {args.out}")
    return 0 if not report.errors else 1


if __name__ == "__main__":
    sys.exit(main())
