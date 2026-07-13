"""Tests for the V1 slab ingestion bridge (Excel + images → clean CSV/JSON).

V1 convention: `سریال کالا` / `serial_number` is the dimension-encoded
field AND the image-matching key. `کد کالا` / `item_code` is metadata
only (used at most as a last-resort image fallback).
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from placement_engine.slab_intake.pipeline import (
    _normalize_serial_for_image,
    build_image_index,
    ingest_slab_export,
    parse_dimensions_from_serial,
    write_outputs,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _write_excel(path: Path, rows: list[dict], sheet_name: str = "Sheet1") -> Path:
    df = pd.DataFrame(rows)
    df.to_excel(path, index=False, sheet_name=sheet_name)
    return path


# ---------------------------------------------------------------------------
# parse_dimensions_from_serial
# ---------------------------------------------------------------------------


def test_parse_dimensions_from_serial_real_world_example():
    # The exact case the user called out in the spec update.
    h, w = parse_dimensions_from_serial("1731792-4731/AV2040643-05")
    assert (h, w) == (173, 179)


def test_parse_dimensions_six_digits_plain():
    assert parse_dimensions_from_serial("120200") == (120, 200)


def test_parse_dimensions_ignores_trailing_thickness_digit():
    # Trailing "2" is thickness (ignored in V1).
    assert parse_dimensions_from_serial("1202002") == (120, 200)


def test_parse_dimensions_persian_numerals():
    assert parse_dimensions_from_serial("۱۲۰۲۰۰۲") == (120, 200)


def test_parse_dimensions_short_or_empty():
    assert parse_dimensions_from_serial("12020") == (None, None)
    assert parse_dimensions_from_serial("") == (None, None)
    assert parse_dimensions_from_serial(None) == (None, None)


def test_parse_dimensions_only_uses_first_chunk_before_dash():
    """V1 rule: dimensions come from the chunk before the first '-' only.

    Digits in later chunks must NOT bleed into the dimension parse,
    otherwise an after-slash code or batch suffix could silently fill
    in missing dimension digits.
    """
    # First chunk '120' has only 3 digits — even though later chunks
    # contain plenty of digits, the parser refuses to use them.
    assert parse_dimensions_from_serial("120-200-2/X") == (None, None)
    # First chunk '1234567' has 7 digits → 123, 456 (last digit = thickness).
    assert parse_dimensions_from_serial("1234567-99/AV") == (123, 456)
    # Non-digit characters inside the first chunk are stripped.
    assert parse_dimensions_from_serial("12X345Y67-rest") == (123, 456)


# ---------------------------------------------------------------------------
# _normalize_serial_for_image
# ---------------------------------------------------------------------------


def test_normalize_serial_for_image_head_before_slash():
    primary, candidates = _normalize_serial_for_image("1731792-4731/AV2040643-05")
    assert primary == "1731792-4731"
    # Order matters: head, dash-stripped head, digits-only head, ...
    assert candidates[0] == "1731792-4731"
    assert "17317924731" in candidates
    assert "1731792" in candidates  # first 7 digits
    assert "173179" in candidates  # first 6 digits


def test_normalize_serial_for_image_no_slash():
    primary, candidates = _normalize_serial_for_image("1202002")
    assert primary == "1202002"
    assert "1202002" in candidates
    assert "120200" in candidates


def test_normalize_serial_for_image_strips_av_segment():
    """ERP short-form: image filenames drop the '/AV<digits>' segment.

    Serial `1781722-4731/AV2040643-04` must produce the candidate
    `1781722-4731-04`, matching the actual filename convention in the
    real ERP photo folder.
    """
    _, candidates = _normalize_serial_for_image("1781722-4731/AV2040643-04")
    assert "1781722-4731-04" in candidates
    _, candidates = _normalize_serial_for_image("2161942-4731/AV2040643-61")
    assert "2161942-4731-61" in candidates
    # Serials without '/AV' don't pick up a redundant candidate.
    _, candidates = _normalize_serial_for_image("1202002")
    assert candidates.count("1202002") == 1


# ---------------------------------------------------------------------------
# Excel-explicit dimensions vs serial fallback (طول / عرض)
# ---------------------------------------------------------------------------


def test_explicit_excel_dimensions_take_precedence_over_serial(tmp_path: Path):
    """When both طول/عرض are present, they are the source of truth.

    The serial parser still runs but does NOT override the Excel value.
    """
    excel = _write_excel(
        tmp_path / "export.xlsx",
        [
            {
                "کد کالا": "P-1",
                "سریال کالا": "1591592-6545_5538-08",  # encodes 159×159
                "شماره": 8,
                "طول (CM)": 159,
                "عرض (CM)": 159,
                "مساحت (M2)": 2.528,
            }
        ],
    )
    images = tmp_path / "images"
    images.mkdir()
    result = ingest_slab_export(excel, images)
    rec = result.records[0]
    assert rec.height_cm == 159 and rec.width_cm == 159
    assert rec.height_mm == 1590 and rec.width_mm == 1590
    assert rec.dimension_source == "explicit_excel"
    # Agreement → no mismatch warning.
    assert "dimension_mismatch_excel_vs_serial" not in rec.warnings


def test_serial_fallback_used_when_excel_dimensions_absent(tmp_path: Path):
    """No طول/عرض cells → fall back to the serial parser."""
    excel = _write_excel(
        tmp_path / "export.xlsx",
        [
            {
                "کد کالا": "P-1",
                "سریال کالا": "1591592-6545_5538-08",
                "شماره": 8,
                "مساحت (M2)": 2.528,
            }
        ],
    )
    images = tmp_path / "images"
    images.mkdir()
    result = ingest_slab_export(excel, images)
    rec = result.records[0]
    assert rec.height_cm == 159 and rec.width_cm == 159
    assert rec.dimension_source == "serial_fallback"


def test_partial_excel_dimensions_fall_back_to_serial(tmp_path: Path):
    """One cell populated isn't enough — both must be present to count
    as explicit_excel. Otherwise the serial is used in full."""
    excel = _write_excel(
        tmp_path / "export.xlsx",
        [
            {
                "کد کالا": "P-1",
                "سریال کالا": "1591592-6545_5538-08",
                "شماره": 8,
                "طول (CM)": 200,  # height present
                "عرض (CM)": None,  # width missing
                "مساحت (M2)": 2.528,
            }
        ],
    )
    images = tmp_path / "images"
    images.mkdir()
    result = ingest_slab_export(excel, images)
    rec = result.records[0]
    assert rec.dimension_source == "serial_fallback"
    # Falling back: both come from the serial.
    assert rec.height_cm == 159 and rec.width_cm == 159


def test_dimension_mismatch_warning_when_excel_and_serial_disagree(tmp_path: Path):
    """Excel says 180/180, serial says 159/159 → mismatch warning fires."""
    excel = _write_excel(
        tmp_path / "export.xlsx",
        [
            {
                "کد کالا": "P-1",
                "سریال کالا": "1591592-6545_5538-08",  # → 159×159
                "شماره": 8,
                "طول (CM)": 180,
                "عرض (CM)": 180,
                "مساحت (M2)": 3.24,
            }
        ],
    )
    images = tmp_path / "images"
    images.mkdir()
    result = ingest_slab_export(excel, images)
    rec = result.records[0]
    # Excel still wins (source of truth).
    assert rec.height_cm == 180 and rec.width_cm == 180
    assert rec.dimension_source == "explicit_excel"
    assert "dimension_mismatch_excel_vs_serial" in rec.warnings


def test_tiny_dimension_difference_within_tolerance_no_warning(tmp_path: Path):
    """Excel 159.5 vs serial 159 → within 1 cm tolerance, no warning."""
    excel = _write_excel(
        tmp_path / "export.xlsx",
        [
            {
                "کد کالا": "P-1",
                "سریال کالا": "1591592-6545_5538-08",  # → 159×159
                "شماره": 8,
                "طول (CM)": 159.5,
                "عرض (CM)": 159.5,
                "مساحت (M2)": 2.544,
            }
        ],
    )
    images = tmp_path / "images"
    images.mkdir()
    result = ingest_slab_export(excel, images)
    rec = result.records[0]
    assert rec.height_cm == 159.5 and rec.width_cm == 159.5
    assert "dimension_mismatch_excel_vs_serial" not in rec.warnings


def test_calculated_area_uses_chosen_dimension_source(tmp_path: Path):
    """calculated_area_m2 must reflect the dimensions actually used."""
    excel = _write_excel(
        tmp_path / "export.xlsx",
        [
            {
                "کد کالا": "P-1",
                "سریال کالا": "1591592-6545_5538-08",  # serial → 159×159 → 2.5281
                "شماره": 8,
                "طول (CM)": 200,
                "عرض (CM)": 200,  # excel → 200×200 → 4.0
                "مساحت (M2)": 4.0,
            }
        ],
    )
    images = tmp_path / "images"
    images.mkdir()
    result = ingest_slab_export(excel, images)
    rec = result.records[0]
    # Excel wins, so calculated_area must use 200×200, not 159×159.
    assert rec.calculated_area_m2 == pytest.approx(4.0)


def test_image_match_by_slab_number_suffix_single_digit(tmp_path: Path):
    """Excel slab_number 2 → image filename '####-####-2.jpeg'."""
    excel = _write_excel(
        tmp_path / "export.xlsx",
        [
            {
                "کد کالا": "P-1",
                "سریال کالا": "1731792-4731/AV2040643-05",
                "شماره": 2,
                "مساحت (M2)": 3.10,
            }
        ],
    )
    images = tmp_path / "images"
    images.mkdir()
    img = images / "5538-6545-2.jpeg"
    img.write_bytes(b"")

    result = ingest_slab_export(excel, images)
    rec = result.records[0]
    assert rec.slab_number == "2"
    assert rec.image_found is True
    assert rec.image_match_method == "slab_number_suffix"
    assert rec.image_id == "5538-6545-2"
    assert rec.image_path == str(img)
    # Dimensions still come from the serial, not the image filename.
    assert rec.height_cm == 173 and rec.width_cm == 179


def test_image_match_by_slab_number_suffix_two_digit(tmp_path: Path):
    """Excel slab_number 27 → image filename '####-####-27.jpeg'."""
    excel = _write_excel(
        tmp_path / "export.xlsx",
        [
            {
                "کد کالا": "P-1",
                "سریال کالا": "1731792-4731/AV2040643-27",
                "شماره": 27,
                "مساحت (M2)": 3.10,
            }
        ],
    )
    images = tmp_path / "images"
    images.mkdir()
    img = images / "5538-6545-27.jpeg"
    img.write_bytes(b"")
    # Confounder: a single-digit-2 image must NOT be picked for slab 27.
    (images / "5538-6545-2.jpeg").write_bytes(b"")

    result = ingest_slab_export(excel, images)
    rec = result.records[0]
    assert rec.image_found is True
    assert rec.image_match_method == "slab_number_suffix"
    assert rec.image_id == "5538-6545-27"


def test_slab_number_with_leading_zero_matches_int_suffix(tmp_path: Path):
    """شماره '05' (string with leading zero) matches image suffix 5."""
    excel = _write_excel(
        tmp_path / "export.xlsx",
        [
            {
                "کد کالا": "P-1",
                "سریال کالا": "1731792-4731/AV2040643-05",
                "شماره": "05",
                "مساحت (M2)": 3.10,
            }
        ],
    )
    images = tmp_path / "images"
    images.mkdir()
    img = images / "1731792-4731-05.jpg"
    img.write_bytes(b"")

    result = ingest_slab_export(excel, images)
    rec = result.records[0]
    assert rec.slab_number == "05"  # preserved verbatim in output
    assert rec.image_found is True
    assert rec.image_match_method == "slab_number_suffix"


def test_serial_fallback_used_when_no_slab_number_match(tmp_path: Path):
    """slab_number 99 doesn't match any suffix → fall back to serial stem."""
    excel = _write_excel(
        tmp_path / "export.xlsx",
        [
            {
                "کد کالا": "P-1",
                "سریال کالا": "1731792-4731/AV2040643-05",
                "شماره": 99,  # no image ends in 99
                "مساحت (M2)": 3.10,
            }
        ],
    )
    images = tmp_path / "images"
    images.mkdir()
    img = images / "1731792-4731.jpg"
    img.write_bytes(b"")

    result = ingest_slab_export(excel, images)
    rec = result.records[0]
    assert rec.image_found is True
    assert rec.image_match_method == "serial_fallback"
    assert rec.image_id == "1731792-4731"


