"""CLI: bundle an engine output into a CAD/Rhino/AutoCAD hand-off package.

Usage:
    python export_package.py \\
        --input examples/input_long_corridor.json \\
        --layout outputs/validation_runs/layout_long_corridor.json \\
        --out outputs/layout_packages/long_corridor

Optional flags:
    --strategy <name>     export only the named strategy (e.g. lowest_waste)
    --no-preview          skip the matplotlib preview PNG

If --layout is omitted the engine is run on --input and the resulting
output is exported directly. This is convenient for quick experiments
and keeps the CLI self-contained.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from placement_engine import engine
from placement_engine.exporters.package import write_package
from placement_engine.models import EngineOutput


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Bundle a placement engine output into a Rhino/AutoCAD "
            "hand-off package (DXF + Markdown report + JSON + preview)."
        )
    )
    parser.add_argument("--input", "-i", required=True, type=Path,
                        help="Path to the project input JSON.")
    parser.add_argument("--layout", "-l", type=Path, default=None,
                        help=(
                            "Path to an existing layout output JSON. If "
                            "omitted, the engine is run on --input."
                        ))
    parser.add_argument("--out", "-o", required=True, type=Path,
                        help="Package output directory.")
    parser.add_argument("--strategy", "-s", default=None,
                        help=(
                            "Export only the named strategy "
                            "(e.g. balanced, lowest_waste). Default: all."
                        ))
    parser.add_argument("--no-preview", dest="preview",
                        action="store_false", default=True,
                        help="Skip the preview PNG.")
    return parser.parse_args(argv)


def _load_layout(layout_path: Path) -> EngineOutput:
    raw = json.loads(layout_path.read_text())
    return EngineOutput.model_validate(raw)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    project_input = engine.load_input_from_file(args.input)
    output = (
        _load_layout(args.layout) if args.layout is not None
        else engine.run(project_input)
    )

    options = output.layout_options
    if args.strategy is not None:
        matched = [o for o in options if o.strategy == args.strategy]
        if not matched:
            available = ", ".join(o.strategy for o in options) or "(none)"
            print(
                f"error: --strategy {args.strategy!r} not found in layout "
                f"output (available: {available})",
                file=sys.stderr,
            )
            return 2
        options = matched

    written = write_package(
        project_input=project_input,
        output=output,
        target_dir=args.out,
        options=options,
        render_preview=args.preview,
    )

    print(f"Wrote package to {args.out}")
    for strategy, files in written.items():
        print(f"  {strategy}:")
        for f in files:
            print(f"    - {f.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
