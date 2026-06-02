"""Tests for `placement_engine.layout.inventory_stats`."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from placement_engine.layout import (
    InventoryDimensionSummary,
    compute_inventory_dimension_summary,
)


@dataclass
class _Slab:
    """Minimal duck-type matching `HasDimensions`."""

    width_mm: float
    height_mm: float


def _inv(*pairs: tuple[float, float]) -> list[_Slab]:
    return [_Slab(width_mm=w, height_mm=h) for w, h in pairs]


# ---------------------------------------------------------------------------
# basic stats
# ---------------------------------------------------------------------------


def test_median_for_real_avandad_inventory():
    """The 7 slabs from data/raw_test → median width 1590, height 2200."""
    slabs = _inv(
        (1590, 1590), (1590, 1980), (1550, 2040),
        (1590, 2200), (1570, 2320),
        (1600, 2500), (1610, 2620),
    )
    s = compute_inventory_dimension_summary(slabs)
    assert isinstance(s, InventoryDimensionSummary)
    assert s.slab_count == 7
    assert s.median_width_mm == 1590.0
    assert s.median_height_mm == 2200.0
    assert s.min_width_mm == 1550.0
    assert s.max_width_mm == 1610.0
    assert s.min_height_mm == 1590.0
    assert s.max_height_mm == 2620.0


def test_mean_and_median_can_differ():
    """One outlier shifts the mean but not the median — sanity check."""
    slabs = _inv((100, 100), (100, 100), (100, 100), (1000, 1000))
    s = compute_inventory_dimension_summary(slabs)
    assert s.median_width_mm == 100.0
    assert s.mean_width_mm == pytest.approx(325.0)


def test_mode_returned_when_value_repeats():
    slabs = _inv((1590, 1590), (1590, 1980), (1590, 2200), (1610, 2620))
    s = compute_inventory_dimension_summary(slabs)
    # width 1590 appears 3 times → mode 1590 ×3.
    assert s.mode_width_mm == 1590.0
    assert s.mode_width_count == 3


def test_mode_is_none_when_no_value_repeats():
    """No duplicates → no meaningful mode."""
    slabs = _inv((1500, 1600), (1700, 1800), (1900, 2000))
    s = compute_inventory_dimension_summary(slabs)
    assert s.mode_width_mm is None
    assert s.mode_width_count is None
    assert s.mode_height_mm is None
    assert s.mode_height_count is None


def test_single_slab_inventory():
    """A one-slab inventory still produces valid stats (median = mean = that slab)."""
    slabs = _inv((1500, 2200))
    s = compute_inventory_dimension_summary(slabs)
    assert s.slab_count == 1
    assert s.median_width_mm == 1500.0
    assert s.mean_width_mm == 1500.0


def test_empty_inventory_raises():
    with pytest.raises(ValueError, match="positive dimensions"):
        compute_inventory_dimension_summary([])


def test_non_positive_slabs_are_ignored():
    """Defensive: zero/negative dims (shouldn't reach here, but…) drop out."""
    slabs = _inv((1500, 1500), (0, 1500), (-10, 1500), (1700, 1700))
    s = compute_inventory_dimension_summary(slabs)
    # Only the two valid slabs counted.
    assert s.slab_count == 2
    assert s.median_width_mm == 1600.0  # mean of 1500 and 1700


def test_summary_to_dict_round_trip():
    slabs = _inv((1500, 1500), (1600, 1600), (1700, 1700))
    s = compute_inventory_dimension_summary(slabs)
    d = s.to_dict()
    assert d["median_width_mm"] == 1600.0
    assert d["slab_count"] == 3
