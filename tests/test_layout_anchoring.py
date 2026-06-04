"""Tests for the layout anchor-selection layer."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from placement_engine.layout import (
    ANCHOR_AUTO,
    ANCHOR_BOTTOM_LEFT,
    ANCHOR_BOTTOM_RIGHT,
    ANCHOR_TOP_LEFT,
    ANCHOR_TOP_RIGHT,
    DEFAULT_CANDIDATE_MODES,
    SUPPORTED_ANCHOR_MODES,
    SliverPolicy,
    compute_anchor_origin,
    evaluate_layout,
    generate_tile_layout,
    generate_tile_layout_from_inventory,
    score_evaluation,
    write_layout_json,
)
from placement_engine.target_area import (
    TargetGeometry,
    load_target_geometry_from_dxf,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
EX_L = REPO_ROOT / "examples/cad_inputs/demo/demo_l_shape_floor.dxf"
EX_RECT = REPO_ROOT / "examples/cad_inputs/demo/demo_rectangle_floor.dxf"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


@dataclass
class _Slab:
    width_mm: float
    height_mm: float


def _real_inventory() -> list[_Slab]:
    """The 7 slabs from data/raw_test — median 1590 × 2200."""
    return [
        _Slab(1590, 1590), _Slab(1590, 1980), _Slab(1550, 2040),
        _Slab(1590, 2200), _Slab(1570, 2320),
        _Slab(1600, 2500), _Slab(1610, 2620),
    ]


def _rect(w: float, h: float) -> TargetGeometry:
    return TargetGeometry(
        target_id="rect", name="rect",
        boundary=[(0, 0), (w, 0), (w, h), (0, h)],
    )


# ---------------------------------------------------------------------------
# compute_anchor_origin — the four corners
# ---------------------------------------------------------------------------


def test_bottom_left_anchor_keeps_default_origin():
    """The historical default: origin at the bbox's bottom-left."""
    bbox = (0.0, 0.0, 1640.0, 1640.0)  # 1640 wide → leftover 50 mm
    ox, oy = compute_anchor_origin(bbox, 1590, 2200, ANCHOR_BOTTOM_LEFT)
    assert (ox, oy) == (0.0, 0.0)


def test_bottom_right_anchor_shifts_origin_left_by_leftover():
    """bbox 1640 wide, tile 1590 → 50 mm leftover. Right-anchor must
    shift origin to ``-(1590 - 50) = -1540`` so the rightmost tile
    boundary lands exactly on the right edge."""
    bbox = (0.0, 0.0, 1640.0, 1640.0)
    ox, oy = compute_anchor_origin(bbox, 1590, 2200, ANCHOR_BOTTOM_RIGHT)
    assert ox == pytest.approx(-1540.0)
    # Vertical leftover too: 1640 < 2200 → leftover = 1640, shift = 560.
    # Bottom-right keeps oy at the bbox bottom.
    assert oy == 0.0


def test_exact_divisor_collapses_all_anchors_to_origin():
    """A bbox that's an exact integer multiple of the tile size has
    zero leftover → every anchor mode collapses to the same origin."""
    bbox = (0.0, 0.0, 3180.0, 4400.0)  # 2×1590 by 2×2200
    bl = compute_anchor_origin(bbox, 1590, 2200, ANCHOR_BOTTOM_LEFT)
    br = compute_anchor_origin(bbox, 1590, 2200, ANCHOR_BOTTOM_RIGHT)
    tl = compute_anchor_origin(bbox, 1590, 2200, ANCHOR_TOP_LEFT)
    tr = compute_anchor_origin(bbox, 1590, 2200, ANCHOR_TOP_RIGHT)
    assert bl == br == tl == tr == (0.0, 0.0)


def test_unsupported_anchor_mode_raises():
    with pytest.raises(ValueError, match="unsupported anchor mode"):
        compute_anchor_origin((0, 0, 100, 100), 50, 50, "diagonal")


