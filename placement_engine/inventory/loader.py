"""Load `clean_slabs.json` into a typed `Inventory`."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from placement_engine.inventory.model import Inventory, InventorySlab

logger = logging.getLogger(__name__)


def _coerce_dims(rec: dict[str, Any]) -> tuple[float | None, float | None]:
    """Return ``(width_mm, height_mm)`` as floats, or ``(None, None)``."""
    w = rec.get("width_mm")
    h = rec.get("height_mm")
    try:
        wf = float(w) if w is not None else None
        hf = float(h) if h is not None else None
    except (TypeError, ValueError):
        return None, None
    if wf is None or hf is None or wf <= 0 or hf <= 0:
        return None, None
    return wf, hf


def _resolve_image(
    rec: dict[str, Any], base_dir: Path
) -> tuple[Path | None, bool, str | None]:
    """Resolve `image_path`, check it exists, and explain placeholder use.

    Relative paths in ``clean_slabs.json`` are resolved against the
    directory of the JSON file (so an inventory file moved together with
    its photos still works). The fallback `None` keeps the previous
    behaviour clean.
    """
    raw = rec.get("image_path")
    if not raw:
        return None, False, "no_image_path_in_clean_slabs"
    path = Path(raw)
    if not path.is_absolute():
        # Try the path as recorded (relative to CWD) first, then relative
        # to the JSON file's directory. Use whichever exists.
        if not path.exists():
            candidate = (base_dir / path).resolve()
            if candidate.exists():
                path = candidate
    if path.exists():
        return path, True, None
    return path, False, f"image_file_missing: {path}"


def load_inventory(clean_slabs_json: str | Path) -> Inventory:
    """Parse a ``clean_slabs.json`` file into a typed `Inventory`.

    Records with non-positive or missing dimensions are dropped from
    ``inventory.slabs`` and preserved verbatim in ``skipped_records``.
    Missing or broken image links are flagged on each `InventorySlab`
    via ``image_available`` and ``image_placeholder_reason`` — they
    never block loading.
    """
    json_path = Path(clean_slabs_json)
    if not json_path.exists():
        raise FileNotFoundError(f"clean_slabs.json not found: {json_path}")
    data = json.loads(json_path.read_text(encoding="utf-8"))
    base_dir = json_path.parent.resolve()

    raw_records = data.get("records")
    if raw_records is None:
        raise ValueError(
            f"{json_path} is missing the required 'records' key — "
            "is this the clean_slabs.json produced by prepare_slab_data.py?"
        )

    slabs: list[InventorySlab] = []
    skipped: list[dict] = []
    for rec in raw_records:
        wf, hf = _coerce_dims(rec)
        if wf is None or hf is None:
            skipped.append(rec)
            continue
        image_path, image_available, placeholder = _resolve_image(rec, base_dir)
        slabs.append(
            InventorySlab(
                slab_id=str(rec["slab_id"]),
                serial_number=(rec.get("serial_number") or None),
                slab_number=(
                    str(rec["slab_number"]) if rec.get("slab_number") is not None else None
                ),
                item_code=(rec.get("item_code") or None),
                width_mm=wf,
                height_mm=hf,
                area_m2=(
                    float(rec["area_m2"]) if rec.get("area_m2") is not None else None
                ),
                calculated_area_m2=(
                    float(rec["calculated_area_m2"])
                    if rec.get("calculated_area_m2") is not None
                    else None
                ),
                image_path=image_path,
                image_available=image_available,
                image_placeholder_reason=placeholder,
                source_excel_row=rec.get("source_excel_row"),
                ingestion_warnings=list(rec.get("warnings", [])),
            )
        )

    logger.info(
        "Inventory loaded from %s: %d usable slabs, %d skipped.",
        json_path, len(slabs), len(skipped),
    )
    return Inventory(slabs=slabs, source_json=json_path, skipped_records=skipped)
