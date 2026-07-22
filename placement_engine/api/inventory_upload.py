"""Persistent per-project storage for the Step-3 upload flow.

The upload endpoint hands us the Excel + slab photos; this module:

  1. Creates a fresh project directory under ``AVANDAD_DATA_DIR/projects/``.
  2. Writes the raw files to disk.
  3. Runs the slab-intake pipeline to normalise the Excel.
  4. Runs the calibration pipeline (green boundary → scanned crop →
     raw photo → no photo) on every slab.
  5. Persists ``calibrations.json`` + ``clean_slabs.json``.
  6. Registers the project as the "active" one.

Restart survival: on process start, ``rehydrate_active_upload()``
walks ``AVANDAD_DATA_DIR/projects/`` and picks the most-recently
modified project directory. Records are re-read from
``calibrations.json`` so an operator restarting the backend keeps
their approved slabs.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from placement_engine.api.app_paths import ensure_dirs, resolve_app_paths
from placement_engine.calibration import (
    CalibrationRecord,
    CalibrationStatus,
    FACTORY_POLICY_VERSION,
    SlabCalibrationInput,
    SlabCorners,
    SourceType,
    apply_manual_corners,
    calibrate_batch,
    calibrate_slab,
    count_by_status,
    migrate_legacy_green_box_records,
)
from placement_engine.calibration.storage import (
    ProjectPaths,
    load_meta,
    load_records,
    most_recent_project,
    new_project,
    save_meta,
    save_records,
    save_standardized_inventory,
)
from placement_engine.slab_intake.pipeline import (
    SlabIngestionResult, SlabRecord,
    ingest_slab_export,
)


@dataclass
class UploadSession:
    """One uploaded inventory session (project).

    ``clean_slabs_path`` is the standardized inventory the API
    matcher / DXF writer / client PNG all consume. It contains ONLY
    approved slabs — Layout Helper never sees unreviewed rows.
    """

    session_id: str
    uploaded_at: str
    excel_filename: str
    image_count: int
    project_paths: ProjectPaths
    clean_slabs_path: Path
    summary: dict[str, Any]
    calibration_records: list[CalibrationRecord]


# Module-level slot for the active session. V1 supports one uploaded
# inventory at a time; the on-disk project directory is the durable
# store, this variable is a hot cache.
_ACTIVE_UPLOAD: UploadSession | None = None


def get_active_upload() -> UploadSession | None:
    """Return the currently-active uploaded inventory.

    On the very first call after a fresh boot, tries to rehydrate
    from the most recent project directory on disk so restarts
    don't erase the operator's approvals.
    """
    global _ACTIVE_UPLOAD
    if _ACTIVE_UPLOAD is not None:
        return _ACTIVE_UPLOAD
    _ACTIVE_UPLOAD = _rehydrate_from_disk()
    return _ACTIVE_UPLOAD


def clear_active_upload() -> None:
    """Drop the active upload and delete its project directory.
    Called by the frontend's "Start new project" action so no stale
    slab files linger between projects."""
    global _ACTIVE_UPLOAD
    session = _ACTIVE_UPLOAD
    _ACTIVE_UPLOAD = None
    if session is not None and session.project_paths.root.exists():
        shutil.rmtree(session.project_paths.root, ignore_errors=True)


def _rehydrate_from_disk() -> UploadSession | None:
    paths = resolve_app_paths()
    ensure_dirs(paths)
    projects_root = paths.root / "projects"
    latest = most_recent_project(projects_root)
    if latest is None:
        return None
    records = load_records(latest)
    if not records:
        return None
    meta = load_meta(latest)
    raw_slab_ids = {
        r.slab_id for r in records if r.source_type == SourceType.RAW_PHOTO
    }
    migrate_legacy_green_box_records(latest.root, records)
    if any(
        r.slab_id in raw_slab_ids and r.source_type == SourceType.GREEN_BOUNDARY
        for r in records
    ):
        save_records(latest, records)
        save_standardized_inventory(
            latest, records,
            source_meta={
                "excel_filename": meta.get("excel_filename") or "",
                "source_type": "step3_upload",
            },
        )
    summary = meta.get("summary") or _summary_from_records(records)
    return UploadSession(
        session_id=latest.session_id,
        uploaded_at=meta.get("uploaded_at") or "",
        excel_filename=meta.get("excel_filename") or "",
        image_count=meta.get("image_count") or 0,
        project_paths=latest,
        clean_slabs_path=latest.clean_slabs_file,
        summary=summary,
        calibration_records=records,
    )


def _summary_from_records(
    records: list[CalibrationRecord],
) -> dict[str, Any]:
    counts = count_by_status(records)
    return {
        "total_rows": len(records),
        "valid_slabs": counts["approved"],
        "invalid_slabs": counts["rejected"] + counts["missing_photo"],
        "linked_photos": sum(1 for r in records if r.original_image_path),
        "unmatched_photos": [],
        "slabs_without_photos": [
            r.slab_id for r in records if r.original_image_path is None
        ],
        "mapped_columns": {},
        "unmapped_columns": [],
        "warning_counts": {},
        "preview": [],
        "calibration": counts,
    }


def process_upload(
    excel_bytes: bytes, excel_filename: str,
    images: list[tuple[str, bytes]],
) -> UploadSession:
    """Persist a fresh project on disk and run the calibration pipeline.

    The previous active project is deleted first so operators
    don't accidentally mix slabs between two Excel exports.
    """
    # Reset any prior state before writing new files.
    clear_active_upload()

    paths = resolve_app_paths()
    ensure_dirs(paths)
    projects_root = paths.root / "projects"
    projects_root.mkdir(parents=True, exist_ok=True)
    project = new_project(projects_root)

    excel_path = project.excel / _safe_filename(excel_filename, "inventory.xlsx")
    excel_path.write_bytes(excel_bytes)

    written_images: list[Path] = []
    for original_name, payload in images:
        safe_name = _safe_filename(Path(original_name).name, "photo.jpg")
        dest = project.original_images / safe_name
        dest.write_bytes(payload)
        written_images.append(dest)

    try:
        result = ingest_slab_export(
            excel_path=excel_path, image_dir=project.original_images,
            sheet_name=None,
        )
    except Exception:
        # Something in the Excel parse failed — clean up the empty
        # project directory before re-raising so we don't leak.
        shutil.rmtree(project.root, ignore_errors=True)
        raise

    calibration_inputs = _inputs_from_slab_records(result.records)
    calibration_records = calibrate_batch(
        calibration_inputs, project.calibrated_images,
    )

    save_records(project, calibration_records)
    save_standardized_inventory(
        project, calibration_records,
        source_meta={
            "excel_filename": excel_filename,
            "source_type": "step3_upload",
        },
    )

    summary = _build_summary(
        result, project.original_images, written_images,
        calibration_records,
    )
    meta = {
        "session_id": project.session_id,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "excel_filename": excel_filename,
        "image_count": len(written_images),
        "factory_policy_version": FACTORY_POLICY_VERSION,
        "summary": summary,
    }
    save_meta(project, meta)

    session = UploadSession(
        session_id=project.session_id,
        uploaded_at=meta["uploaded_at"],
        excel_filename=excel_filename,
        image_count=len(written_images),
        project_paths=project,
        clean_slabs_path=project.clean_slabs_file,
        summary=summary,
        calibration_records=calibration_records,
    )

    global _ACTIVE_UPLOAD
    _ACTIVE_UPLOAD = session
    return session


def _safe_filename(raw: str, fallback: str) -> str:
    """Strip directory components + any characters that might be
    interpreted by the shell. Deliberately conservative — the
    original stem still needs to survive so the photo matcher can
    key off it."""
    name = Path(raw).name.strip()
    if not name:
        return fallback
    if name.startswith("."):
        name = "_" + name
    return name


def _inputs_from_slab_records(
    records: list[SlabRecord],
) -> list[SlabCalibrationInput]:
    """Adapt the slab-intake pipeline's ``SlabRecord`` to the
    calibration pipeline's input tuple. Both live in the API
    process so we can afford one pass over the list."""
    out: list[SlabCalibrationInput] = []
    for rec in records:
        # Use whichever slab id survives the pipeline's fallbacks;
        # every SlabRecord has one after normalisation.
        slab_id = rec.slab_id or rec.serial_number or rec.item_code or ""
        if not slab_id:
            continue
        original = Path(rec.image_path) if rec.image_path else None
        if original is not None and not original.exists():
            original = None
        out.append(SlabCalibrationInput(
            slab_id=str(slab_id),
            excel_width_mm=float(rec.width_mm or 0.0),
            excel_height_mm=float(rec.height_mm or 0.0),
            original_image_path=original,
        ))
    return out


def _build_summary(
    result: SlabIngestionResult,
    image_dir: Path,
    written_images: list[Path],
    calibration_records: list[CalibrationRecord],
) -> dict[str, Any]:
    """Frontend-facing summary of the upload outcome.

    Same top-level shape as before so the existing Step-3 panel
    continues to render, plus a ``calibration`` block with per-
    status counts.
    """
    records = result.records
    linked = [r for r in records if r.image_found]
    missing_photo = [r for r in records if not r.image_found]

    matched_paths = {
        Path(r.image_path).resolve() for r in records if r.image_path
    }
    on_disk = {p.resolve() for p in written_images}
    unmatched = sorted(p.name for p in on_disk - matched_paths)

    preview = [
        {
            "slab_id": r.slab_id,
            "serial_number": r.serial_number,
            "item_code": r.item_code,
            "width_cm": r.width_cm,
            "height_cm": r.height_cm,
            "width_mm": r.width_mm,
            "height_mm": r.height_mm,
            "area_m2": r.area_m2,
            "image_found": r.image_found,
            "image_filename": (
                Path(r.image_path).name if r.image_path else None
            ),
            "warnings": list(r.warnings),
        }
        for r in records[:10]
    ]

    counts = count_by_status(calibration_records)

    return {
        "total_rows": len(records),
        "valid_slabs": counts["approved"],
        "invalid_slabs": len(records) - counts["approved"],
        "linked_photos": len(linked),
        "unmatched_photos": unmatched,
        "slabs_without_photos": [
            r.slab_id for r in missing_photo if r.slab_id
        ],
        "mapped_columns": result.mapped_columns,
        "unmapped_columns": result.unmapped_columns,
        "warning_counts": result.warning_counts(),
        "preview": preview,
        # Calibration counts drive the new Step-3 status buckets.
        "calibration": counts,
    }


# ---------------------------------------------------------------------------
# Calibration mutations — invoked by the API endpoints in routes.py
# ---------------------------------------------------------------------------


def update_manual_corners(
    slab_id: str, corners: SlabCorners,
) -> CalibrationRecord:
    """Apply operator-confirmed corners for one slab and persist."""
    session = get_active_upload()
    if session is None:
        raise LookupError("no active upload")
    for i, rec in enumerate(session.calibration_records):
        if rec.slab_id == slab_id:
            updated = apply_manual_corners(
                rec, corners, session.project_paths.calibrated_images,
            )
            session.calibration_records[i] = updated
            _persist_current_session(session)
            return updated
    raise LookupError(f"slab {slab_id!r} not in active upload")


def set_calibration_status(
    slab_id: str, status: CalibrationStatus,
) -> CalibrationRecord:
    """Force a calibration record into ``approved`` / ``rejected``.

    Used by the review UI's Approve / Reject buttons when the
    detected corners were already correct."""
    session = get_active_upload()
    if session is None:
        raise LookupError("no active upload")
    for i, rec in enumerate(session.calibration_records):
        if rec.slab_id == slab_id:
            rec.calibration_status = status
            if status == CalibrationStatus.APPROVED:
                rec.approved_at = datetime.now(timezone.utc).isoformat()
                rec.approved_by = "anonymous"
                # If the operator hit Approve without touching the
                # corners, adopt the detected corners as confirmed.
                if rec.confirmed_corners is None and rec.detected_corners is not None:
                    rec.confirmed_corners = rec.detected_corners
            else:
                rec.approved_at = None
                rec.approved_by = None
            session.calibration_records[i] = rec
            _persist_current_session(session)
            return rec
    raise LookupError(f"slab {slab_id!r} not in active upload")


def replace_slab_image(
    slab_id: str, filename: str, image_bytes: bytes,
) -> CalibrationRecord:
    """Swap a slab's source photo and re-run calibration from scratch.

    Used by the manual-review modal's "Replace image" action — the
    operator's new photo goes through the exact same classifier
    (green boundary → scanned crop → raw photo) every other slab
    does, so a replaced photo is judged by the same rules as one
    uploaded the normal way."""
    session = get_active_upload()
    if session is None:
        raise LookupError("no active upload")
    for i, rec in enumerate(session.calibration_records):
        if rec.slab_id == slab_id:
            safe_name = _safe_filename(filename, f"{slab_id}.jpg")
            dest = session.project_paths.original_images / safe_name
            dest.write_bytes(image_bytes)
            new_input = SlabCalibrationInput(
                slab_id=rec.slab_id,
                excel_width_mm=rec.excel_width_mm,
                excel_height_mm=rec.excel_height_mm,
                original_image_path=dest,
            )
            updated = calibrate_slab(
                new_input, session.project_paths.calibrated_images,
            )
            session.calibration_records[i] = updated
            _persist_current_session(session)
            return updated
    raise LookupError(f"slab {slab_id!r} not in active upload")


def _persist_current_session(session: UploadSession) -> None:
    save_records(session.project_paths, session.calibration_records)
    save_standardized_inventory(
        session.project_paths, session.calibration_records,
        source_meta={
            "excel_filename": session.excel_filename,
            "source_type": "step3_upload",
        },
    )
    # Refresh the summary + meta so subsequent GETs report new counts.
    session.summary["calibration"] = count_by_status(session.calibration_records)
    save_meta(session.project_paths, {
        "session_id": session.session_id,
        "uploaded_at": session.uploaded_at,
        "excel_filename": session.excel_filename,
        "image_count": session.image_count,
        "factory_policy_version": FACTORY_POLICY_VERSION,
        "summary": session.summary,
    })


def calibration_records_dict(session: UploadSession) -> list[dict[str, Any]]:
    """Serialise every record for the ``/api/calibration/records``
    endpoint. Keeps the endpoint definition in ``routes.py`` thin."""
    return [r.to_dict() for r in session.calibration_records]