def test_dimensions_always_come_from_serial_not_image_filename(tmp_path: Path):
    """Even when the image filename happens to contain large numbers,
    the slab's dimensions are parsed from سریال کالا only."""
    excel = _write_excel(
        tmp_path / "export.xlsx",
        [
            {
                "کد کالا": "P-1",
                "سریال کالا": "1202002",  # 120cm × 200cm
                "شماره": 5,
                "مساحت (M2)": 2.4,
            }
        ],
    )
    images = tmp_path / "images"
    images.mkdir()
    # Filename contains misleading "999999" — must NOT be used for dims.
    (images / "999999-9999-5.jpeg").write_bytes(b"")

    result = ingest_slab_export(excel, images)
    rec = result.records[0]
    assert rec.image_found is True
    assert rec.height_cm == 120
    assert rec.width_cm == 200
    assert rec.height_mm == 1200
    assert rec.width_mm == 2000


def test_image_not_found_only_when_neither_path_works(tmp_path: Path):
    """image_not_found warning fires only after BOTH slab_number_suffix
    and serial_fallback have failed."""
    excel = _write_excel(
        tmp_path / "export.xlsx",
        [
            {
                "کد کالا": "P-1",
                "سریال کالا": "9999991-0000/AV-99",
                "شماره": 88,
                "مساحت (M2)": 1.0,
            }
        ],
    )
    images = tmp_path / "images"
    images.mkdir()
    # An image whose suffix is 5 and stem unrelated to serial — should not match.
    (images / "1234-5678-5.jpeg").write_bytes(b"")

    result = ingest_slab_export(excel, images)
    rec = result.records[0]
    assert rec.image_found is False
    assert rec.image_match_method == "not_found"
    assert "image_not_found" in rec.warnings


