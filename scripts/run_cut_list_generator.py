#!/usr/bin/env python3
"""V1 cut-list generator — layout.json → cut_list.json + summary + preview.

Reads a `layout.json` produced by ``scripts/run_layout_generator.py``
and writes the manufacturing cut list: a 1:1 record per layout piece,
classified as ``full`` / ``edge`` / ``hole`` / ``sliver``, plus a
fabrication-style preview PNG and a one-page summary JSON.

No packers, no inventory assignment, no waste optimisation — this is
just the formalisation step between layout and any future slab
assignment.

Example:

    python3 scripts/run_cut_list_generator.py \\
        --layout outputs/layouts/demo_l_shape_floor/layout.json \\
        --output outputs/cut_lists/demo_l_shape_floor

Output:

    <output>/cut_list.json
    <output>/cut_list_summary.json
    <output>/cut_list_preview.png
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from placement_engine.cut_list import (  # noqa: E402
    build_cut_list,
    render_cut_list_preview,
    write_cut_list_json,
    write_summary_json,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--layout", required=True, type=Path,
                   help="Path to layout.json produced by the layout generator.")
    p.add_argument("--output", required=True, type=Path,
                   help="Output directory.")
    p.add_argument("--no-preview", action="store_true",
                   help="Skip the PNG preview (write JSON files only).")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("run_cut_list_generator")

    cut_list = build_cut_list(args.layout)

    # Read boundary + holes from the source layout so the preview can
    # draw the floor outline. The cut-list package itself stays free
    # of layout-schema knowledge.
    layout_dict = json.loads(args.layout.read_text(encoding="utf-8"))
    target = layout_dict.get("target", {})
    boundary = [tuple(pt) for pt in target.get("boundary", [])]
    holes = [
        [tuple(pt) for pt in hole] for hole in target.get("holes", [])
    ]

    # ---- terminal summary --------------------------------------------------
    summary = cut_list.summary
    print()
    print("=" * 70)
    print(f"Source layout : {cut_list.source_layout_path}")
    print(f"Target        : {cut_list.target_name} ({cut_list.target_id})")
    print(f"Tile size     : {cut_list.tile_width_mm:.0f} × "
          f"{cut_list.tile_height_mm:.0f} mm")
    print()
    print(f"  total pieces             : {summary.total_pieces}")
    print(f"  full pieces              : {summary.full_pieces}")
    print(f"  edge pieces              : {summary.edge_pieces}")
    print(f"  hole / internal-cut      : {summary.hole_pieces}")
    print(f"  pieces with internal cuts: {summary.pieces_with_internal_cuts}")
    print(f"  sliver pieces            : {summary.sliver_pieces}")
    print(f"  total area               : {summary.total_area_m2:.4f} m²")
    print("=" * 70)

    # ---- artefacts ---------------------------------------------------------
    args.output.mkdir(parents=True, exist_ok=True)
    cl_path = write_cut_list_json(cut_list, args.output / "cut_list.json")
    sm_path = write_summary_json(cut_list, args.output / "cut_list_summary.json")
    log.info("Wrote cut list JSON  : %s", cl_path)
    log.info("Wrote summary JSON   : %s", sm_path)

    if not args.no_preview:
        png_path = render_cut_list_preview(
            cut_list, args.output / "cut_list_preview.png",
            boundary=boundary, holes=holes,
        )
        log.info("Wrote preview PNG    : %s", png_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
