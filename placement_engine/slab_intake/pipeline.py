"""ERP Excel + image folder → clean per-slab CSV / JSON (V1).

Pipeline steps:

    1. Index every image file under `image_dir` by its filename stem
       (the filename without extension). Stems are translated from
       Persian/Arabic-Indic digits to ASCII so they line up with cleaned
       serial numbers.

    2. Open the Excel file. If the workbook has multiple sheets and the
       caller didn't pick one, the sheet with the most non-empty rows
       wins.

    3. Apply `COLUMN_MAP` to rename Persian headers to internal names.
       Unmapped columns are listed in the report but dropped from the
       cleaned output to keep the schema predictable.

    4. For each row, build a `SlabRecord`:

        - clean serial_number (the dimension-encoded field; whitespace
          and invisibles stripped, original digit script preserved)
        - parse height_cm / width_cm from the first 6 digits of
          serial_number (thickness and any extra digits ignored for V1)
        - convert to mm
        - clean item_code (kept as metadata only — NOT used for
          dimension parsing in V1)
        - read area_m2 from the Excel column
        - compute calculated_area_m2 = h_mm * w_mm / 1e6 and flag
          `suspicious_area_mismatch` if it differs from area_m2 by more
          than `AREA_MISMATCH_RELATIVE_TOLERANCE`
        - generate normalized image-key candidates from serial_number
          (head-before-slash, dash-stripped head, digits-only head,
          first 7 / first 6 digits, slash-replaced variants) and walk
          them in order against the image index; image_id is set to the
          candidate that actually matched
        - if no serial-derived candidate matches, fall back to item_code
          as a last-resort image key (warning: `image_matched_via_item_code`)
        - pick slab_id: serial_number if available, else item_code
        - attach any optional metadata columns

    5. Sweep the records again to flag `duplicate_slab_id`. Shared
       `item_code` values are NOT a warning — multiple slabs in the
       same product batch are expected to share the ERP product code.

    6. `write_outputs` emits `clean_slabs.csv`, `clean_slabs.json`, and
       `ingestion_report.txt` into the output directory.
"""

from __future__ import annotations

import csv
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from placement_engine.slab_intake.column_map import COLUMN_MAP

logger = logging.getLogger(__name__)

# Image extensions we treat as slab photos (lowercase).
IMAGE_EXTENSIONS: tuple[str, ...] = (
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
)

# Tolerance for area-mismatch warning: |calc - excel| / max > 5%.
AREA_MISMATCH_RELATIVE_TOLERANCE: float = 0.05

# Persian + Arabic-Indic digits → ASCII. Used everywhere an ERP cell may
# contain non-ASCII digits (item codes, area, etc.).
_DIGIT_TRANSLATE = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789",
)

# Invisible Unicode characters that often leak into Persian ERP exports.
_INVISIBLE_CHARS = ("‌", "​", "﻿", "‎", "‏")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SlabRecord:
    """A single cleaned slab row, ready for the placement engine.

    V1 schema is deliberately minimal and engine-focused. Business
    metadata fields (material_name, status, block_id, batch_id, ...)
    are NOT part of V1. They will be added when the real slab database
    integration arrives.
    """

    # Identity
    slab_id: str | None = None
    serial_number: str | None = None
    item_code: str | None = None
    image_id: str | None = None
    # Geometry (cm and mm forms, plus area)
    height_cm: int | None = None
    width_cm: int | None = None
    height_mm: int | None = None
    width_mm: int | None = None
    area_m2: float | None = None
    calculated_area_m2: float | None = None
    # Image
    image_path: str | None = None
    image_found: bool = False
    # Source traceability
    source_excel_row: int | None = None
    # Warnings (free of duplicates, order preserved)
    warnings: list[str] = field(default_factory=list)


@dataclass
class SlabIngestionResult:
    """Outcome of a single ingestion run."""

    records: list[SlabRecord]
    excel_path: Path
    image_dir: Path
    sheet_name: str | None
    image_index: dict[str, Path]
    mapped_columns: dict[str, str]
    unmapped_columns: list[str]

    def warning_counts(self) -> dict[str, int]:
        """Histogram of warning codes across all records."""
        counts: dict[str, int] = {}
        for rec in self.records:
            for w in rec.warnings:
                counts[w] = counts.get(w, 0) + 1
        return counts


# ---------------------------------------------------------------------------
# Cell / header normalization
# ---------------------------------------------------------------------------


def _strip_invisible(s: str) -> str:
    for ch in _INVISIBLE_CHARS:
        s = s.replace(ch, "")
    return s


