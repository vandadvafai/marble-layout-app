"""CLI: one-shot CAD → layout hand-off package.

Chains the whole pipeline behind a single command:

    standardized .dwg/.dxf
        → DWG-to-DXF conversion (if needed)
        → engine input JSON
        → placement engine
        → CAD hand-off package (per-strategy DXF + report + JSON + preview)

Usage:
    python make_package.py \\
        --cad path/to/standardized_project.dwg \\
        --project-id project_001 \\
        --out outputs/layout_packages/project_001 \\
        --strategies balanced lowest_waste \\
        --include-test-slabs \\
        --test-slab-count auto \\
        --oda-path "/path/to/ODAFileConverter"

This is thin orchestration over existing pieces — it does not add new
engine behaviour. For a geometry-only draft (no slabs) use
`cad_to_input.py` instead; `make_package.py` runs the engine and so
needs a slab inventory.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from placement_engine import engine
from placement_engine.cad_conversion import CADConversionError
from placement_engine.cad_intake import build_project_input_dict
from placement_engine.cad_intake.dxf_reader import CADIntakeError
from placement_engine.exporters.package import write_package
from placement_engine.models import ProjectInput
from placement_engine.utils.test_inventory import SlabInventorySpec


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the full CAD → layout package pipeline for one standardized "
            ".dxf or .dwg file."
        )
    )
    parser.add_argument("--cad", "-c", required=True, type=Path,
                        help="Standardized .dxf or .dwg input file.")
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
    parser.add_argument("--oda-path", default=None,
                        help="ODA File Converter executable (for .dwg input).")
    parser.add_argument("--conversion-backend", default="auto",
                        choices=["auto", "oda", "none"],
                        help="DWG conversion backend (default: auto).")
    parser.add_argument("--no-preview", dest="preview",
                        action="store_false", default=True,
                        help="Skip preview PNG generation.")
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


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not args.include_test_slabs:
        print(
            "make_package.py runs the placement engine and therefore needs a "
            "slab inventory. Pass --include-test-slabs (with --test-slab-count "
            "auto or an integer), or build the input JSON with cad_to_input.py "
            "and edit in real slabs.",
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

    args.out.mkdir(parents=True, exist_ok=True)
    # Keep the generated engine input next to the package for traceability.
    (args.out / "generated_engine_input.json").write_text(
        json.dumps(payload, indent=2)
    )

    # 2. Validate + 3. run the placement engine.
    project = ProjectInput.model_validate(payload)
    output = engine.run(project)

    # 4. Export the per-strategy hand-off package.
    written = write_package(
        project, output, args.out, render_preview=args.preview
    )

    source = payload["layout"]["source_file"]
    print(f"Wrote package to {args.out}")
    print(f"  source: {source['type']}")
    if source.get("converted_dxf_path"):
        print(f"  converted DXF: {source['converted_dxf_path']}")
    print(f"  slabs generated: {len(payload['slabs'])}")
    for strategy, files in written.items():
        opt = next(o for o in output.layout_options if o.strategy == strategy)
        m = opt.metrics
        print(f"  {strategy}: coverage={m.coverage_percentage}% "
              f"({m.layout_status}), {len(files)} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
