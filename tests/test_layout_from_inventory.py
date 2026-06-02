"""Tests for `generate_tile_layout_from_inventory` and the basis fields."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from placement_engine.layout import (
    LAYOUT_BASIS_EXPLICIT,
    LAYOUT_BASIS_INVENTORY_MEDIAN,
    generate_tile_layout,
    generate_tile_layout_from_inventory,
    write_layout_json,
)
from placement_engine.target_area import TargetGeometry


@dataclass
class _Slab:
    width_mm: float
    height_mm: float


def _rect(w: float, h: float) -> TargetGeometry:
    return TargetGeometry(
        target_id="rect", name="rect",
        boundary=[(0, 0), (w, 0), (w, h), (0, h)],
    )


def _real_inventory() -> list[_Slab]:
    """The 7 slabs from data/raw_test — median 1590 × 2200."""
    return [
        _Slab(1590, 1590), _Slab(1590, 1980), _Slab(1550, 2040),
        _Slab(1590, 2200), _Slab(1570, 2320),
        _Slab(1600, 2500), _Slab(1610, 2620),
    ]


# ---------------------------------------------------------------------------
# Default behaviour: inventory-median
# ---------------------------------------------------------------------------


def test_default_tile_size_comes_from_inventory_median():
    """generate_tile_layout_from_inventory picks the median, not 1200×600."""
    layout = generate_tile_layout_from_inventory(_rect(6000, 4000), _real_inventory())
    assert layout.tile_width_mm == 1590.0
    assert layout.tile_height_mm == 2200.0
    assert layout.layout_basis == LAYOUT_BASIS_INVENTORY_MEDIAN
    assert layout.inventory_dimension_summary is not None
    assert layout.inventory_dimension_summary.slab_count == 7


def test_inventory_layout_attaches_source_path_when_supplied():
    layout = generate_tile_layout_from_inventory(
        _rect(6000, 4000), _real_inventory(),
        source_inventory_path="outputs/slab_ingestion_test/clean_slabs.json",
    )
    assert layout.source_inventory_path == "outputs/slab_ingestion_test/clean_slabs.json"


def test_default_rectangle_yields_far_fewer_pieces_than_1200x600():
    """The headline acceptance criterion: median 1590×2200 on a 6×4 m
    rectangle produces ≤ 10 pieces, not ~35."""
    layout = generate_tile_layout_from_inventory(_rect(6000, 4000), _real_inventory())
    # 4 cols × 2 rows = 8 pieces at most.
    assert 4 <= len(layout.pieces) <= 10
    assert layout.total_actual_area_m2 == pytest.approx(24.0, abs=0.01)


def test_explicit_tile_dimensions_still_supported_with_explicit_basis():
    layout = generate_tile_layout(_rect(6000, 4000), 1200, 600)
    assert layout.layout_basis == LAYOUT_BASIS_EXPLICIT
    assert layout.source_inventory_path is None
    assert layout.inventory_dimension_summary is None


# ---------------------------------------------------------------------------
# JSON shape — schema contract for downstream consumers
# ---------------------------------------------------------------------------


def test_layout_json_includes_basis_source_and_summary(tmp_path: Path):
    layout = generate_tile_layout_from_inventory(
        _rect(6000, 4000), _real_inventory(),
        source_inventory_path="some/clean_slabs.json",
    )
    path = write_layout_json(layout, tmp_path / "layout.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    grid = data["grid"]
    assert grid["layout_basis"] == "inventory_median"
    assert grid["source_inventory_path"] == "some/clean_slabs.json"
    summary = grid["inventory_dimension_summary"]
    assert summary["slab_count"] == 7
    assert summary["median_width_mm"] == 1590.0
    assert summary["median_height_mm"] == 2200.0


def test_explicit_layout_json_marks_basis_explicit_and_omits_inventory(tmp_path: Path):
    layout = generate_tile_layout(_rect(6000, 4000), 1200, 600)
    path = write_layout_json(layout, tmp_path / "layout.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    grid = data["grid"]
    assert grid["layout_basis"] == "explicit"
    assert grid["source_inventory_path"] is None
    assert grid["inventory_dimension_summary"] is None


# ---------------------------------------------------------------------------
# Acceptance correctness — clipping invariants survive inventory tile size
# ---------------------------------------------------------------------------


def test_inventory_layout_still_covers_usable_area_exactly():
    """The median-tile layout still satisfies area conservation."""
    layout = generate_tile_layout_from_inventory(_rect(6000, 4000), _real_inventory())
    assert layout.total_actual_area_m2 == pytest.approx(24.0, abs=0.01)
    assert layout.coverage_percentage == pytest.approx(100.0, abs=0.05)


def test_empty_inventory_factory_raises_clearly():
    with pytest.raises(ValueError, match="positive dimensions"):
        generate_tile_layout_from_inventory(_rect(6000, 4000), [])
