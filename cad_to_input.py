"""CLI: convert a standardized CAD file into an engine input JSON file.

Accepts either a `.dxf` (read directly) or a `.dwg` (converted to DXF
internally via ODA File Converter before the same intake runs).

Usage:
    # DXF input
    python cad_to_input.py \\
        --cad examples/cad_inputs/basic_floor_standardized.dxf \\
        --out examples/generated/input_basic_floor_from_cad.json \\
        --project-id cad_basic_floor_001

    # DWG input (needs ODA File Converter)
    python cad_to_input.py \\
        --cad path/to/standardized_project.dwg \\
        --out examples/generated/input_from_dwg.json \\
        --project-id cad_project_001 \\
        --include-test-slabs \\
        --oda-path "/path/to/ODAFileConverter"

Optional flags:
    --include-test-slabs    attach a synthetic test slab inventory
    --test-slab-count       'auto' (default) or an integer
    --test-slab-width       slab width mm (default 3200)
    --test-slab-height      slab height mm (default 1800)
    --test-slab-thickness   slab thickness mm (default 20)
    --slab-buffer-factor    surplus factor for 'auto' count (default 1.25)
    --strategy <name>       add to options_requested (repeatable; defaults to "balanced")
    --random-seed <int>     seed for natural_random; defaults to 42
    --oda-path <path>       ODA File Converter executable (for .dwg input)
    --conversion-backend    auto (default) / oda / none

The output JSON validates against `ProjectInput` only when
--include-test-slabs is set; without slabs the JSON is a draft the
designer fills in before running the placement engine.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from placement_engine.cad_conversion import CADConversionError
from placement_engine.cad_intake import build_project_input_dict
from placement_engine.cad_intake.dxf_reader import CADIntakeError
from placement_engine.utils.test_inventory import SlabInventorySpec


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert a standardized DXF into a placement-engine input JSON. "
            "The DXF must place the project surface on layer "
            "AI_PROJECT_BOUNDARY and any holes/cutouts on AI_HOLES_CUTOUTS."
        )
    )
    parser.add_argument("--cad", "-c", required=True, type=Path,
                        help="Path to the standardized .dxf or .dwg file.")
    parser.add_argument("--out", "-o", required=True, type=Path,
                        help="Path to write the generated JSON.")
    parser.add_argument("--project-id", required=True,
                        help="Identifier copied through to the engine output.")
    parser.add_argument("--project-type", default="floor",
                        help="Free-form project type label (default: floor).")
    parser.add_argument("--include-test-slabs", action="store_true",
                        help="Attach a synthetic test slab inventory.")
    parser.add_argument("--test-slab-count", default="auto",
                        help="'auto' (area-based estimate) or an integer. "
                             "Only used with --include-test-slabs.")
    parser.add_argument("--test-slab-width", type=float, default=3200.0,
                        help="Test slab width in mm (default 3200).")
    parser.add_argument("--test-slab-height", type=float, default=1800.0,
                        help="Test slab height in mm (default 1800).")
    parser.add_argument("--test-slab-thickness", type=float, default=20.0,
                        help="Test slab thickness in mm (default 20).")
    parser.add_argument("--slab-buffer-factor", type=float, default=1.25,
                        help="Surplus factor for 'auto' slab count (default 1.25).")
    parser.add_argument("--strategy", action="append", default=None,
                        help=(
                            "Strategy to add to options_requested. Repeatable. "
                            "Defaults to ['balanced'] when omitted."
                        ))
    parser.add_argument("--random-seed", type=int, default=42,
                        help="random_seed value for natural_random (default 42).")
    parser.add_argument("--oda-path", default=None,
                        help="Path to the ODA File Converter executable "
                             "(only needed for .dwg input).")
    parser.add_argument("--conversion-backend", default="auto",
                        choices=["auto", "oda", "none"],
                        help="DWG conversion backend (default: auto).")
    return parser.parse_args(argv)


def _parse_slab_count(raw: str) -> int | str:
    """`--test-slab-count` is either the literal 'auto' or an integer."""
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

    test_slab_spec: SlabInventorySpec | None = None
    if args.include_test_slabs:
        test_slab_spec = SlabInventorySpec(
            count=_parse_slab_count(args.test_slab_count),
            width=args.test_slab_width,
            height=args.test_slab_height,
            thickness=args.test_slab_thickness,
            buffer_factor=args.slab_buffer_factor,
        )

    try:
        payload = build_project_input_dict(
            args.cad,
            project_id=args.project_id,
            project_type=args.project_type,
            test_slab_spec=test_slab_spec,
            options_requested=args.strategy,
            random_seed=args.random_seed,
            conversion_backend=args.conversion_backend,
            oda_path=args.oda_path,
        )
    except CADConversionError as exc:
        print(f"CAD conversion error: {exc}", file=sys.stderr)
        return 2
    except CADIntakeError as exc:
        print(f"CAD intake error: {exc}", file=sys.stderr)
        return 2

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2))

    slab_count = len(payload["slabs"])
    hole_count = len(payload["layout"]["holes"])
    source = payload["layout"]["source_file"]
    print(f"Wrote {args.out}")
    print(f"  project_id: {payload['project_id']}")
    print(f"  source: {source['type']}")
    if source.get("converted_dxf_path"):
        print(f"  converted DXF: {source['converted_dxf_path']}")
    print(f"  boundary vertices: {len(payload['layout']['boundary'])}")
    print(f"  holes: {hole_count}")
    print(f"  slabs: {slab_count}" + ("" if slab_count else "  ← fill these in before running the engine"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
