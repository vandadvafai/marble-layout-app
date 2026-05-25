"""PDF designer report.

These tests don't try to validate the visual layout of the PDF —
ReportLab is responsible for that. They lock down the contract: the
file is a real PDF, has multiple pages when there's enough content,
contains the key text strings, and handles the with/without-preview
and with/without-markers cases without crashing.

The text content is extracted by reading the uncompressed text streams
in the PDF; ReportLab writes literal `(text) Tj` operators so a simple
byte-level search is reliable enough for our acceptance checks.
"""
import re
from pathlib import Path

import pytest

from placement_engine import engine
from placement_engine.exporters.pdf_report import write_pdf_report

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _pdf_text(path: Path) -> str:
    """Concatenated extracted text from every page of the PDF.

    Uses pypdf — a small pure-Python PDF library — so the tests don't
    depend on the exact encoding ReportLab picked for the content
    streams (ASCII-85 + Flate by default, which is awkward to decode
    by hand).
    """
    import pypdf  # noqa: WPS433 — test-time import keeps it out of runtime
    reader = pypdf.PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def hole_output():
    pi = engine.load_input_from_file(EXAMPLES / "input_floor_with_hole.json")
    return pi, engine.run(pi)


@pytest.fixture
def insufficient_output():
    pi = engine.load_input_from_file(EXAMPLES / "input_insufficient_slabs.json")
    return pi, engine.run(pi)


# ---------------------------------------------------------------------------
# Core acceptance criteria
# ---------------------------------------------------------------------------


def test_pdf_is_real_pdf(tmp_path, hole_output):
    pi, out = hole_output
    target = tmp_path / "report.pdf"
    written = write_pdf_report(pi, out, out.layout_options[0], target)
    assert written == target
    assert target.is_file()
    assert target.stat().st_size > 1024
    assert target.read_bytes().startswith(b"%PDF-")


def test_pdf_contains_project_strategy_and_headline_metrics(tmp_path, hole_output):
    pi, out = hole_output
    option = out.layout_options[0]
    target = tmp_path / "report.pdf"
    write_pdf_report(pi, out, option, target)
    text = _pdf_text(target)

    assert pi.project_id in text
    assert option.strategy in text
    assert "Coverage" in text
    assert "Slab waste" in text or "waste" in text.lower()
    # The status banner shows the layout/inventory uppercase.
    assert option.metrics.layout_status.upper() in text
    assert option.metrics.inventory_status.upper() in text


def test_pdf_includes_review_and_limitations_headings(tmp_path, hole_output):
    pi, out = hole_output
    target = tmp_path / "report.pdf"
    write_pdf_report(pi, out, out.layout_options[0], target)
    text = _pdf_text(target)
    assert "Designer review notes" in text
    assert "Piece schedule" in text
    # "&" gets PDF-encoded as "&amp;" via reportlab — match either form.
    assert "limitations" in text.lower()


def test_pdf_works_when_preview_is_absent(tmp_path, hole_output):
    pi, out = hole_output
    target = tmp_path / "no_preview.pdf"
    write_pdf_report(pi, out, out.layout_options[0], target, preview_path=None)
    assert target.is_file()
    assert target.read_bytes().startswith(b"%PDF-")


def test_pdf_embeds_preview_when_present(tmp_path, hole_output):
    pi, out = hole_output
    # Render the preview the way the package exporter does, then embed it.
    from placement_engine.visualization.debug_plot import render_layout
    preview = tmp_path / "preview.png"
    render_layout(pi, out, preview, option_index=0)

    target = tmp_path / "with_preview.pdf"
    write_pdf_report(pi, out, out.layout_options[0], target, preview_path=preview)

    # Embedded image makes the PDF larger than the preview-less variant.
    target_no_img = tmp_path / "no_preview.pdf"
    write_pdf_report(pi, out, out.layout_options[0], target_no_img,
                     preview_path=None)
    assert target.stat().st_size > target_no_img.stat().st_size


def test_pdf_works_with_no_review_markers_or_risk_flags(tmp_path):
    """The simple-floor example produces no markers and no risk flags."""
    pi = engine.load_input_from_file(EXAMPLES / "input_floor_simple.json")
    out = engine.run(pi)
    option = out.layout_options[0]
    assert option.review_markers == []
    assert all(p.risk_flags == [] for p in option.placed_pieces)

    target = tmp_path / "clean.pdf"
    write_pdf_report(pi, out, option, target)
    text = _pdf_text(target)
    assert "No review markers" in text
    assert "No piece-level risk flags" in text


def test_pdf_works_with_review_markers_and_risk_flags(tmp_path, insufficient_output):
    """The insufficient-slabs fixture produces coverage + inventory markers."""
    pi, out = insufficient_output
    option = out.layout_options[0]
    assert option.review_markers  # fixture sanity
    target = tmp_path / "with_markers.pdf"
    write_pdf_report(pi, out, option, target)
    text = _pdf_text(target)
    # Marker types render as Title Case in the heading.
    assert "Incomplete Coverage" in text or "Insufficient Inventory" in text


def test_pdf_is_multipage_when_content_warrants(tmp_path, hole_output):
    """The hole example has 8 pieces; a PageBreak before the piece
    schedule guarantees ≥ 2 pages."""
    pi, out = hole_output
    target = tmp_path / "multi.pdf"
    write_pdf_report(pi, out, out.layout_options[0], target)
    # Count `/Type /Page` occurrences — one per page.
    blob = target.read_bytes()
    page_count = blob.count(b"/Type /Page\n") + blob.count(b"/Type/Page\n")
    # ReportLab sometimes encodes differently; fall back to looking
    # for a Pages /Count entry.
    if page_count < 2:
        m = re.search(rb"/Count\s+(\d+)", blob)
        assert m, "could not determine page count"
        page_count = int(m.group(1))
    assert page_count >= 2
