"""Batch driver: consume ``clean_slabs.json``, crop the green usable area,
write processed images + metadata + report.

The processor is the only place in this subpackage that touches disk.
``green_box`` stays pure-NumPy so it can be tested in isolation.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import cv2

from placement_engine.image_intake.green_box import (
    GreenBox,
    crop_inside_green_box,
    detect_green_box,
)
from placement_engine.inventory import Inventory, InventorySlab, load_inventory

logger = logging.getLogger(__name__)


@dataclass
class ImageMetadata:
    """Per-image record emitted into ``image_metadata.json``.

    When the green box could not be detected, ``processed_image_path``
    falls back to the original — the placement engine can still display
    the slab, just without the usable-area crop. ``crop_*`` fields are
    set to ``None`` in that case.
    """

    slab_id: str
    slab_number: str | None
    original_image_path: str
    processed_image_path: str
    green_box_detected: bool
    crop_x: int | None
    crop_y: int | None
    crop_width: int | None
    crop_height: int | None
    confidence_score: float | None
    warnings: list[str] = field(default_factory=list)


@dataclass
class ProcessingResult:
    inventory_source: Path
    output_dir: Path
    images: list[ImageMetadata]

    @property
    def detected_count(self) -> int:
        return sum(1 for m in self.images if m.green_box_detected)

    @property
    def warning_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for m in self.images:
            for w in m.warnings:
                counts[w] = counts.get(w, 0) + 1
        return counts


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _sanitize_for_filename(value: str) -> str:
    """Replace filesystem-unsafe characters with ``_``.

    Slab IDs can contain `/`, spaces, or Persian chars; CSVs and Web
    UIs are happier with bare ASCII filenames. Keeps `-`, `_`, and
    alphanumerics; everything else becomes `_`.
    """
    return re.sub(r"[^\w\-]+", "_", value).strip("_") or "slab"


def _processed_filename(slab: InventorySlab, ext: str) -> str:
    """Build a deterministic processed-image filename for a slab.

    Uses the source image stem when possible so the relationship with
    the original photo is obvious; otherwise falls back to a sanitised
    slab_id.
    """
    if slab.image_path is not None:
        return f"{slab.image_path.stem}{ext}"
    return f"{_sanitize_for_filename(slab.slab_id)}{ext}"


def _process_one(
    slab: InventorySlab,
    processed_dir: Path,
) -> ImageMetadata:
    warnings: list[str] = []
    if not slab.image_available or slab.image_path is None:
        warnings.append("original_image_not_available")
        return ImageMetadata(
            slab_id=slab.slab_id,
            slab_number=None,  # populated by the caller from the inventory record
            original_image_path=str(slab.image_path) if slab.image_path else "",
            processed_image_path="",
            green_box_detected=False,
            crop_x=None,
            crop_y=None,
            crop_width=None,
            crop_height=None,
            confidence_score=None,
            warnings=warnings,
        )

    image_bgr = cv2.imread(str(slab.image_path))
    if image_bgr is None:
        # cv2 returns None on unreadable / corrupted files.
        warnings.append("image_unreadable")
        return ImageMetadata(
            slab_id=slab.slab_id,
            slab_number=None,
            original_image_path=str(slab.image_path),
            processed_image_path=str(slab.image_path),
            green_box_detected=False,
            crop_x=None, crop_y=None, crop_width=None, crop_height=None,
            confidence_score=None,
            warnings=warnings,
        )

    box: GreenBox | None = detect_green_box(image_bgr)
    if box is None:
        warnings.append("green_box_not_found")
        return ImageMetadata(
            slab_id=slab.slab_id,
            slab_number=None,
            original_image_path=str(slab.image_path),
            # Per spec: keep the original image path so previews still work.
            processed_image_path=str(slab.image_path),
            green_box_detected=False,
            crop_x=None, crop_y=None, crop_width=None, crop_height=None,
            confidence_score=None,
            warnings=warnings,
        )

    cropped = crop_inside_green_box(image_bgr, box)
    # Use the source extension verbatim so JPEGs stay JPEGs (smaller
    # files for the long photo previews).
    ext = slab.image_path.suffix.lower() or ".jpg"
    out_path = processed_dir / _processed_filename(slab, ext)
    cv2.imwrite(str(out_path), cropped)

    return ImageMetadata(
        slab_id=slab.slab_id,
        slab_number=None,
        original_image_path=str(slab.image_path),
        processed_image_path=str(out_path),
        green_box_detected=True,
        crop_x=int(box.x),
        crop_y=int(box.y),
        crop_width=int(box.width),
        crop_height=int(box.height),
        confidence_score=round(float(box.confidence), 4),
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# top-level driver
# ---------------------------------------------------------------------------


def process_inventory(
    clean_slabs_json: str | Path,
    output_dir: str | Path,
) -> ProcessingResult:
    """Process every slab in the inventory; do not mutate the source JSON.

    Raises ``FileNotFoundError`` if the inventory JSON is missing.
    Individual image failures (unreadable file, no green box) never
    raise — they end up as warnings on the per-image metadata record.
    """
    out_dir = Path(output_dir)
    processed_dir = out_dir / "processed_images"
    processed_dir.mkdir(parents=True, exist_ok=True)

    inventory: Inventory = load_inventory(clean_slabs_json)
    # We need slab_number for the metadata records, but load_inventory's
    # InventorySlab doesn't carry it. Re-read the JSON once to look it
    # up by slab_id without re-implementing the loader.
    slab_numbers = _slab_numbers_by_id(Path(clean_slabs_json))

    images: list[ImageMetadata] = []
    for slab in inventory.slabs:
        meta = _process_one(slab, processed_dir)
        meta.slab_number = slab_numbers.get(slab.slab_id)
        images.append(meta)

    return ProcessingResult(
        inventory_source=Path(clean_slabs_json),
        output_dir=out_dir,
        images=images,
    )


def _slab_numbers_by_id(clean_slabs_json: Path) -> dict[str, str | None]:
    """Quick lookup of slab_number per slab_id from the source JSON."""
    data = json.loads(clean_slabs_json.read_text(encoding="utf-8"))
    out: dict[str, str | None] = {}
    for rec in data.get("records", []):
        slab_id = rec.get("slab_id")
        if slab_id is None:
            continue
        out[str(slab_id)] = rec.get("slab_number")
    return out


# ---------------------------------------------------------------------------
# writers
# ---------------------------------------------------------------------------


def write_outputs(result: ProcessingResult) -> dict[str, Path]:
    """Persist ``image_metadata.json`` and ``image_report.txt``.

    Processed images are already on disk (written by `_process_one`).
    """
    out_dir = result.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    meta_path = out_dir / "image_metadata.json"
    report_path = out_dir / "image_report.txt"

    payload: dict[str, Any] = {
        "inventory_source": str(result.inventory_source),
        "output_dir": str(out_dir),
        "total_images": len(result.images),
        "detected_count": result.detected_count,
        "warning_counts": result.warning_counts,
        "images": [asdict(m) for m in result.images],
    }
    meta_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    report_path.write_text(_build_report(result), encoding="utf-8")
    return {"metadata": meta_path, "report": report_path}


def _build_report(result: ProcessingResult) -> str:
    total = len(result.images)
    detected = result.detected_count
    counts = result.warning_counts
    lines: list[str] = []
    lines.append("Image intake report (V1 green-box crop)")
    lines.append("=" * 60)
    lines.append(f"Inventory source : {result.inventory_source}")
    lines.append(f"Output directory : {result.output_dir}")
    lines.append("")
    lines.append(f"Images processed         : {total}")
    lines.append(f"Green boxes detected     : {detected} / {total}")
    lines.append(f"Green boxes not detected : {total - detected} / {total}")
    lines.append("")
    lines.append("Warning counts:")
    if counts:
        for w, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"  {w:30s} {n}")
    else:
        lines.append("  (no warnings)")
    lines.append("")
    lines.append("Per-image detail:")
    for m in result.images:
        marker = "✓" if m.green_box_detected else "·"
        crop = (
            f"{m.crop_width}x{m.crop_height}@({m.crop_x},{m.crop_y}) "
            f"conf={m.confidence_score}"
            if m.green_box_detected
            else "(no crop)"
        )
        warn = f"  warn={','.join(m.warnings)}" if m.warnings else ""
        lines.append(f"  {marker} {m.slab_id:30s} {crop}{warn}")
    return "\n".join(lines) + "\n"
