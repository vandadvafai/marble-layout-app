"""Tests for `placement_engine.preview.seam_detect` — pure-function seam math."""

from __future__ import annotations

import pytest

from placement_engine.preview.schema import PlacedSlabView
from placement_engine.preview.seam_detect import detect_seams


def _p(slab_id: str, x: float, y: float, w: float, h: float, idx: int = 1) -> PlacedSlabView:
    return PlacedSlabView(
        slab_id=slab_id, display_index=idx,
        x_mm=x, y_mm=y, width_mm=w, height_mm=h,
        image_path=None, image_source="placeholder",
    )


# ---------------------------------------------------------------------------
# Vertical seams
# ---------------------------------------------------------------------------


def test_two_slabs_flush_share_a_vertical_seam():
    """A.right = B.left, same height."""
    seams = detect_seams([
        _p("A", 0, 0, 1000, 800, idx=1),
        _p("B", 1000, 0, 1000, 800, idx=2),
    ])
    assert len(seams) == 1
    s = seams[0]
    assert s.from_slab_id == "A" and s.to_slab_id == "B"
    assert s.x0_mm == 1000.0 and s.x1_mm == 1000.0
    assert s.length_mm == pytest.approx(800.0)


def test_partial_vertical_overlap_seam_is_only_the_shared_strip():
    """A.right = B.left but B starts higher. Seam is the y-overlap only."""
    seams = detect_seams([
        _p("A", 0, 0, 1000, 800, idx=1),
        _p("B", 1000, 200, 1000, 800, idx=2),
    ])
    assert len(seams) == 1
    s = seams[0]
    # Overlap is y in [200, 800] → length 600.
    assert s.y0_mm == pytest.approx(200.0)
    assert s.y1_mm == pytest.approx(800.0)
    assert s.length_mm == pytest.approx(600.0)


def test_corner_touch_is_not_a_seam():
    """A's top-right corner exactly meets B's bottom-left → point contact only."""
    seams = detect_seams([
        _p("A", 0, 0, 1000, 800, idx=1),
        _p("B", 1000, 800, 1000, 800, idx=2),
    ])
    assert seams == []


# ---------------------------------------------------------------------------
# Horizontal seams
# ---------------------------------------------------------------------------


def test_two_slabs_stacked_share_a_horizontal_seam():
    seams = detect_seams([
        _p("A", 0, 0, 1000, 800, idx=1),
        _p("B", 0, 800, 1000, 800, idx=2),
    ])
    assert len(seams) == 1
    s = seams[0]
    assert s.x0_mm == 0.0 and s.x1_mm == 1000.0
    assert s.y0_mm == s.y1_mm == 800.0
    assert s.length_mm == pytest.approx(1000.0)


# ---------------------------------------------------------------------------
# Multi-slab + non-adjacent
# ---------------------------------------------------------------------------


def test_non_adjacent_slabs_produce_no_seam():
    seams = detect_seams([
        _p("A", 0, 0, 1000, 800, idx=1),
        _p("B", 2000, 0, 1000, 800, idx=2),  # gap of 1000 mm
    ])
    assert seams == []


def test_three_slabs_in_a_row_emit_two_seams():
    seams = detect_seams([
        _p("A", 0, 0, 1000, 800, idx=1),
        _p("B", 1000, 0, 1000, 800, idx=2),
        _p("C", 2000, 0, 1000, 800, idx=3),
    ])
    assert len(seams) == 2
    ids = {(s.from_slab_id, s.to_slab_id) for s in seams}
    assert ids == {("A", "B"), ("B", "C")}


def test_tolerance_treats_sub_mm_drift_as_touching():
    seams = detect_seams([
        _p("A", 0, 0, 1000, 800, idx=1),
        _p("B", 1000.4, 0, 1000, 800, idx=2),  # 0.4 mm gap
    ], tol_mm=1.0)
    assert len(seams) == 1


def test_large_gap_not_treated_as_seam_even_with_tolerance():
    seams = detect_seams([
        _p("A", 0, 0, 1000, 800, idx=1),
        _p("B", 1005, 0, 1000, 800, idx=2),  # 5 mm gap
    ], tol_mm=1.0)
    assert seams == []


def test_empty_input_returns_empty_seams():
    assert detect_seams([]) == []
    assert detect_seams([_p("A", 0, 0, 100, 100, idx=1)]) == []