def test_suffix_tie_break_prefers_serial_overlap(tmp_path: Path):
    """When two images share the same suffix, prefer the one whose
    stem shares a long digit run with the serial."""
    excel = _write_excel(
        tmp_path / "export.xlsx",
        [
            {
                "کد کالا": "P-1",
                "سریال کالا": "1731792-4731/AV2040643-05",
                "شماره": 5,
                "مساحت (M2)": 3.10,
            }
        ],
    )
    images = tmp_path / "images"
    images.mkdir()
    # Both end in -5; the 1731792 one should win because its stem shares
    # the "1731792" run with the serial.
    (images / "9999-0000-5.jpeg").write_bytes(b"")
    expected = images / "1731792-4731-5.jpeg"
    expected.write_bytes(b"")

    result = ingest_slab_export(excel, images)
    rec = result.records[0]
    assert rec.image_found is True
    assert rec.image_path == str(expected)


def test_image_match_short_form_av_stripped(tmp_path):
    """End-to-end: a row matches an image filed under the AV-stripped stem."""
    excel = _write_excel(
        tmp_path / "export.xlsx",
        [
            {
                "کد کالا": "P-1",
                "سریال کالا": "1781722-4731/AV2040643-04",
                "مساحت (M2)": 3.062,
            }
        ],
    )
    images = tmp_path / "images"
    images.mkdir()
    img = images / "1781722-4731-04.jpg"
    img.write_bytes(b"")

    result = ingest_slab_export(excel, images)
    rec = result.records[0]
    assert rec.image_found is True
    assert rec.image_id == "1781722-4731-04"
    assert rec.image_match_method == "serial_fallback"
    assert rec.image_path == str(img)


