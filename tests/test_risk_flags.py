"""Risk-flag detection.

The strategy's `Rules.min_piece_*` filter drops pieces below those
thresholds entirely. To force the risk evaluator to see a "bad" piece,
each fixture sets the hard filter to 0 and lets the soft thresholds in
`Rules.risk_thresholds` produce the warning.

Each fixture deliberately uses a tiny project polygon that the slab is
clipped down to, so the resulting piece has a known shape.
"""
import json
from pathlib import Path

import pytest
from shapely.geometry import Polygon

from placement_engine import engine
from placement_engine.exporters.json_exporter import write_output
from placement_engine.models import (
    PlacedPiece,
    ProjectInput,
    RiskThresholds,
    TextureTransform,
)
from placement_engine.scoring.risk import (
    annotate_pieces_with_risks,
    build_risk_review_markers,
    evaluate_piece,
)


# ---------------------------------------------------------------------------
# Direct evaluator tests — small, fast, isolated from the engine plumbing.
# ---------------------------------------------------------------------------


def _make_piece(coords: list[tuple[float, float]]) -> PlacedPiece:
    return PlacedPiece(
        piece_id="P001",
        slab_id="S001",
        project_polygon=coords,
        slab_polygon=coords,
        rotation=0.0,
        texture_transform=TextureTransform(
            uv_origin=(0.0, 0.0), uv_width=100.0, uv_height=100.0,
        ),
    )


def test_60x500_piece_flagged_as_narrow_even_though_area_passes():
    """Direct test of the narrow check.

    A 60 mm × 500 mm piece has area 30 000 mm² which is *below* the
    default risk min_piece_area (50 000), so it would also pick up
    `small_piece`. Lower min_piece_area for this test so we can prove the
    narrow flag fires independently of the small-area flag.
    """
    piece = _make_piece([(0, 0), (60, 0), (60, 500), (0, 500)])
    thresholds = RiskThresholds(min_piece_area=10_000)  # 30k passes
    flags = evaluate_piece(piece, thresholds)
    flag_types = {f.type for f in flags}
    assert "narrow_piece" in flag_types
    assert "small_piece" not in flag_types


def test_small_area_piece_flagged_as_small_piece():
    """Area below min_piece_area triggers small_piece, regardless of shape."""
    # 200 × 200 = 40 000 mm² < default 50 000.
    piece = _make_piece([(0, 0), (200, 0), (200, 200), (0, 200)])
    flags = evaluate_piece(piece, RiskThresholds())  # defaults
    assert any(f.type == "small_piece" for f in flags)


def test_long_thin_piece_flagged_for_aspect_ratio():
    """1000 × 100 piece has aspect 10 : 1, exceeding default 8.0."""
    piece = _make_piece([(0, 0), (1000, 0), (1000, 100), (0, 100)])
    thresholds = RiskThresholds(
        min_piece_area=0, min_piece_width=0, min_piece_height=0,
        max_aspect_ratio=8.0,
    )
    flags = evaluate_piece(piece, thresholds)
    assert any(f.type == "thin_aspect_ratio" for f in flags)


def test_irregular_piece_flagged_for_vertex_count():
    """A 7-vertex polygon exceeds the default max_vertex_count of 6."""
    # Trapezoid with a notch — 7 distinct vertices.
    poly_coords = [
        (0, 0), (1000, 0), (1000, 600), (700, 600),
        (700, 400), (300, 400), (0, 400),
    ]
    piece = _make_piece(poly_coords)
    flags = evaluate_piece(piece, RiskThresholds())
    assert any(f.type == "irregular_piece" for f in flags)


def test_clean_rectangle_has_no_flags():
    """A 2 m × 1 m piece should be unflagged with default thresholds."""
    piece = _make_piece([(0, 0), (2000, 0), (2000, 1000), (0, 1000)])
    flags = evaluate_piece(piece, RiskThresholds())
    assert flags == []


def test_annotate_returns_count_of_flagged_pieces():
    pieces = [
        _make_piece([(0, 0), (50, 0), (50, 50), (0, 50)]),       # small + narrow + short
        _make_piece([(0, 0), (2000, 0), (2000, 1000), (0, 1000)]),  # clean
    ]
    flagged = annotate_pieces_with_risks(pieces, RiskThresholds())
    assert flagged == 1
    assert pieces[0].risk_flags
    assert pieces[1].risk_flags == []


