"""CLI: inspect a standardized CAD file and report what the intake sees.

Accepts either a `.dxf` (inspected directly) or a `.dwg` (converted to
a temporary DXF first, then inspected). The report records the
original file, its format, the converted DXF path, and the backend.

Usage:
    # DXF input
    python inspect_cad.py \\
        --cad examples/cad_inputs/basic_floor_standardized.dxf \\
        --out outputs/cad_inspections/basic_floor_report.md

    # DWG input (needs ODA File Converter)
    python inspect_cad.py \\
        --cad path/to/standardized_project.dwg \\
        --out outputs/cad_inspections/project_dwg_report.md \\
        --oda-path "/path/to/ODAFileConverter"

If --out is omitted the Markdown report is printed to stdout.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from placement_engine.cad_intake.dxf_reader import CADIntakeError
from placement_engine.cad_intake.inspection import (
    format_report_markdown,
    inspect_cad_file,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect a standardized CAD file (.dxf or .dwg) and report what "
            "the CAD intake will see. Useful for spotting layer typos or "
            "unsupported entities before running cad_to_input.py."
        )
    )
    parser.add_argument("--cad", "-c", required=True, type=Path,
                        help="Path to the standardized .dxf or .dwg file.")
    parser.add_argument("--out", "-o", type=Path, default=None,
                        help="Optional Markdown output path. If omitted, "
                             "the report is printed to stdout.")
    parser.add_argument("--oda-path", default=None,
                        help="Path to the ODA File Converter executable "
                             "(only needed for .dwg input).")
    parser.add_argument("--conversion-backend", default="auto",
                        choices=["auto", "oda", "none"],
                        help="DWG conversion backend (default: auto).")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = inspect_cad_file(
            args.cad,
            conversion_backend=args.conversion_backend,
            oda_path=args.oda_path,
        )
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