def test_anchor_supported_constants_cover_four_corners():
    assert set(SUPPORTED_ANCHOR_MODES) == {
        ANCHOR_BOTTOM_LEFT, ANCHOR_BOTTOM_RIGHT,
        ANCHOR_TOP_LEFT, ANCHOR_TOP_RIGHT,
    }


# ---------------------------------------------------------------------------
# evaluate_layout — sliver scoring
# ---------------------------------------------------------------------------


def test_evaluate_layout_counts_sliver_pieces_and_total_area():
    """Floor 1640 mm wide / tile 1590 → one 50 mm sliver column."""
    layout = generate_tile_layout(_rect(1640, 2200), 1590, 2200)
    ev = evaluate_layout(layout, anchor_mode=ANCHOR_BOTTOM_LEFT,
                         policy=SliverPolicy())
    assert ev.sliver_count == 1
    assert ev.uncuttable_piece_count == 1  # 50 mm < 100 mm threshold
    assert ev.min_sliver_width_mm == pytest.approx(50.0)
    assert ev.total_sliver_area_m2 == pytest.approx(50 * 2200 / 1e6)


def test_evaluate_layout_no_slivers_when_floor_matches_tile():
    """Exact divisor → no slivers, no uncuttable pieces, no edge pieces."""
    layout = generate_tile_layout(_rect(3180, 4400), 1590, 2200)
    ev = evaluate_layout(layout, anchor_mode=ANCHOR_BOTTOM_LEFT,
                         policy=SliverPolicy())
    assert ev.sliver_count == 0
    assert ev.uncuttable_piece_count == 0
    assert ev.edge_piece_count == 0
    assert ev.min_sliver_width_mm is None


def test_uncuttable_flag_catches_strips_above_sliver_area_threshold():
    """A 100×2200 mm strip is ~14% of a 1590×2200 tile (above the
    5% sliver-by-area threshold) but only 100 mm wide → still
    uncuttable under a 150 mm policy."""
    # 1690 mm wide → leftover = 100 mm in the right column.
    layout = generate_tile_layout(_rect(1690, 2200), 1590, 2200)
    # By area: 100*2200/1e6 = 0.22 m²; threshold = 0.05*3.498 = 0.175.
    # Above threshold → not flagged ``sliver`` by area.
    slivers = [p for p in layout.pieces if "sliver" in p.notes]
    assert slivers == []
    ev = evaluate_layout(
        layout, anchor_mode=ANCHOR_BOTTOM_LEFT,
        policy=SliverPolicy(min_sliver_width_mm=150.0),
    )
    # But still uncuttable because width < 150 mm policy.
    assert ev.uncuttable_piece_count == 1


def test_score_evaluation_orders_by_uncuttable_first_then_count_then_area():
    """Hand-built evaluations show the priority chain is strict."""
    from placement_engine.layout.anchoring import SliverEvaluation

    a = SliverEvaluation("a", sliver_count=5, uncuttable_piece_count=1,
                         total_sliver_area_m2=0.01,
                         min_sliver_width_mm=10.0, min_sliver_height_mm=10.0,
                         min_edge_piece_side_mm=200.0, edge_piece_count=5)
    b = SliverEvaluation("b", sliver_count=10, uncuttable_piece_count=0,
                         total_sliver_area_m2=10.0,
                         min_sliver_width_mm=50.0, min_sliver_height_mm=50.0,
                         min_edge_piece_side_mm=50.0, edge_piece_count=10)
    # b has 10× the slivers and waste but ZERO uncuttable — must beat a.
    assert score_evaluation(b) < score_evaluation(a)


# ---------------------------------------------------------------------------
# L-shape — the headline acceptance test
# ---------------------------------------------------------------------------


