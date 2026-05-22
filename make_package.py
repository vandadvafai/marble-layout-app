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
(see --oda-path), but DXF is the recommended MVP input — if no
converter is available, export DXF from Rhino/AutoCAD manually.

Output layout (one folder per run):

    <out>/
      cad_inspection.md            what the intake saw
      generated_engine_input.json  the engine input that was run
      internal/                    (only with --keep-intermediate)
        full_engine_output.json    raw multi-option engine output
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

This is thin orchestration over existing pieces — it adds no engine
behaviour. The lower-level scripts (cad_to_input.py, run_engine.py,
export_package.py, inspect_cad.py) remain available for debugging.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from placement_engine import engine
from placement_engine.cad_conversion import CADConversionError
from placement_engine.cad_intake import build_project_input_dict
from placement_engine.cad_intake.dxf_reader import CADIntakeError
from placement_engine.cad_intake.inspection import (
    format_report_markdown,
    inspect_cad_file,
)
from placement_engine.exporters.dxf_exporter import write_dxf
from placement_engine.exporters.markdown_report import write_report
from placement_engine.models import EngineOutput, LayoutOption, ProjectInput
from placement_engine.utils.test_inventory import SlabInventorySpec
from placement_engine.visualization.debug_plot import render_layout


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


def _slug(name: str) -> str:
    """Filesystem-safe slug for a strategy subfolder name."""
    return "".join(c if c.isalnum() else "_" for c in name).strip("_").lower()


def _write_strategy_package(
    project: ProjectInput,
    output: EngineOutput,
    option: LayoutOption,
    option_index: int,
    strategy_dir: Path,
    render_preview: bool,
) -> dict[str, Path]:
    """Write one strategy's layout.json / layout.dxf / layout_report.md
    (+ preview.png) into `strategy_dir`. Returns the written paths."""
    strategy_dir.mkdir(parents=True, exist_ok=True)

    # layout.json — the engine output trimmed to just this option.
    single = output.model_copy(update={"layout_options": [option]})
    json_path = strategy_dir / "layout.json"
    json_path.write_text(json.dumps(single.to_json_dict(), indent=2))

    dxf_path = write_dxf(project, option, strategy_dir / "layout.dxf")
    report_path = write_report(
        project, output, option, strategy_dir / "layout_report.md"
    )

    written = {"json": json_path, "dxf": dxf_path, "report": report_path}
    if render_preview:
        preview_path = strategy_dir / "preview.png"
        render_layout(project, output, preview_path, option_index=option_index)
        written["preview"] = preview_path
    return written


def _print_summary(
    args: argparse.Namespace,
    payload: dict,
    output: EngineOutput,
    per_strategy_files: dict[str, dict[str, Path]],
) -> None:
    """Print the clean terminal summary described in the milestone spec."""
    layout = payload["layout"]
    holes = layout["holes"]
    # project_usable_area is identical across options; read it off the first.
    usable_area = output.layout_options[0].metrics.project_usable_area

    print()
    print("Package created:")
    print(f"  {args.out}/")
    print()
    print("Input:")
    print(f"  - CAD file: {args.cad}")
    print(f"  - Project ID: {payload['project_id']}")
    print(f"  - Project type: {payload['project_type']}")
    print(f"  - Source: {layout['source_file']['type']}")
    print()
    print("CAD intake:")
    print(f"  - boundary found: yes ({len(layout['boundary'])} vertices)")
    print(f"  - holes found: {len(holes)}")
    print(f"  - project usable area: {usable_area:.0f} mm²")
    print(f"  - slabs generated: {len(payload['slabs'])}")
    print()
    print("Strategies:")
    for i, option in enumerate(output.layout_options, start=1):
        m = option.metrics
        files = per_strategy_files[option.strategy]
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

    # Clear, actionable error for a missing CAD file.
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

    slab_spec = SlabInventorySpec(
        count=_slab_count(args.test_slab_count),
        width=args.test_slab_width,
        height=args.test_slab_height,
        thickness=args.test_slab_thickness,
        buffer_factor=args.slab_buffer_factor,
    )

    # 1. Convert (if .dwg) + build the engine input JSON.
    try:
        payload = build_project_input_dict(
            args.cad,
            project_id=args.project_id,
            project_type=args.project_type,
            test_slab_spec=slab_spec,
            options_requested=args.strategies,
            conversion_backend=args.conversion_backend,
            oda_path=args.oda_path,
        )
    except CADConversionError as exc:
        print(f"CAD conversion error: {exc}", file=sys.stderr)
        return 2
    except CADIntakeError as exc:
        print(f"CAD intake error: {exc}", file=sys.stderr)
        return 2

    # Prepare the output folder.
    if args.clean_output and args.out.exists():
        shutil.rmtree(args.out)
    args.out.mkdir(parents=True, exist_ok=True)

    # 2. CAD inspection report (never raises for content reasons).
    inspection = inspect_cad_file(
        args.cad,
        conversion_backend=args.conversion_backend,
        oda_path=args.oda_path,
    )
    (args.out / "cad_inspection.md").write_text(
        format_report_markdown(inspection)
    )

    # 3. Persist the generated engine input next to the package.
    (args.out / "generated_engine_input.json").write_text(
        json.dumps(payload, indent=2)
    )

    # 4. Validate + 5. run the placement engine.
    project = ProjectInput.model_validate(payload)
    output = engine.run(project)

    if args.keep_intermediate:
        internal = args.out / "internal"
        internal.mkdir(parents=True, exist_ok=True)
        (internal / "full_engine_output.json").write_text(
            json.dumps(output.to_json_dict(), indent=2)
        )

    # 6. Per-strategy hand-off packages in <out>/<strategy>/.
    per_strategy_files: dict[str, dict[str, Path]] = {}
    for index, option in enumerate(output.layout_options):
        per_strategy_files[option.strategy] = _write_strategy_package(
            project=project,
            output=output,
            option=option,
            option_index=index,
            strategy_dir=args.out / _slug(option.strategy),
            render_preview=args.preview,
        )

    # 7. Terminal summary.
    _print_summary(args, payload, output, per_strategy_files)
    return 0


if __name__ == "__main__":
    sys.exit(main())
