"""Smoke tests for `placement_engine.preview` renderers.

These do not compare pixels — they check that each renderer accepts a
valid PlacementView and produces a non-empty PNG without raising.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from placement_engine.inventory.blf_pack import blf_pack
from placement_engine.inventory.model import InventorySlab
from placement_engine.preview import (
    render_debug,
    render_geometric,
    render_geometric_comparison,
    render_textured,
    render_textured_comparison,
    view_from_blf_pack_result,
)
from placement_engine.target_area import TargetGeometry


def _slab(slab_id: str, w: float, h: float) -> InventorySlab:
    return InventorySlab(
        slab_id=slab_id,
        serial_number=None, slab_number=None, item_code=None,
        width_mm=w, height_mm=h,
        area_m2=w * h / 1e6, calculated_area_m2=w * h / 1e6,
        image_path=None, image_available=False,
        image_placeholder_reason=None, source_excel_row=None,
    )


def _l_shape() -> TargetGeometry:
    return TargetGeometry(
        target_id="L", name="L synthetic",
        boundary=[
            (0, 0), (4000, 0), (4000, 2000),
            (2000, 2000), (2000, 4000), (0, 4000),
        ],
    )


def _rect_with_hole() -> TargetGeometry:
    return TargetGeometry(
        target_id="rh", name="rect with hole",
        boundary=[(0, 0), (6000, 0), (6000, 4000), (0, 4000)],
        holes=[[(2500, 1500), (3500, 1500), (3500, 2500), (2500, 2500)]],
    )


def _view(target: TargetGeometry, slabs: list[InventorySlab]):
    return view_from_blf_pack_result(blf_pack(slabs, target))


# ---------------------------------------------------------------------------
# Geometric
# ---------------------------------------------------------------------------


def test_geometric_renders_l_shape_without_error(tmp_path: Path):
    view = _view(_l_shape(), [_slab(f"S{i}", 1500, 1500) for i in range(3)])
    out = render_geometric(view, tmp_path / "geo.png")
    assert out.exists()
    assert out.stat().st_size > 1000  # not an empty / corrupt PNG


def test_geometric_renders_holes_and_seams(tmp_path: Path):
    view = _view(_rect_with_hole(), [_slab("A", 2000, 1000), _slab("B", 2000, 1000)])
    out = render_geometric(view, tmp_path / "geo_hole.png")
    assert out.exists()
    # The two slabs should produce at least one seam in the view.
    assert len(view.seams) >= 1


def test_geometric_respects_no_labels_flag(tmp_path: Path):
    """The flag shouldn't error and shouldn't change the file existence."""
    view = _view(_l_shape(), [_slab(f"S{i}", 1500, 1500) for i in range(3)])
    out = render_geometric(view, tmp_path / "geo_no_labels.png", show_labels=False)
    assert out.exists()


def test_geometric_with_dimensions(tmp_path: Path):
    view = _view(_l_shape(), [_slab(f"S{i}", 1500, 1500) for i in range(2)])
    out = render_geometric(view, tmp_path / "geo_dim.png", show_dimensions=True)
    assert out.exists()


# ---------------------------------------------------------------------------
# Textured
# ---------------------------------------------------------------------------


def test_textured_renders_without_photos_via_placeholder_fills(tmp_path: Path):
    view = _view(_l_shape(), [_slab(f"S{i}", 1500, 1500) for i in range(3)])
    # All slabs have image_source == "placeholder" in this synthetic setup.
    out = render_textured(view, tmp_path / "tex.png")
    assert out.exists()


def test_textured_respects_show_labels_flag(tmp_path: Path):
    view = _view(_l_shape(), [_slab(f"S{i}", 1500, 1500) for i in range(2)])
    out = render_textured(view, tmp_path / "tex_labels.png", show_labels=True)
    assert out.exists()


# ---------------------------------------------------------------------------
# Debug
# ---------------------------------------------------------------------------


def test_debug_renders_rejection_ghosts(tmp_path: Path):
    """Debug mode should draw the ghost rectangles for rejected slabs."""
    target = _rect_with_hole()
    too_big = _slab("TOO_BIG", 5500, 3500)
    view = _view(target, [too_big])
    assert view.rejected_count == 1
    out = render_debug(view, tmp_path / "dbg.png")
    assert out.exists()


# ---------------------------------------------------------------------------
# Comparison contact sheets
# ---------------------------------------------------------------------------


def test_geometric_comparison_writes_one_file_for_multiple_packers(tmp_path: Path):
    target = _l_shape()
    slabs_a = [_slab(f"A{i}", 1500, 1500) for i in range(2)]
    slabs_b = [_slab(f"B{i}", 1500, 1500) for i in range(3)]
    views = {
        "small_inventory": _view(target, slabs_a),
        "larger_inventory": _view(target, slabs_b),
    }
    out = render_geometric_comparison(views, tmp_path / "cmp_geo.png")
    assert out.exists()


def test_textured_comparison_writes_one_file_for_multiple_packers(tmp_path: Path):
    target = _l_shape()
    views = {
        "v1": _view(target, [_slab("A", 1500, 1500)]),
        "v2": _view(target, [_slab("A", 1500, 1500), _slab("B", 1500, 1500)]),
    }
    out = render_textured_comparison(views, tmp_path / "cmp_tex.png")
    assert out.exists()


def test_comparison_with_empty_dict_raises():
    with pytest.raises(ValueError):
        render_geometric_comparison({}, "/tmp/none.png")