def _normalize_header(value: Any) -> str:
    """Trim whitespace and invisible Unicode characters from a header."""
    if value is None:
        return ""
    return _strip_invisible(str(value)).strip()


def _clean_cell(value: Any) -> Any:
    """Return None for blanks/NaN; trim and strip invisibles from strings."""
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    if isinstance(value, str):
        s = _strip_invisible(value).strip()
        return s or None
    return value


def _to_str_code(value: Any) -> str | None:
    """Coerce an item-code-like value into a digit-bearing string.

    Preserves leading zeros where possible (whole-number floats get cast
    via int rather than ``str(float)`` to avoid ``"12020.0"`` artifacts)
    and translates Persian/Arabic digits to ASCII.
    """
    if value is None:
        return None
    if isinstance(value, float):
        if pd.isna(value):
            return None
        if value.is_integer():
            s = str(int(value))
        else:
            s = repr(value).rstrip("0").rstrip(".")
    else:
        s = str(value).strip()
    s = _strip_invisible(s).translate(_DIGIT_TRANSLATE).strip()
    return s or None


# ---------------------------------------------------------------------------
# Serial-number normalization + dimension parsing
# ---------------------------------------------------------------------------


def _normalize_serial_cell(value: Any) -> str | None:
    """Return a printable serial string with whitespace + invisibles trimmed.

    Original digit script is preserved (Persian or ASCII) — the export
    keeps the serial as the ERP wrote it. Digit translation happens
    later, only when computing image-key candidates or parsing
    dimensions.
    """
    if value is None:
        return None
    if isinstance(value, float):
        if pd.isna(value):
            return None
        s = str(int(value)) if value.is_integer() else repr(value)
    else:
        s = str(value)
    s = _strip_invisible(s).strip()
    return s or None


def parse_dimensions_from_serial(serial: str | None) -> tuple[int | None, int | None]:
    """Return ``(height_cm, width_cm)`` parsed from a سریال کالا value.

    Convention (V1) — **first chunk before the first ``-`` only**:

        1. Translate Persian / Arabic-Indic digits to ASCII.
        2. Split at the first ``-`` and take the leading chunk.
        3. Extract digits from that chunk.
        4. First 3 digits = ``height_cm``, next 3 = ``width_cm``.
        5. Any remaining digit(s) in the chunk represent thickness and
           are ignored for V1.

    Digits in *later* chunks are deliberately NOT considered — that
    prevents e.g. an after-slash code or a batch suffix from
    accidentally filling in missing dimension digits. Returns
    ``(None, None)`` when fewer than six digits can be extracted from
    the leading chunk.

    Examples:
        "1731792-4731/AV2040643-05" -> first chunk "1731792" -> (173, 179)
        "1202002"                   -> first chunk "1202002" -> (120, 200)
        "۱۲۰۲۰۰۲"                   -> "1202002"            -> (120, 200)
        "120-200-2"                 -> first chunk "120"     -> (None, None)
    """
    if not serial:
        return None, None
    translated = serial.translate(_DIGIT_TRANSLATE)
    first_chunk = translated.split("-", 1)[0]
    digits = re.sub(r"\D", "", first_chunk)
    if len(digits) < 6:
        return None, None
    return int(digits[0:3]), int(digits[3:6])


def _normalize_serial_for_image(serial: str) -> tuple[str, list[str]]:
    """Derive image-stem candidates from a سریال کالا value.

    Returns ``(primary, candidates)``:

    * ``primary`` — the deterministic ``image_id`` recorded on the row,
      regardless of whether any candidate finds a file. This is the
      portion of the serial before the first ``/``, after digit-script
      translation. Stable across runs and easy to recognize in reports.
    * ``candidates`` — ordered list of stems to try against the image
      index. Higher-priority forms come first. Duplicates are squashed.

    Filenames cannot contain ``/`` on common filesystems, so we never
    try the raw post-slash form as a stem; we generate slash-replaced
    variants instead.
    """
    s = _strip_invisible(serial).translate(_DIGIT_TRANSLATE).strip()
    head = s.split("/", 1)[0].strip()
    primary = head or s

    candidates: list[str] = []

    def add(c: str) -> None:
        c = c.strip()
        if c and c not in candidates:
            candidates.append(c)

    # 1. Portion before any slash — the most common filename convention.
    add(head)
    # 2. ERP "short form": drop the "/AV<digits>" middle segment, keeping
    #    any trailing slab-number suffix. E.g.
    #        "1781722-4731/AV2040643-04"  ->  "1781722-4731-04"
    #    This is the convention used by Avandad's slab photo folder.
    av_stripped = re.sub(r"/AV\d+", "", s)
    if av_stripped != s:
        add(av_stripped)
    # 3. Same with dashes/underscores removed (some exports normalize them out).
    add(re.sub(r"[-_]", "", head))
    # 4. Digits-only of the head.
    add(re.sub(r"\D", "", head))
    all_digits = re.sub(r"\D", "", s)
    # 5. First 7 digits (height + width + thickness).
    if len(all_digits) >= 7:
        add(all_digits[:7])
    # 6. First 6 digits (height + width only).
    if len(all_digits) >= 6:
        add(all_digits[:6])
    # 7. Slash-replaced variants of the full serial (Windows-safe naming).
    if "/" in s:
        add(s.replace("/", "_"))
        add(s.replace("/", "-"))

    return primary, candidates


