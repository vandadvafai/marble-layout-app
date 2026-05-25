"""CLI: one-command CAD → layout hand-off package.

The MVP happy path: a designer standardizes a customer plan in
Rhino/AutoCAD, exports a **standardized DXF**, and runs this one
command to get a complete layout package.

    standardized DXF
        → CAD inspection + intake
        → engine input JSON
        → placement engine
        → per-strategy hand-off package (DXF + report + JSON + preview)

DWG input is also accepted *if* an external converter is configured
(see --oda-path), but DXF is the recommended MVP input.

Output layout (one folder per run):

    <out>/
      cad_inspection.md            what the intake saw
      generated_engine_input.json  the engine input that was run
      internal/                    (only with --keep-intermediate)
        full_engine_output.json
      <strategy>/                  one per requested strategy
        layout.json
        layout.dxf
        layout_report.md
        preview.png                (unless --no-preview)

Usage:
    python3 make_package.py \\
        --cad examples/cad_inputs/demo/demo_floor_with_column.dxf \\
        --project-id demo_floor_with_column_001 \\
        --project-type floor \\
        --out outputs/layout_packages/demo_floor_with_column \\
        --strategies balanced lowest_waste \\
        --include-test-slabs --test-slab-count auto

This CLI is a thin wrapper over `generate_layout_package` — the same
orchestration function the local Streamlit UI uses.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from placement_engine.cad_conversion import CADConversionError
from placement_engine.cad_intake.dxf_reader import CADIntakeError
from placement_engine.ui.app_helpers import PackageResult, generate_layout_package


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the full CAD → layout package pipeline for one "
            "standardized .dxf (or .dwg, if a converter is configured)."
        )
    )
    parser.add_argument("--cad", "-c", required=True, type=Path,
                        help="Standardized .dxf input (recommended) or .dwg.")
    parser.add_argument("--project-id", required=True,
                        help="Identifier copied through to the engine output.")
    parser.add_argument("--out", "-o", required=True, type=Path,
                        help="Package output directory.")
    parser.add_argument("--project-type", default="floor",
                        help="Free-form project type label (default: floor).")
    parser.add_argument("--strategies", nargs="+",
                        default=["balanced", "lowest_waste"],
                        help="Strategies to run (default: balanced lowest_waste).")
    parser.add_argument("--include-test-slabs", action="store_true",
                        help="Attach a synthetic test slab inventory "
                             "(required — make_package runs the engine).")
    parser.add_argument("--test-slab-count", default="auto",
                        help="'auto' (area-based) or an integer.")
    parser.add_argument("--test-slab-width", type=float, default=3200.0)
    parser.add_argument("--test-slab-height", type=float, default=1800.0)
    parser.add_argument("--test-slab-thickness", type=float, default=20.0)
    parser.add_argument("--slab-buffer-factor", type=float, default=1.25)
    parser.add_argument("--no-preview", dest="preview",
                        action="store_false", default=True,
                        help="Skip preview PNG generation.")
    parser.add_argument("--keep-intermediate", action="store_true",
                        help="Also write internal/full_engine_output.json.")
    parser.add_argument("--clean-output", action="store_true",
                        help="Delete the output folder before writing.")
    parser.add_argument("--oda-path", default=None,
                        help="ODA File Converter executable (only for .dwg input).")
    parser.add_argument("--conversion-backend", default="auto",
                        choices=["auto", "oda", "none"],
                        help="DWG conversion backend (default: auto).")
    return parser.parse_args(argv)


def _slab_count(raw: str) -> int | str:
    if raw == "auto":
        return "auto"
    try:
        n = int(raw)
    except ValueError:
        raise SystemExit(
            f"--test-slab-count must be 'auto' or an integer, got {raw!r}"
        )
    if n < 1:
        raise SystemExit(f"--test-slab-count must be >= 1, got {n}")
    return n


def _print_summary(args: argparse.Namespace, result: PackageResult) -> None:
    """Print the clean terminal summary described in the milestone spec."""
    layout = result.payload["layout"]
    holes = layout["holes"]
    usable_area = result.engine_output.layout_options[0].metrics.project_usable_area

    print()
    print("Package created:")
    print(f"  {result.output_dir}/")
    print()
    print("Input:")
    print(f"  - CAD file: {args.cad}")
    print(f"  - Project ID: {result.project_id}")
    print(f"  - Project type: {result.project_type}")
    print(f"  - Source: {layout['source_file']['type']}")
    print()
    print("CAD intake:")
    print(f"  - boundary found: yes ({len(layout['boundary'])} vertices)")
    print(f"  - holes found: {len(holes)}")
    print(f"  - project usable area: {usable_area:.0f} mm²")
    print(f"  - slabs generated: {len(result.payload['slabs'])}")
    print()
    print("Strategies:")
    for i, option in enumerate(result.engine_output.layout_options, start=1):
        m = option.metrics
        files = result.per_strategy_files[option.strategy]
        print(f"  {i}. {option.strategy}")
        print(f"     - layout_status: {m.layout_status}")
        print(f"     - inventory_status: {m.inventory_status}")
        print(f"     - coverage_percentage: {m.coverage_percentage}%")
        print(f"     - waste_percentage: {m.waste_percentage}%")
        print(f"     - pieces: {m.piece_count}")
        print(f"     - slabs used: {m.slabs_used}")
        print(f"     - seams: {m.seam_count}")
        print(f"     - report: {files['report']}")
        print(f"     - DXF: {files['dxf']}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not args.cad.is_file():
        print(f"CAD file not found: {args.cad}", file=sys.stderr)
        return 2
    if not args.include_test_slabs:
        print(
            "No slab inventory was provided. Use --include-test-slabs for "
            "validation (with --test-slab-count auto or an integer), or "
            "provide a real slab inventory when database integration is "
            "available.",
            file=sys.stderr,
        )
        return 2

    try:
        result = generate_layout_package(
            args.cad,
            project_id=args.project_id,
            output_dir=args.out,
            project_type=args.project_type,
            strategies=args.strategies,
            include_test_slabs=True,
            test_slab_count=_slab_count(args.test_slab_count),
            test_slab_width=args.test_slab_width,
            test_slab_height=args.test_slab_height,
            test_slab_thickness=args.test_slab_thickness,
            slab_buffer_factor=args.slab_buffer_factor,
            generate_preview=args.preview,
            clean_output=args.clean_output,
            keep_intermediate=args.keep_intermediate,
            conversion_backend=args.conversion_backend,
            oda_path=args.oda_path,
        )
    except CADConversionError as exc:
        print(f"CAD conversion error: {exc}", file=sys.stderr)
        return 2
    except CADIntakeError as exc:
        print(f"CAD intake error: {exc}", file=sys.stderr)
        return 2

    _print_summary(args, result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