# ---------------------------------------------------------------------------
# build_image_index
# ---------------------------------------------------------------------------


def test_build_image_index_scans_recursively_and_filters(tmp_path: Path):
    (tmp_path / "1731792-4731.jpg").write_bytes(b"")
    (tmp_path / "150300.PNG").write_bytes(b"")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "999888.webp").write_bytes(b"")
    (tmp_path / "ignore.txt").write_bytes(b"")  # not an image extension
    index = build_image_index(tmp_path)
    assert set(index.keys()) == {"1731792-4731", "150300", "999888"}


def test_build_image_index_missing_dir_returns_empty(tmp_path: Path):
    assert build_image_index(tmp_path / "does_not_exist") == {}


# ---------------------------------------------------------------------------
# ingest_slab_export — image matching by serial_number
# ---------------------------------------------------------------------------


def test_image_match_uses_serial_head_before_slash(tmp_path: Path):
    """Image is named after the portion of the serial before the slash —
    no slab_number present, so the serial fallback kicks in."""
    excel = _write_excel(
        tmp_path / "export.xlsx",
        [
            {
                "کد کالا": "P-9999",  # item_code is metadata only
                "سریال کالا": "1731792-4731/AV2040643-05",
                "مساحت (M2)": 3.10,
            }
        ],
    )
    images = tmp_path / "images"
    images.mkdir()
    img = images / "1731792-4731.jpg"
    img.write_bytes(b"")

    result = ingest_slab_export(excel, images)
    rec = result.records[0]
    assert rec.serial_number == "1731792-4731/AV2040643-05"
    assert rec.slab_id == "1731792-4731/AV2040643-05"
    assert rec.item_code == "P-9999"
    assert rec.image_id == "1731792-4731"  # the candidate that matched
    assert rec.image_found is True
    assert rec.image_match_method == "serial_fallback"
    assert rec.image_path == str(img)
    assert rec.height_cm == 173 and rec.width_cm == 179
    assert rec.height_mm == 1730 and rec.width_mm == 1790
    assert "invalid_serial_format" not in rec.warnings


