"""Process uploaded slab inventory (Excel + photos) into the
canonical ``clean_slabs.json`` shape, and remember the active
session in-memory so the matcher can use it.

Why this module exists
----------------------
Step 3 of the 4-step wizard needs a real upload path. The existing
ingestion pipeline (``placement_engine.slab_intake.pipeline``) does
all the heavy lifting — Excel parsing, dimension normalisation,
serial-suffix photo matching — and writes the same
``clean_slabs.json`` format the engine and the API already consume.

So this module is a THIN session layer:

  * write the uploaded files to a tempdir,
  * call ``ingest_slab_export(...)`` + ``write_outputs(...)``,
  * stash the tempdir path in module state so the inventory resolver
    can prefer it over the demo / real defaults,
  * surface a summary the frontend can render directly.

Single-user / single-session is fine for V1 — only one upload at a
time can be "active". Replacing the upload cleans up the previous
tempdir. No persistence across server restarts.

The matcher consumes the uploaded inventory transparently:
``resolve_inventory_source`` (next door) now checks
``get_active_upload()`` BEFORE the env / real / demo defaults.
"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from placement_engine.image_intake.processor import (
    ImageMetadata, process_inventory, write_outputs as write_image_outputs,
)
from placement_engine.slab_intake.pipeline import (
    SlabIngestionResult, ingest_slab_export, write_outputs,
)


@dataclass(frozen=True)
class UploadSession:
    """One uploaded inventory session.

    ``clean_slabs_path`` points at the JSON the inventory loader will
    read — the rest of the API only needs that. The other fields are
    for the GET /api/inventory/current endpoint + cleanup.

    ``image_metadata_by_slab`` keys slab_id → per-image record from the
    green-box crop pass (``placement_engine.image_intake.processor``).
    The matcher endpoint can consult this without re-reading the
    on-disk image_metadata.json. Records may have
    ``green_box_detected=False`` when detection fell back; the
    slab-image endpoint then serves the original photo + flags the
    fallback.
    """

    session_id: str
    uploaded_at: str
    excel_filename: str
    image_count: int
    temp_dir: Path
    clean_slabs_path: Path
    summary: dict[str, Any]
    image_metadata_by_slab: dict[str, ImageMetadata]


# Module-level slot for the active session. V1 supports one uploaded
# inventory at a time across all clients; the spec allows
# session/local temporary storage, so a global ref is enough.
_ACTIVE_UPLOAD: UploadSession | None = None


def get_active_upload() -> UploadSession | None:
    """Return the currently-active uploaded inventory, or None when
    no upload has happened (the resolver then falls back to the demo /
    real inventories on disk)."""
    return _ACTIVE_UPLOAD


def clear_active_upload() -> None:
    """Drop the active upload and clean its tempdir. Idempotent."""
    global _ACTIVE_UPLOAD
    if _ACTIVE_UPLOAD is None:
        return
    try:
        shutil.rmtree(_ACTIVE_UPLOAD.temp_dir, ignore_errors=True)
    finally:
        _ACTIVE_UPLOAD = None


def process_upload(
    excel_bytes: bytes, excel_filename: str,
    images: list[tuple[str, bytes]],
) -> UploadSession:
    """Persist the uploaded files to a tempdir, run the slab-intake
    pipeline, and install the result as the active session.

    Replaces any previous active upload — the previous tempdir is
    deleted so memory doesn't grow without bound across uploads.

    ``images`` is a list of ``(filename, bytes)`` pairs. The
    pipeline's photo matcher reads the FILENAME, not the bytes,
    when linking photos to slab records, so the original filenames
    are preserved exactly on disk.
    """
    # Tempdir layout: <root>/excel/<original>.xlsx + <root>/images/*
    # Mirrors how the real project ships these files (see
    # data/raw_test/ in the repo).
    root = Path(tempfile.mkdtemp(prefix="stonelayout_upload_"))
    excel_dir = root / "excel"
    excel_dir.mkdir()
    image_dir = root / "images"
    image_dir.mkdir()

    excel_path = excel_dir / excel_filename
    excel_path.write_bytes(excel_bytes)

    written_images: list[Path] = []
    for original_name, payload in images:
        # Sanitise minimally — strip directory components so a
        # malicious uploader can't write outside the tempdir. The
        # filename's stem is what the matcher reads, so we keep it
        # intact otherwise.
        safe_name = Path(original_name).name
        dest = image_dir / safe_name
        dest.write_bytes(payload)
        written_images.append(dest)

    try:
        result = ingest_slab_export(
            excel_path=excel_path, image_dir=image_dir, sheet_name=None,
        )
        # write_outputs produces clean_slabs.json in the canonical
        # format the inventory loader already understands. We store
        # it at the root of the tempdir.
        out_paths = write_outputs(result, root)
    except Exception:
        # Pipeline failed (Excel unreadable, no recognised columns,
        # etc.) — clean up the tempdir before re-raising so we don't
        # leak.
        shutil.rmtree(root, ignore_errors=True)
        raise

    # 0.1.47 — also run the green-box crop pass. This produces
    # processed images (the usable rectangle inside each slab photo)
    # + image_metadata.json with crop_x/y/width/height. Failures here
    # are NON-FATAL: if cv2 misbehaves or detection finds no green
    # rectangle, individual records fall back to ``green_box_detected
    # = False`` and the slab-image endpoint serves the original. The
    # upload as a whole still succeeds.
    image_metadata_by_slab: dict[str, ImageMetadata] = {}
    try:
        image_result = process_inventory(out_paths["json"], root)
        write_image_outputs(image_result)
        for meta in image_result.images:
            image_metadata_by_slab[meta.slab_id] = meta
    except Exception:
        # Defensive: never let the crop pass break the upload. The
        # session still serves originals through the regular
        # slab-image endpoint.
        image_metadata_by_slab = {}

    summary = _build_summary(
        result, image_dir, written_images, image_metadata_by_slab,
    )

    session = UploadSession(
        session_id=str(uuid.uuid4()),
        uploaded_at=datetime.utcnow().isoformat() + "Z",
        excel_filename=excel_filename,
        image_count=len(written_images),
        temp_dir=root,
        clean_slabs_path=out_paths["json"],
        summary=summary,
        image_metadata_by_slab=image_metadata_by_slab,
    )

    # Swap in the new session — replaces (and cleans up) the previous
    # active upload if any.
    global _ACTIVE_UPLOAD
    previous = _ACTIVE_UPLOAD
    _ACTIVE_UPLOAD = session
    if previous is not None:
        shutil.rmtree(previous.temp_dir, ignore_errors=True)

    return session


def _build_summary(
    result: SlabIngestionResult,
    image_dir: Path,
    written_images: list[Path],
    image_metadata_by_slab: dict[str, ImageMetadata] | None = None,
) -> dict[str, Any]:
    """Frontend-facing summary of the upload outcome.

    Counts:
      * total_rows     — Excel rows the pipeline saw
      * valid_slabs    — rows with positive width AND height
      * invalid_slabs  — rows missing or malformed dimensions
      * linked_photos  — slab records with image_found=True
      * unmatched_photos — files in the upload that the matcher
        couldn't link to any slab (extra photos)
      * slabs_without_photos — slab records with no linked photo

    Also returns a small preview (first 10 normalised records) so
    the Step 3 panel can show a table without a second request.
    """
    records = result.records
    valid = [r for r in records if r.width_mm and r.height_mm]
    linked = [r for r in records if r.image_found]
    missing_photo = [r for r in records if not r.image_found]

    # Unmatched photos = files on disk whose stem isn't referenced
    # by any record's image_path. We compare via Path resolution
    # because the pipeline records absolute paths.
    matched_paths = {
        Path(r.image_path).resolve() for r in records
        if r.image_path
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

    # 0.1.47 — surface the green-box crop outcome to the UI so the
    # Step-3 panel can show "N safe crops detected" and the Step-4
    # properties card can warn when a fallback to the full image
    # happened. Numbers default to None when the crop pass didn't
    # run (e.g. cv2 missing), so the panel can render gracefully.
    safe_crops_detected: int | None = None
    safe_crops_missing: int | None = None
    if image_metadata_by_slab is not None:
        safe_crops_detected = sum(
            1 for m in image_metadata_by_slab.values() if m.green_box_detected
        )
        safe_crops_missing = sum(
            1 for m in image_metadata_by_slab.values()
            if not m.green_box_detected and m.original_image_path
        )

    return {
        "total_rows": len(records),
        "valid_slabs": len(valid),
        "invalid_slabs": len(records) - len(valid),
        "linked_photos": len(linked),
        "unmatched_photos": unmatched,
        "slabs_without_photos": [r.slab_id for r in missing_photo if r.slab_id],
        "mapped_columns": result.mapped_columns,
        "unmapped_columns": result.unmapped_columns,
        "warning_counts": result.warning_counts(),
        "preview": preview,
        "safe_crops_detected": safe_crops_detected,
        "safe_crops_missing": safe_crops_missing,
    }