def test_l_shape_auto_selects_bottom_right_to_reduce_sliver_area():
    """The L-shape's right edge produces a 50 mm sliver under the
    historical bottom-left anchor. Anchoring from the right keeps the
    same sliver count but reduces total sliver area (the second sliver
    row only spans 400 mm vs 1800 mm), so the auto-selector must
    flip to ``bottom_right`` — exactly what the designer does by hand.
    """
    geom = load_target_geometry_from_dxf(EX_L)
    layout = generate_tile_layout_from_inventory(geom, _real_inventory())
    assert layout.anchor_mode == ANCHOR_BOTTOM_RIGHT
    # Both candidates must appear in the JSON trace.
    modes = {ev.anchor_mode for ev in layout.candidate_evaluations}
    assert modes == set(DEFAULT_CANDIDATE_MODES)
    selected = next(ev for ev in layout.candidate_evaluations if ev.selected)
    rejected = next(ev for ev in layout.candidate_evaluations if not ev.selected)
    assert selected.anchor_mode == ANCHOR_BOTTOM_RIGHT
    assert selected.total_sliver_area_m2 < rejected.total_sliver_area_m2


# ---------------------------------------------------------------------------
# Stability for the no-conflict case
# ---------------------------------------------------------------------------


def test_clean_rectangle_keeps_bottom_left_when_no_sliver_present():
    """A plain rectangle with no sliver problem stays on the default
    bottom-left anchor — tie-break by alphabetical mode (``bottom_left``
    sorts before ``bottom_right``) backed by the cleaner edge metric."""
    geom = _rect(6000, 4000)
    layout = generate_tile_layout_from_inventory(geom, _real_inventory())
    assert layout.anchor_mode == ANCHOR_BOTTOM_LEFT
    # No sliver-flagged piece on either side.
    for ev in layout.candidate_evaluations:
        assert ev.sliver_count == 0
        assert ev.uncuttable_piece_count == 0


def test_auto_selection_is_deterministic():
    """Same input → same anchor across repeated runs."""
    geom = load_target_geometry_from_dxf(EX_L)
    inv = _real_inventory()
    modes = {
        generate_tile_layout_from_inventory(geom, inv).anchor_mode
        for _ in range(5)
    }
    assert len(modes) == 1


# ---------------------------------------------------------------------------
# Less-bad selection when every candidate still has slivers
# ---------------------------------------------------------------------------


def test_when_both_anchors_produce_slivers_pick_lower_sliver_area():
    """A 1640 × 2200 mm rectangle with median 1590×2200 has the same
    50 mm sliver on either side, BUT in 1D both anchors produce a
    single sliver. Score should tie on count and area, and the
    tie-break must be deterministic (alphabetical → ``bottom_left``)."""
    geom = _rect(1640, 2200)
    layout = generate_tile_layout_from_inventory(geom, _real_inventory())
    # Tie on every cuttability metric → falls through to alphabetical.
    assert layout.anchor_mode == ANCHOR_BOTTOM_LEFT
    evs = sorted(
        layout.candidate_evaluations, key=lambda e: e.anchor_mode,
    )
    assert evs[0].sliver_count == evs[1].sliver_count
    assert evs[0].total_sliver_area_m2 == pytest.approx(
        evs[1].total_sliver_area_m2, abs=1e-9,
    )


# ---------------------------------------------------------------------------
# Explicit overrides keep the existing escape hatches
# ---------------------------------------------------------------------------


def test_explicit_anchor_mode_bypasses_selection():
    """Passing ``anchor_mode="bottom_right"`` forces that mode without
    running the candidate search."""
    geom = _rect(6000, 4000)
    layout = generate_tile_layout_from_inventory(
        geom, _real_inventory(), anchor_mode=ANCHOR_BOTTOM_RIGHT,
    )
    assert layout.anchor_mode == ANCHOR_BOTTOM_RIGHT
    # Only the selected mode is evaluated — no scan.
    assert len(layout.candidate_evaluations) == 1
    assert layout.candidate_evaluations[0].selected is True


