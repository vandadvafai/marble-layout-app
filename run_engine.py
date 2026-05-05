"""CLI entry point for the placement engine.

Usage:
    python run_engine.py --input examples/input_floor_simple.json \
                         --output outputs/layout_output.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from placement_engine import engine
from placement_engine.exporters.json_exporter import write_output


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Marble/natural stone placement engine (MVP)."
    )
    parser.add_argument("--input", "-i", required=True, type=Path,
                        help="Path to the project input JSON.")
    parser.add_argument("--output", "-o", required=True, type=Path,
                        help="Path to write the layout output JSON.")
    parser.add_argument("--plot", "-p", type=Path, default=None,
                        help="Optional PNG path for a debug visualisation "
                             "of the first layout option.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    project_input = engine.load_input_from_file(args.input)
    output = engine.run(project_input)
    written = write_output(output, args.output)
    print(f"Wrote {len(output.layout_options)} layout option(s) to {written}")
    if args.plot is not None:
        # Imported lazily so matplotlib is only loaded when --plot is used.
        from placement_engine.visualization.debug_plot import render_layout
        plot_path = render_layout(project_input, output, args.plot)
        print(f"Wrote debug plot to {plot_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
