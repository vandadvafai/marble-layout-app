"""Project-scoped persistent storage for calibration records.

Layout:

    <APP_DATA_DIR>/projects/<session_id>/
        meta.json                # project name, created_at, excel filename
        clean_slabs.json         # standardized inventory (usable_*)
        calibrations.json        # list of CalibrationRecord dicts
        original_images/         # raw slab photos as uploaded
        calibrated_images/       # perspective-corrected outputs
        excel/                   # the operator's original .xlsx

Design decisions:

* One project per active session (matches V1 UX). The most recently
  written directory wins on server restart.
* No filesystem lock. V1 is single-operator; multi-writer will need
  a session lock file when we get there.
* Every mutation persists atomically — write to a temp file next to
  the target and rename. Keeps the on-disk state consistent if the
  process dies mid-write.
* Session id is a UUID. Uniqueness gives us collision safety across
  concurrent uploads and makes filenames safe.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from placement_engine.calibration.models import (
    CalibrationRecord, CalibrationStatus,
)


PROJECTS_SUBDIR = "projects"
CALIBRATIONS_FILE = "calibrations.json"
CLEAN_SLABS_FILE = "clean_slabs.json"
META_FILE = "meta.json"


@dataclass(frozen=True)
class ProjectPaths:
    session_id: str
    root: Path
    original_images: Path
    calibrated_images: Path
    excel: Path
    meta_file: Path
    calibrations_file: Path
    clean_slabs_file: Path


def new_project(
    projects_root: Path, *, session_id: str | None = None,
) -> ProjectPaths:
    """Create a fresh project directory under ``projects_root``.

    ``session_id`` is picked automatically (UUID4) unless a caller
    supplies one. Every subdirectory is created eagerly so the
    upload handler can start writing straight away.
    """
    session_id = session_id or str(uuid.uuid4())
    root = projects_root / session_id
    paths = ProjectPaths(
        session_id=session_id,
        root=root,
        original_images=root / "original_images",
        calibrated_images=root / "calibrated_images",
        excel=root / "excel",
        meta_file=root / META_FILE,
        calibrations_file=root / CALIBRATIONS_FILE,
        clean_slabs_file=root / CLEAN_SLABS_FILE,
    )
    for p in (
        root, paths.original_images, paths.calibrated_images, paths.excel,
    ):
        p.mkdir(parents=True, exist_ok=True)
    return paths


def _atomic_write_text(target: Path, text: str) -> None:
    """Write ``text`` to ``target`` atomically.

    Uses ``mkstemp`` in the same directory (so ``os.replace`` is a
    real atomic rename on the same filesystem). Guarantees that
    readers never see a truncated file.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=target.parent, prefix=".tmp-", suffix=target.suffix,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def save_records(
    paths: ProjectPaths, records: list[CalibrationRecord],
) -> None:
    payload = {
        "version": 1,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "records": [rec.to_dict() for rec in records],
    }
    _atomic_write_text(
        paths.calibrations_file,
        json.dumps(payload, ensure_ascii=False, indent=2),
    )


def load_records(paths: ProjectPaths) -> list[CalibrationRecord]:
    if not paths.calibrations_file.exists():
        return []
    payload = json.loads(paths.calibrations_file.read_text(encoding="utf-8"))
    return [
        CalibrationRecord.from_dict(r) for r in payload.get("records", [])
    ]


def save_meta(paths: ProjectPaths, meta: dict[str, Any]) -> None:
    _atomic_write_text(
        paths.meta_file,
        json.dumps(meta, ensure_ascii=False, indent=2),
    )


def load_meta(paths: ProjectPaths) -> dict[str, Any]:
    if not paths.meta_file.exists():
        return {}
    return json.loads(paths.meta_file.read_text(encoding="utf-8"))


def save_standardized_inventory(
    paths: ProjectPaths, records: list[CalibrationRecord],
    *, source_meta: dict[str, Any] | None = None,
) -> None:
    """Serialise the approved slabs into a ``clean_slabs.json`` the
    existing inventory loader can consume.

    Records with status != ``APPROVED`` are OMITTED. Layout Helper
    must only see slabs the operator vouched for.
    """
    approved = [r for r in records if r.calibration_status == CalibrationStatus.APPROVED]
    payload: dict[str, Any] = {
        "source": source_meta or {},
        "policy_version": records[0].factory_policy_version if records else "1.0",
        "record_count": len(approved),
        "records": [
            _inventory_record(r) for r in approved
        ],
    }
    _atomic_write_text(
        paths.clean_slabs_file,
        json.dumps(payload, ensure_ascii=False, indent=2),
    )


def _inventory_record(r: CalibrationRecord) -> dict[str, Any]:
    """Standardized-inventory row consumed by ``load_inventory``.

    Historically ``width_mm`` / ``height_mm`` on a slab meant the
    PHYSICAL slab. Under the new policy Layout Helper reads
    ``usable_width_mm`` / ``usable_height_mm`` because those are
    the dimensions it may plan against. We publish BOTH so
    downstream consumers can inspect either value; legacy loaders
    fall back to width_mm/height_mm which we intentionally point
    at the USABLE size to prevent Layout Helper from double-
    trimming.
    """
    return {
        "slab_id": r.slab_id,
        "serial_number": None,
        "slab_number": None,
        "item_code": None,
        "excel_width_mm": r.excel_width_mm,
        "excel_height_mm": r.excel_height_mm,
        "usable_width_mm": r.usable_width_mm,
        "usable_height_mm": r.usable_height_mm,
        "width_mm": r.usable_width_mm,
        "height_mm": r.usable_height_mm,
        "area_m2": (r.usable_width_mm * r.usable_height_mm) / 1_000_000.0,
        "calculated_area_m2": (r.usable_width_mm * r.usable_height_mm) / 1_000_000.0,
        "image_path": r.calibrated_image_path,
        "calibration_status": r.calibration_status.value,
        "calibration_confidence": r.calibration_confidence,
        "factory_policy_version": r.factory_policy_version,
        "source_type": r.source_type.value,
        "warnings": list(r.warnings),
    }


def most_recent_project(projects_root: Path) -> ProjectPaths | None:
    """Restart-survives helper: return the newest project directory
    under ``projects_root``, or None when the folder doesn't exist
    yet."""
    if not projects_root.exists():
        return None
    candidates = [
        p for p in projects_root.iterdir()
        if p.is_dir() and (p / CALIBRATIONS_FILE).exists()
    ]
    if not candidates:
        return None
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    return ProjectPaths(
        session_id=latest.name,
        root=latest,
        original_images=latest / "original_images",
        calibrated_images=latest / "calibrated_images",
        excel=latest / "excel",
        meta_file=latest / META_FILE,
        calibrations_file=latest / CALIBRATIONS_FILE,
        clean_slabs_file=latest / CLEAN_SLABS_FILE,
    )


def clear_project(paths: ProjectPaths) -> None:
    """Nuke the project directory. Used by "Start new project"."""
    if paths.root.exists():
        shutil.rmtree(paths.root, ignore_errors=True)