def test_explicit_origin_disables_anchor_selection_entirely():
    """Passing an explicit ``origin=`` is the lowest-level escape
    hatch — it should bypass the selector and leave the anchor metadata
    at ``explicit_origin``."""
    geom = _rect(6000, 4000)
    layout = generate_tile_layout_from_inventory(
        geom, _real_inventory(), origin=(-300.0, 0.0),
    )
    assert layout.anchor_mode == "explicit_origin"
    assert layout.candidate_evaluations == []
    assert layout.origin == (-300.0, 0.0)


def test_unknown_anchor_mode_raises():
    geom = _rect(6000, 4000)
    with pytest.raises(ValueError, match="unsupported anchor_mode"):
        generate_tile_layout_from_inventory(
            geom, _real_inventory(), anchor_mode="diagonal",
        )


# ---------------------------------------------------------------------------
# Sliver policy is honoured + reaches JSON
# ---------------------------------------------------------------------------


def test_custom_sliver_policy_changes_uncuttable_threshold():
    """A 130 mm strip is uncuttable under 150 mm policy but cuttable
    under 100 mm policy."""
    # 1720 mm wide → 130 mm right leftover (1590 + 130).
    geom = _rect(1720, 2200)
    # Default 100 mm policy → 130 mm is fine.
    base = generate_tile_layout_from_inventory(
        geom, _real_inventory(), anchor_mode=ANCHOR_BOTTOM_LEFT,
    )
    assert base.candidate_evaluations[0].uncuttable_piece_count == 0
    # Stricter 150 mm policy → 130 mm becomes uncuttable.
    strict = generate_tile_layout_from_inventory(
        geom, _real_inventory(), anchor_mode=ANCHOR_BOTTOM_LEFT,
        sliver_policy=SliverPolicy(min_sliver_width_mm=150.0,
                                   min_sliver_height_mm=150.0),
    )
    assert strict.candidate_evaluations[0].uncuttable_piece_count == 1


def test_layout_json_includes_anchor_and_sliver_policy(tmp_path: Path):
    """Every new grid field is present in the JSON output and round-trips."""
    geom = load_target_geometry_from_dxf(EX_L)
    layout = generate_tile_layout_from_inventory(geom, _real_inventory())
    path = write_layout_json(layout, tmp_path / "layout.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    grid = data["grid"]
    assert grid["anchor_mode"] == ANCHOR_BOTTOM_RIGHT
    assert grid["min_sliver_width_mm"] == 100.0
    assert grid["min_sliver_height_mm"] == 100.0
    assert grid["sliver_policy"] == {
        "min_sliver_width_mm": 100.0, "min_sliver_height_mm": 100.0,
    }
    cand = grid["candidate_evaluations"]
    assert len(cand) == 2
    assert {c["anchor_mode"] for c in cand} == set(DEFAULT_CANDIDATE_MODES)
    selected = [c for c in cand if c["selected"]]
    assert len(selected) == 1
    assert selected[0]["anchor_mode"] == ANCHOR_BOTTOM_RIGHT


def test_explicit_generate_tile_layout_has_null_anchor_fields(tmp_path: Path):
    """The lower-level entry point doesn't run the selector — JSON
    should record ``anchor_mode = null`` rather than guessing."""
    layout = generate_tile_layout(_rect(2400, 1200), 1200, 600)
    path = write_layout_json(layout, tmp_path / "layout.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    grid = data["grid"]
    assert grid["anchor_mode"] is None
    assert grid["sliver_policy"] is None
    assert grid["candidate_evaluations"] == []


# ---------------------------------------------------------------------------
# ANCHOR_AUTO sentinel
# ---------------------------------------------------------------------------


def test_anchor_auto_is_the_default():
    """Default ``anchor_mode`` must equal ``ANCHOR_AUTO`` — the only
    way the L-shape gets the designer-style right anchoring is if
    auto-selection runs without the caller asking."""
    geom = load_target_geometry_from_dxf(EX_L)
    explicit_auto = generate_tile_layout_from_inventory(
        geom, _real_inventory(), anchor_mode=ANCHOR_AUTO,
    )
    default = generate_tile_layout_from_inventory(geom, _real_inventory())
    assert explicit_auto.anchor_mode == default.anchor_mode
