"""Smoke tests for the cut-list renderer."""

from __future__ import annotations

import json
from pathlib import Path

from placement_engine.cut_list import (
    build_cut_list,
    render_cut_list_preview,
)
from placement_engine.layout import (
    generate_tile_layout_from_inventory,
    write_layout_json,
)
from placement_engine.target_area import (
    TargetGeometry,
    load_target_geometry_from_dxf,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
EX_RECT = REPO_ROOT / "examples/cad_inputs/demo/demo_rectangle_floor.dxf"
EX_L = REPO_ROOT / "examples/cad_inputs/demo/demo_l_shape_floor.dxf"
EX_APT = REPO_ROOT / "examples/cad_inputs/demo/demo_irregular_apartment_floor.dxf"


def _slabs():
    from dataclasses import dataclass

    @dataclass
    class _S:
        width_mm: float
        height_mm: float

    return [
        _S(1590, 1590), _S(1590, 1980), _S(1550, 2040),
        _S(1590, 2200), _S(1570, 2320),
        _S(1600, 2500), _S(1610, 2620),
    ]


def _layout_path(dxf: Path, tmp_path: Path) -> Path:
    geometry = load_target_geometry_from_dxf(dxf)
    layout = generate_tile_layout_from_inventory(geometry, _slabs())
    return write_layout_json(layout, tmp_path / "layout.json")


def _render(layout_path: Path, png_path: Path):
    cl = build_cut_list(layout_path)
    layout_dict = json.loads(layout_path.read_text(encoding="utf-8"))
    boundary = [tuple(pt) for pt in layout_dict["target"]["boundary"]]
    holes = [
        [tuple(pt) for pt in hole]
        for hole in layout_dict["target"].get("holes", [])
    ]
    return render_cut_list_preview(
        cl, png_path, boundary=boundary, holes=holes,
    )


def test_renders_rectangle_cut_list_without_error(tmp_path: Path):
    out = _render(_layout_path(EX_RECT, tmp_path), tmp_path / "rect.png")
    assert out.exists() and out.stat().st_size > 1000


def test_renders_l_shape_cut_list_without_error(tmp_path: Path):
    out = _render(_layout_path(EX_L, tmp_path), tmp_path / "l.png")
    assert out.exists()


def test_renders_apartment_cut_list_without_error(tmp_path: Path):
    out = _render(_layout_path(EX_APT, tmp_path), tmp_path / "apt.png")
    assert out.exists()


def test_renderer_handles_layout_without_boundary_or_holes(tmp_path: Path):
    """Boundary and holes are optional render inputs."""
    layout_path = _layout_path(EX_RECT, tmp_path)
    cl = build_cut_list(layout_path)
    out = render_cut_list_preview(cl, tmp_path / "no_outline.png")
    assert out.exists()


def test_legend_off_still_renders(tmp_path: Path):
    layout_path = _layout_path(EX_L, tmp_path)
    cl = build_cut_list(layout_path)
    out = render_cut_list_preview(
        cl, tmp_path / "no_legend.png", show_legend=False,
    )
    assert out.exists()
