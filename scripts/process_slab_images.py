#!/usr/bin/env python3
"""V1 image intake: detect the green usable-area rectangle, crop, save.

Example:

    python3 scripts/process_slab_images.py \\
        --inventory outputs/slab_ingestion_test/clean_slabs.json \\
        --output    outputs/image_intake

Output:

    <output>/processed_images/<stem>.jpg   one per detected slab
    <output>/image_metadata.json           per-image crop bbox + confidence
    <output>/image_report.txt              human-readable summary

Slabs whose green rectangle could not be detected keep their original
image path in metadata — they are NOT skipped, just flagged with the
warning ``green_box_not_found``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from placement_engine.image_intake import (  # noqa: E402
    process_inventory,
    write_outputs,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="V1 slab-photo image intake (detect green usable-area, crop).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--inventory",
        required=True,
        type=Path,
        help="Path to clean_slabs.json (output of prepare_slab_data.py).",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/image_intake"),
        help="Output directory. Default: outputs/image_intake",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug-level logging.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("process_slab_images")
    log.info("Inventory: %s", args.inventory)
    log.info("Output   : %s", args.output)

    result = process_inventory(args.inventory, args.output)
    paths = write_outputs(result)

    log.info("Wrote metadata: %s", paths["metadata"])
    log.info("Wrote report  : %s", paths["report"])

    # --- terminal summary ---
    total = len(result.images)
    print()
    print("=" * 60)
    print(f"Images processed         : {total}")
    print(f"Green boxes detected     : {result.detected_count} / {total}")
    print(f"Green boxes not detected : {total - result.detected_count} / {total}")
    counts = result.warning_counts
    if counts:
        print("Warnings:")
        for w, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {w:30s} {n}")
    else:
        print("Warnings: none")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