# ---------------------------------------------------------------------------
# Image index
# ---------------------------------------------------------------------------


def build_image_index(image_dir: Path) -> dict[str, Path]:
    """Index every image under ``image_dir`` by filename stem.

    Scans recursively. Stems are stripped, invisible-Unicode-scrubbed and
    digit-translated so they match cleaned item codes. The first stem
    encountered (in sorted order) wins on collision — duplicates are only
    logged at DEBUG level, never raised.
    """
    index: dict[str, Path] = {}
    if not image_dir.exists():
        logger.warning("Image directory does not exist: %s", image_dir)
        return index
    if not image_dir.is_dir():
        logger.warning("Image path is not a directory: %s", image_dir)
        return index
    for path in sorted(image_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        stem = _strip_invisible(path.stem).strip().translate(_DIGIT_TRANSLATE)
        if not stem:
            continue
        if stem in index:
            logger.debug(
                "Duplicate image stem %r, keeping %s, ignoring %s",
                stem, index[stem], path,
            )
            continue
        index[stem] = path
    return index


# ---------------------------------------------------------------------------
# Excel loading / column rename
# ---------------------------------------------------------------------------


def _load_excel(excel_path: Path, sheet_name: str | None) -> tuple[pd.DataFrame, str]:
    """Read the chosen (or best) sheet and return ``(df, sheet_name)``.

    With multiple sheets and no explicit choice, pick the sheet with the
    most non-empty rows.
    """
    book = pd.read_excel(excel_path, sheet_name=None, dtype=object)
    if not book:
        raise ValueError(f"Excel file has no sheets: {excel_path}")
    if sheet_name is not None:
        if sheet_name not in book:
            raise KeyError(
                f"Sheet {sheet_name!r} not found. Available: {list(book)}"
            )
        return book[sheet_name], sheet_name
    if len(book) == 1:
        only = next(iter(book))
        return book[only], only
    best_name, best_df = max(
        book.items(),
        key=lambda kv: len(kv[1].dropna(how="all")),
    )
    return best_df, best_name


def _rename_columns(
    df: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, str], list[str]]:
    """Apply ``COLUMN_MAP`` and drop unmapped columns from the working df.

    Unmapped headers are returned in ``unmapped`` for reporting. Duplicate
    internal names are de-duplicated (first occurrence wins) so downstream
    code never has to deal with ambiguous column references.
    """
    renamed_cols: list[str] = []
    keep_mask: list[bool] = []
    mapped: dict[str, str] = {}
    unmapped: list[str] = []
    seen_internal: set[str] = set()
    for col in df.columns:
        header = _normalize_header(col)
        internal = COLUMN_MAP.get(header)
        if internal is None:
            unmapped.append(header or str(col))
            renamed_cols.append(str(col))
            keep_mask.append(False)
            continue
        if internal in seen_internal:
            # Another column already mapped to this internal name; drop.
            logger.debug(
                "Duplicate mapping to %r from header %r; ignoring.",
                internal, header,
            )
            renamed_cols.append(str(col))
            keep_mask.append(False)
            continue
        mapped[header] = internal
        seen_internal.add(internal)
        renamed_cols.append(internal)
        keep_mask.append(True)
    df = df.copy()
    df.columns = renamed_cols
    df = df.loc[:, keep_mask]
    return df, mapped, unmapped


# ---------------------------------------------------------------------------
# Per-row record build
# ---------------------------------------------------------------------------


def _add_warning(warnings: list[str], code: str) -> None:
    if code not in warnings:
        warnings.append(code)