def test_image_match_uses_digits_only_when_filename_is_digits(tmp_path: Path):
    """Image is named '1731792.jpg' — the first 7 digits of the serial.

    With slab_number absent the matcher falls through to serial-based
    candidate generation and the digits-only stem catches it.
    """
    excel = _write_excel(
        tmp_path / "export.xlsx",
        [
            {
                "کد کالا": "P-9999",
                "سریال کالا": "1731792-4731/AV2040643-05",
                "مساحت (M2)": 3.10,
            }
        ],
    )
    images = tmp_path / "images"
    images.mkdir()
    (images / "1731792.jpg").write_bytes(b"")

    result = ingest_slab_export(excel, images)
    rec = result.records[0]
    assert rec.image_found is True
    assert rec.image_id == "1731792"
    assert rec.image_match_method == "serial_fallback"


def test_item_code_is_not_used_for_image_matching(tmp_path: Path):
    """item_code must NEVER drive image matching in V1, even if a file
    with that stem happens to exist in the folder."""
    excel = _write_excel(
        tmp_path / "export.xlsx",
        [
            {
                "کد کالا": "PROD-42",
                "سریال کالا": "9999999/whatever",
                "مساحت (M2)": 1.0,
            }
        ],
    )
    images = tmp_path / "images"
    images.mkdir()
    (images / "PROD-42.jpg").write_bytes(b"")  # tempting decoy

    result = ingest_slab_export(excel, images)
    rec = result.records[0]
    assert rec.image_found is False
    assert rec.image_match_method == "not_found"
    assert "image_not_found" in rec.warnings


def test_item_code_is_not_used_for_dimension_parsing(tmp_path: Path):
    """item_code with valid-looking digits must NOT drive dimensions."""
    excel = _write_excel(
        tmp_path / "export.xlsx",
        [
            {
                "کد کالا": "120200",  # would parse to (120, 200) if used
                "سریال کالا": None,
                "مساحت (M2)": 2.4,
            }
        ],
    )
    images = tmp_path / "images"
    images.mkdir()
    result = ingest_slab_export(excel, images)
    rec = result.records[0]
    assert rec.height_cm is None
    assert rec.width_cm is None
    assert "missing_serial_number" in rec.warnings
    assert "could_not_parse_dimensions" in rec.warnings


# ---------------------------------------------------------------------------
# ingest_slab_export — happy path with metadata
# ---------------------------------------------------------------------------


def test_ingest_happy_path(tmp_path: Path):
    excel = _write_excel(
        tmp_path / "export.xlsx",
        [
            {"کد کالا": "P-1", "سریال کالا": "1202002", "مساحت (M2)": 2.40},
            {"کد کالا": "P-2", "سریال کالا": "1503002", "مساحت (M2)": 4.50},
        ],
    )
    images = tmp_path / "images"
    images.mkdir()
    (images / "1202002.jpg").write_bytes(b"")
    # second slab has no matching image

    result = ingest_slab_export(excel, images)
    r1, r2 = result.records

    assert r1.slab_id == "1202002"
    assert r1.serial_number == "1202002"
    assert r1.item_code == "P-1"
    assert r1.image_id == "1202002"
    assert r1.image_found is True
    assert r1.height_cm == 120 and r1.width_cm == 200
    assert r1.area_m2 == pytest.approx(2.40)
    assert r1.calculated_area_m2 == pytest.approx(2.40)
    assert r1.warnings == []

    assert r2.image_found is False
    assert "image_not_found" in r2.warnings


