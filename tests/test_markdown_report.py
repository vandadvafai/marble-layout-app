"""Markdown report — section presence + content addresses for warnings."""
from pathlib import Path

import pytest

from placement_engine import engine
from placement_engine.exporters.markdown_report import write_report

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.fixture
def corridor_report(tmp_path):
    pi = engine.load_input_from_file(
        EXAMPLES / "input_lowest_waste_corridor_offcut.json"
    )
    output = engine.run(pi)
    option = next(o for o in output.layout_options if o.strategy == "lowest_waste")
    target = tmp_path / "report.md"
    written = write_report(pi, output, option, target)
    return option, written, written.read_text()


def test_report_file_is_created(corridor_report):
    _, path, _ = corridor_report
    assert path.exists()
    assert path.stat().st_size > 512


def test_report_includes_project_id_and_status_fields(corridor_report):
    _, _, body = corridor_report
    # Title carries the project id verbatim.
    assert "marble_floor_lowest_waste_corridor_offcut_001" in body
    # Status fields are upper-cased in the summary so they're easy to scan.
    assert "PARTIAL" in body
    assert "INSUFFICIENT" in body
    assert "Coverage:" in body
    assert "Slab waste:" in body


def test_report_has_required_sections(corridor_report):
    _, _, body = corridor_report
    for header in (
        "## Summary",
        "## Metrics",
        "## Pieces",
        "## Seams",
        "## Designer Review Notes",
        "## Risk Flags",
        "## Notes & Limitations",
    ):
        assert header in body, f"missing section: {header}"


def test_report_pieces_table_lists_every_piece(corridor_report):
    option, _, body = corridor_report
    for piece in option.placed_pieces:
        assert f"`{piece.piece_id}`" in body, (
            f"missing piece row for {piece.piece_id}"
        )


def test_report_review_notes_carry_addresses(corridor_report):
    _, _, body = corridor_report
    # Layout-level markers should be flagged as such, not faked with a
    # bogus coordinate.
    assert "_layout-level — no specific point_" in body
    # A suggested action accompanies every marker subsection.
    assert "Suggested action" in body


def test_report_warns_about_draft_status(corridor_report):
    _, _, body = corridor_report
    assert "AI-generated first-draft" in body
    assert "DXF is intended as an **editable review draft**" in body
