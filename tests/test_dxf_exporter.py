"""DXF exporter — layer set + entity counts + cleanliness contract.

These tests don't try to validate the DXF visually (that's what
Rhino/AutoCAD is for). They lock down the file contract: required
layers exist, every piece becomes a closed polyline, every seam
becomes a line/polyline, and labels exist.
"""
from pathlib import Path

import ezdxf
import pytest

from placement_engine import engine
from placement_engine.exporters.dxf_exporter import write_dxf

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"

REQUIRED_LAYERS = {
    "PROJECT_BOUNDARY",
    "HOLES_CUTOUTS",
    "SLAB_PIECES",
    "OFFCUT_PIECES",
    "SEAMS",
    "PIECE_LABELS",
    "REVIEW_REFERENCE_POINTS",
}


@pytest.fixture
def corridor_dxf(tmp_path):
    """Run lowest_waste on the corridor and write a DXF for it."""
    pi = engine.load_input_from_file(
        EXAMPLES / "input_lowest_waste_corridor_offcut.json"
    )
    output = engine.run(pi)
    option = next(o for o in output.layout_options if o.strategy == "lowest_waste")
    target = tmp_path / "layout.dxf"
    written = write_dxf(pi, option, target)
    return option, written


def test_dxf_file_is_created(corridor_dxf):
    _, path = corridor_dxf
    assert path.exists()
    assert path.stat().st_size > 1024


def test_dxf_has_all_required_layers(corridor_dxf):
    _, path = corridor_dxf
    doc = ezdxf.readfile(path)
    layers = {layer.dxf.name for layer in doc.layers}
    missing = REQUIRED_LAYERS - layers
    assert not missing, f"missing layers: {missing}"


def test_dxf_has_one_closed_polyline_per_piece(corridor_dxf):
    option, path = corridor_dxf
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    piece_polylines = [
        e for e in msp
        if e.dxftype() == "LWPOLYLINE"
        and e.dxf.layer in ("SLAB_PIECES", "OFFCUT_PIECES")
    ]
    assert len(piece_polylines) == len(option.placed_pieces)
    for pl in piece_polylines:
        assert pl.closed, f"polyline on layer {pl.dxf.layer} is not closed"


def test_dxf_has_seam_entities_when_seams_exist(corridor_dxf):
    option, path = corridor_dxf
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    seams = [
        e for e in msp
        if e.dxf.layer == "SEAMS"
        and e.dxftype() in ("LINE", "LWPOLYLINE")
    ]
    assert option.seams, "fixture should produce seams"
    assert len(seams) == len(option.seams)


def test_dxf_has_piece_id_labels(corridor_dxf):
    option, path = corridor_dxf
    doc = ezdxf.readfile(path)
    label_texts = [
        e.dxf.text for e in doc.modelspace()
        if e.dxftype() == "TEXT" and e.dxf.layer == "PIECE_LABELS"
    ]
    # Each piece contributes two label entries (id on top, slab id below).
    for piece in option.placed_pieces:
        assert piece.piece_id in label_texts, (
            f"missing label text for {piece.piece_id}"
        )


def test_dxf_skips_layout_level_review_markers(tmp_path):
    """Layout-level markers (`location=None`) are explained in the
    Markdown report, not the DXF — the DXF must stay visually clean."""
    pi = engine.load_input_from_file(EXAMPLES / "input_insufficient_slabs.json")
    output = engine.run(pi)
    option = output.layout_options[0]
    # Sanity: this fixture must produce a layout-level marker.
    assert any(m.location is None for m in option.review_markers)

    target = tmp_path / "layout.dxf"
    write_dxf(pi, option, target)
    doc = ezdxf.readfile(target)
    msp = doc.modelspace()
    points = [e for e in msp if e.dxf.layer == "REVIEW_REFERENCE_POINTS"]
    # Only piece-level markers (with a real `location`) should land in the DXF.
    expected_points = [m for m in option.review_markers if m.location is not None]
    assert len(points) == len(expected_points)