def test_slab_id_falls_back_to_item_code_when_no_serial(tmp_path: Path):
    # V1.1 requires either a serial or explicit dimensions — supply
    # explicit dimensions so the ``slab_id`` fallback to ``item_code``
    # is still exercised without the newer column-guard rejecting
    # the input outright.
    excel = _write_excel(
        tmp_path / "export.xlsx",
        [{"کد کالا": "P-1", "طول (CM)": 150, "عرض (CM)": 200,
          "مساحت (M2)": 3.0}],
    )
    images = tmp_path / "images"
    images.mkdir()
    result = ingest_slab_export(excel, images)
    rec = result.records[0]
    assert rec.slab_id == "P-1"
    assert rec.serial_number is None
    assert "missing_serial_number" in rec.warnings


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------


def test_warnings_missing_invalid_and_area_mismatch(tmp_path: Path):
    excel = _write_excel(
        tmp_path / "export.xlsx",
        [
            # area mismatch: serial says 120x200 = 2.4 m², Excel says 9.0
            {"کد کالا": "P-1", "سریال کالا": "1202002", "مساحت (M2)": 9.0},
            # missing serial (item_code present but irrelevant for dims)
            {"کد کالا": "P-2", "سریال کالا": None, "مساحت (M2)": 1.0},
            # bad serial (fewer than 6 digits)
            {"کد کالا": "P-3", "سریال کالا": "12X", "مساحت (M2)": 1.0},
            # missing area
            {"کد کالا": "P-4", "سریال کالا": "1202003", "مساحت (M2)": None},
        ],
    )
    images = tmp_path / "images"
    images.mkdir()
    result = ingest_slab_export(excel, images)

    assert "suspicious_area_mismatch" in result.records[0].warnings
    assert "missing_serial_number" in result.records[1].warnings
    assert "could_not_parse_dimensions" in result.records[1].warnings
    assert "invalid_serial_format" in result.records[2].warnings
    assert "could_not_parse_dimensions" in result.records[2].warnings
    assert "missing_area_m2" in result.records[3].warnings


def test_duplicate_slab_id_still_flagged(tmp_path: Path):
    """Two rows with the same serial_number → duplicate_slab_id on both."""
    excel = _write_excel(
        tmp_path / "export.xlsx",
        [
            {"کد کالا": "P-1", "سریال کالا": "1202002", "مساحت (M2)": 2.4},
            {"کد کالا": "P-1", "سریال کالا": "1202002", "مساحت (M2)": 2.4},
        ],
    )
    images = tmp_path / "images"
    images.mkdir()
    result = ingest_slab_export(excel, images)
    for rec in result.records:
        assert "duplicate_slab_id" in rec.warnings


def test_shared_item_code_alone_is_not_a_warning(tmp_path: Path):
    """Multiple slabs in the same product batch share item_code — expected,
    not a warning. duplicate_item_code must NEVER appear in V1."""
    excel = _write_excel(
        tmp_path / "export.xlsx",
        [
            # Same item_code (product), different serials (different slabs).
            {"کد کالا": "P-1", "سریال کالا": "1202002", "مساحت (M2)": 2.4},
            {"کد کالا": "P-1", "سریال کالا": "1503002", "مساحت (M2)": 4.5},
            {"کد کالا": "P-1", "سریال کالا": "2003002", "مساحت (M2)": 6.0},
        ],
    )
    images = tmp_path / "images"
    images.mkdir()
    result = ingest_slab_export(excel, images)
    for rec in result.records:
        assert "duplicate_item_code" not in rec.warnings
        assert "duplicate_slab_id" not in rec.warnings
    # And the warning code must not appear anywhere in the aggregate.
    assert "duplicate_item_code" not in result.warning_counts()


# ---------------------------------------------------------------------------
# Source-row index
# ---------------------------------------------------------------------------