def _build_record(
    row: pd.Series,
    source_excel_row: int,
    image_index: dict[str, Path],
) -> SlabRecord:
    rec = SlabRecord(source_excel_row=source_excel_row)
    warnings: list[str] = []

    # --- read raw identity cells --------------------------------------
    item_code = _to_str_code(row.get("item_code") if "item_code" in row else None)
    serial_clean = _normalize_serial_cell(
        row.get("serial_number") if "serial_number" in row else None
    )

    rec.item_code = item_code
    rec.serial_number = serial_clean  # preserves the original serial string
    # slab_id = normalized serial when available, else item_code as fallback.
    rec.slab_id = serial_clean or item_code

    if not serial_clean:
        _add_warning(warnings, "missing_serial_number")
    if not item_code:
        # item_code is metadata only, so this is informational.
        _add_warning(warnings, "missing_item_code")

    # --- dimensions from serial_number --------------------------------
    if serial_clean:
        h_cm, w_cm = parse_dimensions_from_serial(serial_clean)
        if h_cm is None or w_cm is None:
            _add_warning(warnings, "invalid_serial_format")
            _add_warning(warnings, "could_not_parse_dimensions")
        else:
            rec.height_cm = h_cm
            rec.width_cm = w_cm
            rec.height_mm = h_cm * 10
            rec.width_mm = w_cm * 10
    else:
        _add_warning(warnings, "could_not_parse_dimensions")

    # --- area ---------------------------------------------------------
    area_raw = row.get("area_m2") if "area_m2" in row else None
    if area_raw is None or (isinstance(area_raw, float) and pd.isna(area_raw)):
        _add_warning(warnings, "missing_area_m2")
        rec.area_m2 = None
    else:
        try:
            area_str = (
                _strip_invisible(str(area_raw))
                .translate(_DIGIT_TRANSLATE)
                .strip()
                .replace(",", ".")
            )
            rec.area_m2 = float(area_str)
        except (TypeError, ValueError):
            _add_warning(warnings, "missing_area_m2")
            rec.area_m2 = None

    if rec.height_mm is not None and rec.width_mm is not None:
        rec.calculated_area_m2 = round(
            (rec.height_mm * rec.width_mm) / 1_000_000.0, 4
        )
        if rec.area_m2 is not None and rec.calculated_area_m2 > 0:
            ref = max(rec.area_m2, rec.calculated_area_m2)
            rel = abs(rec.area_m2 - rec.calculated_area_m2) / ref
            if rel > AREA_MISMATCH_RELATIVE_TOLERANCE:
                _add_warning(warnings, "suspicious_area_mismatch")

    # --- image match (serial-first, item_code as last resort) --------
    image_id: str | None = None
    matched_path: Path | None = None

    if serial_clean:
        primary, candidates = _normalize_serial_for_image(serial_clean)
        image_id = primary
        for cand in candidates:
            hit = image_index.get(cand)
            if hit is not None:
                matched_path = hit
                # Record exactly which form matched, for downstream traceability.
                image_id = cand
                break

    # Last-resort fallback: if no serial-derived stem matched a file but
    # we do have an item_code, try that. Flag it so designers can verify.
    if matched_path is None and item_code:
        hit = image_index.get(item_code)
        if hit is not None:
            matched_path = hit
            image_id = item_code
            _add_warning(warnings, "image_matched_via_item_code")

    rec.image_id = image_id
    if matched_path is not None:
        rec.image_path = str(matched_path)
        rec.image_found = True
    else:
        _add_warning(warnings, "image_not_found")

    rec.warnings = warnings
    return rec


# ---------------------------------------------------------------------------
# Top-level ingest
# ---------------------------------------------------------------------------


def ingest_slab_export(
    excel_path: str | Path,
    image_dir: str | Path,
    sheet_name: str | None = None,
) -> SlabIngestionResult:
    """Load and clean a single ERP Excel + image-folder pair."""
    excel_path = Path(excel_path)
    image_dir = Path(image_dir)
    if not excel_path.exists():
        raise FileNotFoundError(f"Excel file not found: {excel_path}")

    image_index = build_image_index(image_dir)
    df, resolved_sheet = _load_excel(excel_path, sheet_name)
    df, mapped, unmapped = _rename_columns(df)

    records: list[SlabRecord] = []
    for i, row in df.iterrows():
        # Excel rows are 1-indexed and include the header — +2 lines up
        # with what a user sees when opening the file in Excel.
        records.append(_build_record(row, int(i) + 2, image_index))

    # Cross-row duplicate detection — slab_id only.
    #
    # Shared `item_code` is NOT a warning: under the V1 model, item_code
    # is the ERP product/material code, so every slab in a product
    # batch legitimately shares it. Only duplicate slab_id (= duplicate
    # serial_number, with item_code fallback when no serial exists) is
    # a real integrity issue.
    slab_id_counts: dict[str, int] = {}
    for rec in records:
        if rec.slab_id:
            slab_id_counts[rec.slab_id] = slab_id_counts.get(rec.slab_id, 0) + 1
    for rec in records:
        if rec.slab_id and slab_id_counts[rec.slab_id] > 1:
            _add_warning(rec.warnings, "duplicate_slab_id")

    return SlabIngestionResult(
        records=records,
        excel_path=excel_path,
        image_dir=image_dir,
        sheet_name=resolved_sheet,
        image_index=image_index,
        mapped_columns=mapped,
        unmapped_columns=unmapped,
    )


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------


