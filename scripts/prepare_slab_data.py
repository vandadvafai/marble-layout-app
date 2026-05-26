#!/usr/bin/env python3
"""V1 CLI: convert an ERP Excel export + image folder into clean slab data.

Example:

    python scripts/prepare_slab_data.py \\
        --excel  path/to/export.xlsx \\
        --images path/to/images_folder \\
        --output outputs/slab_ingestion

Output:

    <output>/clean_slabs.csv        — flat per-slab CSV
    <output>/clean_slabs.json       — same data + metadata, structured
    <output>/ingestion_report.txt   — what was mapped / matched / warned

This script is a thin wrapper around `placement_engine.slab_intake`.
Edit the column map there to keep up with ERP header changes.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Allow `python scripts/prepare_slab_data.py` from the repo root without
# installing the package.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from placement_engine.slab_intake import (  # noqa: E402
    ingest_slab_export,
    write_outputs,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="V1 ERP Excel + image folder → clean slab CSV/JSON.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--excel",
        required=True,
        type=Path,
        help="Path to the ERP Excel export (.xlsx).",
    )
    p.add_argument(
        "--images",
        required=True,
        type=Path,
        help="Path to the slab image folder (scanned recursively).",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/slab_ingestion"),
        help="Output directory. Default: outputs/slab_ingestion",
    )
    p.add_argument(
        "--sheet",
        default=None,
        help=(
            "Sheet name to read. If omitted and the workbook has multiple "
            "sheets, the sheet with the most non-empty rows is used."
        ),
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
    log = logging.getLogger("prepare_slab_data")

    log.info("Excel  : %s", args.excel)
    log.info("Images : %s", args.images)
    log.info("Output : %s", args.output)

    result = ingest_slab_export(
        excel_path=args.excel,
        image_dir=args.images,
        sheet_name=args.sheet,
    )

    log.info(
        "Loaded %d rows from sheet %r; indexed %d image files.",
        len(result.records),
        result.sheet_name,
        len(result.image_index),
    )

    paths = write_outputs(result, args.output)
    log.info("Wrote CSV    : %s", paths["csv"])
    log.info("Wrote JSON   : %s", paths["json"])
    log.info("Wrote report : %s", paths["report"])

    # Concise terminal summary (visible without --verbose).
    matched = sum(1 for r in result.records if r.image_found)
    total = len(result.records)
    counts = result.warning_counts()
    print()
    print("=" * 60)
    print(f"Records : {total}")
    print(f"Images  : {matched} / {total} matched")
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