def test_source_excel_row_is_1_indexed_with_header(tmp_path: Path):
    excel = _write_excel(
        tmp_path / "export.xlsx",
        [
            {"کد کالا": "P-1", "سریال کالا": "1202002", "مساحت (M2)": 2.4},
            {"کد کالا": "P-2", "سریال کالا": "1503002", "مساحت (M2)": 4.5},
        ],
    )
    images = tmp_path / "images"
    images.mkdir()
    result = ingest_slab_export(excel, images)
    assert result.records[0].source_excel_row == 2
    assert result.records[1].source_excel_row == 3


# ---------------------------------------------------------------------------
# write_outputs
# ---------------------------------------------------------------------------


def test_write_outputs_creates_csv_json_report(tmp_path: Path):
    excel = _write_excel(
        tmp_path / "export.xlsx",
        [
            {
                "کد کالا": "P-1",
                "سریال کالا": "1731792-4731/AV2040643-05",
                "مساحت (M2)": 3.10,
            }
        ],
    )
    images = tmp_path / "images"
    images.mkdir()
    (images / "1731792-4731.jpg").write_bytes(b"")

    result = ingest_slab_export(excel, images)
    out_dir = tmp_path / "out"
    paths = write_outputs(result, out_dir)

    assert paths["csv"].exists()
    assert paths["json"].exists()
    assert paths["report"].exists()

    data = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert data["record_count"] == 1
    rec = data["records"][0]
    assert rec["slab_id"] == "1731792-4731/AV2040643-05"
    assert rec["serial_number"] == "1731792-4731/AV2040643-05"
    assert rec["item_code"] == "P-1"
    assert rec["image_id"] == "1731792-4731"
    assert rec["image_found"] is True
    assert rec["image_match_method"] == "serial_fallback"
    assert rec["height_cm"] == 173 and rec["width_cm"] == 179

    csv_text = paths["csv"].read_text(encoding="utf-8-sig")
    header_line = csv_text.splitlines()[0]
    assert "slab_id" in header_line and "warnings" in header_line
    assert "1731792-4731/AV2040643-05" in csv_text

    report = paths["report"].read_text(encoding="utf-8")
    assert "Slab ingestion report" in report
    assert "سریال کالا" in report and "serial_number" in report


# ---------------------------------------------------------------------------
# Multi-sheet selection
# ---------------------------------------------------------------------------


def test_multi_sheet_auto_picks_largest(tmp_path: Path):
    path = tmp_path / "export.xlsx"
    with pd.ExcelWriter(path) as w:
        pd.DataFrame(
            [{"سریال کالا": "1202002", "مساحت (M2)": 2.4}]
        ).to_excel(w, index=False, sheet_name="Empty")
        pd.DataFrame(
            [
                {"سریال کالا": "1202002", "مساحت (M2)": 2.4},
                {"سریال کالا": "1503002", "مساحت (M2)": 4.5},
                {"سریال کالا": "2003002", "مساحت (M2)": 6.0},
            ]
        ).to_excel(w, index=False, sheet_name="Main")
    images = tmp_path / "images"
    images.mkdir()
    result = ingest_slab_export(path, images)
    assert result.sheet_name == "Main"
    assert len(result.records) == 3


def test_explicit_sheet_name(tmp_path: Path):
    path = tmp_path / "export.xlsx"
    with pd.ExcelWriter(path) as w:
        pd.DataFrame(
            [{"سریال کالا": "1202002", "مساحت (M2)": 2.4}]
        ).to_excel(w, index=False, sheet_name="A")
        pd.DataFrame(
            [
                {"سریال کالا": "1503002", "مساحت (M2)": 4.5},
                {"سریال کالا": "2003002", "مساحت (M2)": 6.0},
            ]
        ).to_excel(w, index=False, sheet_name="B")
    images = tmp_path / "images"
    images.mkdir()
    result = ingest_slab_export(path, images, sheet_name="A")
    assert result.sheet_name == "A"
    assert len(result.records) == 1


def test_unknown_sheet_raises(tmp_path: Path):
    path = _write_excel(
        tmp_path / "export.xlsx",
        [{"سریال کالا": "1202002", "مساحت (M2)": 2.4}],
    )
    images = tmp_path / "images"
    images.mkdir()
    with pytest.raises(KeyError):
        ingest_slab_export(path, images, sheet_name="ghost")
