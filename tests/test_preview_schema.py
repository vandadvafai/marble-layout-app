"""Tests for `placement_engine.preview.schema` — adapters + JSON contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from placement_engine.inventory.blf_pack import blf_pack
from placement_engine.inventory.model import InventorySlab
from placement_engine.inventory.polygon_pack import polygon_pack
from placement_engine.inventory.shelf_pack import shelf_pack
from placement_engine.preview.schema import (
    PlacedSlabView,
    PlacementView,
    TargetView,
    view_from_blf_pack_result,
    view_from_polygon_pack_result,
    view_from_shelf_pack_result,
    write_placement_json,
)
from placement_engine.target_area import TargetArea, TargetGeometry


def _slab(slab_id: str, w: float, h: float) -> InventorySlab:
    return InventorySlab(
        slab_id=slab_id,
        serial_number=None, slab_number=None, item_code=None,
        width_mm=w, height_mm=h,
        area_m2=w * h / 1e6, calculated_area_m2=w * h / 1e6,
        image_path=None, image_available=False,
        image_placeholder_reason=None, source_excel_row=None,
    )


def _rect_geom(w: float, h: float) -> TargetGeometry:
    return TargetGeometry(
        target_id="rect", name="rect",
        boundary=[(0.0, 0.0), (w, 0.0), (w, h), (0.0, h)],
    )


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


def test_view_from_shelf_pack_result_returns_normalized_view():
    target = TargetArea(target_id="t", name="t", width_mm=4000, height_mm=3000)
    result = shelf_pack([_slab("A", 1000, 800)], target)
    view = view_from_shelf_pack_result(result)
    assert isinstance(view, PlacementView)
    assert isinstance(view.target, TargetView)
    assert view.target.target_id == "t"
    assert view.target.usable_area_m2 == pytest.approx(12.0)
    assert view.placed_count == 1
    p = view.placements[0]
    assert isinstance(p, PlacedSlabView)
    assert p.display_index == 1
    assert (p.x_mm, p.y_mm, p.width_mm, p.height_mm) == (0.0, 0.0, 1000.0, 800.0)
    assert view.metadata["packer"] == "shelf_pack"


def test_view_from_polygon_pack_carries_rejected_with_reasons():
    target = _rect_geom(2000, 2000)
    too_wide = _slab("WIDE", 3000, 1000)
    view = view_from_polygon_pack_result(polygon_pack([too_wide], target))
    assert view.placed_count == 0
    assert view.rejected_count == 1
    rj = view.rejected[0]
    assert rj.reason == "too_large_for_bbox"


def test_view_from_blf_pack_carries_runtime_and_grid_step():
    target = _rect_geom(4000, 3000)
    view = view_from_blf_pack_result(
        blf_pack([_slab("A", 1000, 800)], target, grid_step_mm=100.0)
    )
    assert view.metadata["packer"] == "blf_pack"
    assert view.metadata["grid_step_mm"] == 100.0
    assert "runtime_seconds" in view.metadata


def test_display_index_is_one_based_and_sequential():
    target = _rect_geom(6000, 4000)
    slabs = [_slab(f"S{i}", 1500, 1000) for i in range(3)]
    view = view_from_blf_pack_result(blf_pack(slabs, target))
    indices = [p.display_index for p in view.placements]
    assert indices == list(range(1, len(view.placements) + 1))


# ---------------------------------------------------------------------------
# Derived metrics
# ---------------------------------------------------------------------------


def test_coverage_percentage_uses_usable_area_not_bbox():
    """L-shape: bbox 16 m², usable 12 m². Two 2×2 m slabs → 8/12 = 66.7%."""
    target = TargetGeometry(
        target_id="L", name="L",
        boundary=[
            (0, 0), (4000, 0), (4000, 2000),
            (2000, 2000), (2000, 4000), (0, 4000),
        ],
    )
    slabs = [_slab("A", 2000, 2000), _slab("B", 2000, 2000)]
    view = view_from_blf_pack_result(blf_pack(slabs, target))
    assert view.target.usable_area_m2 == pytest.approx(12.0)
    assert view.coverage_percentage == pytest.approx(100.0 * 8.0 / 12.0)


def test_total_seam_length_is_aggregated_from_detected_seams():
    target = _rect_geom(4000, 3000)
    slabs = [_slab("A", 2000, 1500), _slab("B", 2000, 1500)]
    view = view_from_blf_pack_result(blf_pack(slabs, target))
    # The two slabs share a 1500 mm vertical seam at x=2000.
    assert len(view.seams) == 1
    assert view.total_seam_length_mm == pytest.approx(1500.0)


# ---------------------------------------------------------------------------
# JSON serialization
# ---------------------------------------------------------------------------


def test_to_dict_is_json_serializable_and_round_trips_shape(tmp_path: Path):
    target = _rect_geom(4000, 3000)
    slabs = [_slab("A", 1500, 1500), _slab("B", 1500, 1500)]
    view = view_from_polygon_pack_result(polygon_pack(slabs, target))
    out_path = write_placement_json(view, tmp_path / "placement.json")
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert data["target"]["target_id"] == "rect"
    assert isinstance(data["target"]["boundary"], list)
    assert isinstance(data["target"]["boundary"][0], list)
    assert isinstance(data["placements"], list)
    assert data["derived"]["placed_count"] == 2
    assert data["derived"]["coverage_percentage"] >= 0
    assert "packer" in data["metadata"]


def test_metadata_override_merges_with_default_packer_name():
    target = TargetArea(target_id="t", name="t", width_mm=4000, height_mm=3000)
    result = shelf_pack([], target)
    view = view_from_shelf_pack_result(
        result, metadata={"is_demo_default": True, "extra": "value"},
    )
    assert view.metadata["packer"] == "shelf_pack"
    assert view.metadata["is_demo_default"] is True
    assert view.metadata["extra"] == "value"