# Order matters: this is the canonical column order in clean_slabs.csv.
# Kept minimal and engine-focused for V1 — business metadata is not
# included yet (see ARCHITECTURE.md §Slab database future work).
CSV_FIELDS: tuple[str, ...] = (
    "slab_id",
    "serial_number",
    "item_code",
    "image_id",
    "height_cm",
    "width_cm",
    "height_mm",
    "width_mm",
    "area_m2",
    "calculated_area_m2",
    "image_path",
    "image_found",
    "source_excel_row",
    "warnings",
)


def write_outputs(result: SlabIngestionResult, output_dir: str | Path) -> dict[str, Path]:
    """Write CSV, JSON, and the human-readable report. Return the paths."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    csv_path = output_dir / "clean_slabs.csv"
    json_path = output_dir / "clean_slabs.json"
    report_path = output_dir / "ingestion_report.txt"

    # --- CSV (UTF-8 BOM so Excel opens Persian columns correctly) -----
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(CSV_FIELDS))
        writer.writeheader()
        for rec in result.records:
            d = asdict(rec)
            row = {k: d.get(k, "") for k in CSV_FIELDS}
            row["warnings"] = "; ".join(rec.warnings)
            row["image_found"] = "true" if rec.image_found else "false"
            writer.writerow(row)

    # --- JSON (preserves richer types: lists, bools, nulls) -----------
    json_payload = {
        "source_excel": str(result.excel_path),
        "image_dir": str(result.image_dir),
        "sheet_name": result.sheet_name,
        "record_count": len(result.records),
        "warning_counts": result.warning_counts(),
        "mapped_columns": result.mapped_columns,
        "unmapped_columns": result.unmapped_columns,
        "records": [asdict(rec) for rec in result.records],
    }
    json_path.write_text(
        json.dumps(json_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # --- Human report -------------------------------------------------
    report_path.write_text(_build_report(result), encoding="utf-8")

    return {"csv": csv_path, "json": json_path, "report": report_path}


def _build_report(result: SlabIngestionResult) -> str:
    total = len(result.records)
    matched = sum(1 for r in result.records if r.image_found)
    fully_parsed = sum(
        1
        for r in result.records
        if r.height_mm is not None and r.width_mm is not None and r.area_m2 is not None
    )
    counts = result.warning_counts()

    lines: list[str] = []
    lines.append("Slab ingestion report (V1 ERP bridge)")
    lines.append("=" * 60)
    lines.append(f"Source Excel        : {result.excel_path}")
    lines.append(f"Sheet               : {result.sheet_name}")
    lines.append(f"Image folder        : {result.image_dir}")
    lines.append(f"Image files indexed : {len(result.image_index)}")
    lines.append("")
    lines.append(f"Rows ingested        : {total}")
    lines.append(f"Images matched       : {matched} / {total}")
    lines.append(f"Fully parsed records : {fully_parsed} / {total}")
    lines.append("  (serial_number parseable + height + width + area_m2 present)")
    lines.append("")

    lines.append("Mapped Persian columns:")
    if result.mapped_columns:
        for src, dst in result.mapped_columns.items():
            lines.append(f"  {src}  ->  {dst}")
    else:
        lines.append("  (none — check column_map.py against the file)")
    lines.append("")

    lines.append("Unmapped columns (dropped from clean output):")
    if result.unmapped_columns:
        for c in result.unmapped_columns:
            lines.append(f"  {c}")
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append("Warning counts:")
    if counts:
        for w, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"  {w:30s} {n}")
    else:
        lines.append("  (no warnings)")
    lines.append("")

    # Per-row warning list — designers can scan this without opening CSV.
    rows_with_warnings = [r for r in result.records if r.warnings]
    if rows_with_warnings:
        lines.append("Rows with warnings:")
        for r in rows_with_warnings:
            ident = r.slab_id or r.item_code or f"row{r.source_excel_row}"
            lines.append(
                f"  row {r.source_excel_row:>4}  {ident:<20}  "
                f"{', '.join(r.warnings)}"
            )

    return "\n".join(lines) + "\n"
