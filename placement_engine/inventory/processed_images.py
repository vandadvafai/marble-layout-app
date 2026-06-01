"""Attach processed (green-box cropped) image paths to an `Inventory`.

The image-intake layer writes ``image_metadata.json`` describing the
crop result per slab. This module reads that file and patches the
matching `InventorySlab.processed_image_path` field — strictly
additive; nothing in the existing inventory is overwritten.

Match priority is **slab_id first, then slab_number**, which keeps the
mapping unambiguous on the common case (every record has a unique
slab_id) but still works when the ingestion JSON and the
image-metadata JSON only share a slab_number column (e.g. two
batches reconciled by-hand later).

Crops where `green_box_detected` is false are intentionally ignored —
those records point back at the original image, and we'd rather have
the smoke preview fall through to the original explicitly than
silently pretend a non-existent crop exists.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from placement_engine.inventory.model import Inventory

logger = logging.getLogger(__name__)


def load_processed_image_metadata(
    metadata_path: str | Path,
) -> dict[str, Path]:
    """Read ``image_metadata.json`` and return ``slab_id → processed_path``.

    Only entries with ``green_box_detected == True`` are returned —
    records that fell back to the original photo are dropped here so
    they don't shadow the original path when attached.

    Paths in the metadata are taken as-written. Caller is responsible
    for checking whether the file actually exists (``attach_processed_images``
    does this).
    """
    path = Path(metadata_path)
    if not path.exists():
        raise FileNotFoundError(f"image_metadata.json not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, Path] = {}
    for img in data.get("images", []):
        if not img.get("green_box_detected"):
            continue
        slab_id = img.get("slab_id")
        processed = img.get("processed_image_path")
        if slab_id and processed:
            out[str(slab_id)] = Path(processed)
    return out


def attach_processed_images(
    inventory: Inventory,
    metadata_path: str | Path,
) -> int:
    """Patch each `InventorySlab.processed_image_path` from the metadata file.

    Returns the number of slabs that received a processed image. Match
    priority: slab_id, then slab_number. Files that no longer exist on
    disk are skipped (the field stays ``None`` so the consumer falls
    back cleanly to the original photo).
    """
    metadata_path = Path(metadata_path)
    if not metadata_path.exists():
        raise FileNotFoundError(f"image_metadata.json not found: {metadata_path}")
    data = json.loads(metadata_path.read_text(encoding="utf-8"))

    by_slab_id: dict[str, Path] = {}
    by_slab_number: dict[str, Path] = {}
    for img in data.get("images", []):
        if not img.get("green_box_detected"):
            continue
        processed = img.get("processed_image_path")
        if not processed:
            continue
        path = Path(processed)
        slab_id = img.get("slab_id")
        if slab_id:
            by_slab_id[str(slab_id)] = path
        slab_number = img.get("slab_number")
        if slab_number is not None:
            by_slab_number[str(slab_number)] = path

    attached = 0
    for slab in inventory.slabs:
        candidate: Path | None = None
        if slab.slab_id in by_slab_id:
            candidate = by_slab_id[slab.slab_id]
        elif slab.slab_number and slab.slab_number in by_slab_number:
            candidate = by_slab_number[slab.slab_number]
        if candidate is not None and candidate.exists():
            slab.processed_image_path = candidate
            attached += 1
        elif candidate is not None:
            logger.warning(
                "Processed image path recorded but file missing for %s: %s",
                slab.slab_id, candidate,
            )
    return attached