def test_review_markers_built_from_flagged_pieces():
    pieces = [
        _make_piece([(0, 0), (50, 0), (50, 50), (0, 50)]),
        _make_piece([(0, 0), (2000, 0), (2000, 1000), (0, 1000)]),
    ]
    annotate_pieces_with_risks(pieces, RiskThresholds())
    markers = build_risk_review_markers(pieces)
    assert len(markers) == 1
    m = markers[0]
    assert m.type == "piece_risk"
    assert m.related_piece_ids == ["P001"]
    assert m.location == (25.0, 25.0)  # centroid of 50×50 square at origin
    assert "small_piece" in m.message


# ---------------------------------------------------------------------------
# End-to-end: risk flags and markers must show up in the JSON output.
# ---------------------------------------------------------------------------


SLIVER_FIXTURE = {
    "project_id": "sliver_demo",
    "layout": {
        # Project is itself only 60 mm wide × 500 mm tall, so any slab
        # placed at the origin gets clipped to exactly 60 × 500.
        "boundary": [[0, 0], [60, 0], [60, 500], [0, 500]],
    },
    "slabs": [
        {"slab_id": "S001", "width": 1000, "height": 1000, "thickness": 20}
    ],
    # Hard filter disabled so the sliver reaches the risk module.
    "rules": {
        "min_piece_width": 0, "min_piece_height": 0, "min_piece_area": 0,
        # Lower the area threshold so we can isolate the narrow flag in
        # the JSON output assertions below.
        "risk_thresholds": {"min_piece_area": 10_000},
    },
}


@pytest.fixture
def sliver_output():
    pi = ProjectInput.model_validate(SLIVER_FIXTURE)
    return pi, engine.run(pi)


def test_engine_emits_risk_flags_in_output_json(sliver_output, tmp_path):
    _, output = sliver_output
    out_path = tmp_path / "out.json"
    write_output(output, out_path)
    raw = json.loads(out_path.read_text())

    pieces = raw["layout_options"][0]["placed_pieces"]
    assert len(pieces) == 1
    flags = pieces[0]["risk_flags"]
    flag_types = {f["type"] for f in flags}
    # The 60 × 500 sliver passes area (30k > 10k) but not width (60 < 150).
    assert "narrow_piece" in flag_types
    # JSON shape: each flag carries type / severity / message.
    for f in flags:
        assert set(f.keys()) >= {"type", "severity", "message"}


def test_engine_emits_piece_risk_review_markers(sliver_output):
    _, output = sliver_output
    markers = output.layout_options[0].review_markers
    risk_markers = [m for m in markers if m.type == "piece_risk"]
    assert len(risk_markers) == 1
    m = risk_markers[0]
    assert m.related_piece_ids == ["P001"]
    # Marker IDs start at R001 and form a contiguous sequence.
    assert m.review_id == "R001"


def test_small_piece_count_metric_matches_flagged_pieces():
    """`metrics.small_piece_count` must reflect the number of pieces
    carrying a `small_piece` flag — not just the total piece count."""
    fixture = {
        "project_id": "small_piece_count_demo",
        "layout": {"boundary": [[0, 0], [200, 0], [200, 200], [0, 200]]},
        "slabs": [{"slab_id": "S1", "width": 1000, "height": 1000, "thickness": 20}],
        "rules": {
            "min_piece_width": 0, "min_piece_height": 0, "min_piece_area": 0,
            # Defaults: min_piece_area=50 000, so 200×200=40 000 is small.
        },
    }
    pi = ProjectInput.model_validate(fixture)
    output = engine.run(pi)
    metrics = output.layout_options[0].metrics
    assert metrics.small_piece_count == 1
    assert metrics.piece_count == 1


def test_default_thresholds_produce_no_flags_on_shipped_examples():
    """Sanity: the bundled examples already have rectangular, sensibly-sized
    pieces. A risk evaluator that fires on them would be too sensitive."""
    examples = Path(__file__).resolve().parents[1] / "examples"
    for name in ("input_floor_simple.json", "input_floor_with_hole.json"):
        pi = engine.load_input_from_file(examples / name)
        output = engine.run(pi)
        for piece in output.layout_options[0].placed_pieces:
            # If this fires, either the example changed or the default
            # thresholds have drifted into being noisy.
            assert piece.risk_flags == [], (
                f"unexpected flags on {name} piece {piece.piece_id}: "
                f"{[f.type for f in piece.risk_flags]}"
            )
