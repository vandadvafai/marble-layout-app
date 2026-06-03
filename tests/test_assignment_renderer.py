"""Smoke tests for the assignment renderer."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from placement_engine.assignment import (
    build_assignment,
    render_assignment_preview,
)
from placement_engine.cut_list import build_cut_list, write_cut_list_json
from placement_engine.layout import (
    generate_tile_layout_from_inventory,
    write_layout_json,
)
from placement_engine.target_area import load_target_geometry_from_dxf

REPO_ROOT = Path(__file__).resolve().parent.parent
EX_RECT = REPO_ROOT / "examples/cad_inputs/demo/demo_rectangle_floor.dxf"
EX_L = REPO_ROOT / "examples/cad_inputs/demo/demo_l_shape_floor.dxf"
EX_APT = REPO_ROOT / "examples/cad_inputs/demo/demo_irregular_apartment_floor.dxf"


@dataclass
class _S:
    width_mm: float
    height_mm: float


def _slabs():
    return [
        _S(1590, 1590), _S(1590, 1980), _S(1550, 2040),
        _S(1590, 2200), _S(1570, 2320),
        _S(1600, 2500), _S(1610, 2620),
    ]


def _clean_slabs_path(tmp_path: Path) -> Path:
    records = []
    for i, s in enumerate(_slabs(), start=1):
        records.append({
            "slab_id": f"S{i:02d}", "serial_number": f"S{i:02d}", "slab_number": str(i),
            "item_code": "P", "image_id": None,
            "height_cm": int(s.height_mm / 10), "width_cm": int(s.width_mm / 10),
            "height_mm": s.height_mm, "width_mm": s.width_mm,
            "area_m2": s.width_mm * s.height_mm / 1e6,
            "calculated_area_m2": s.width_mm * s.height_mm / 1e6,
            "dimension_source": "explicit_excel",
            "image_path": None, "image_found": False,
            "image_match_method": "not_found",
            "source_excel_row": 2, "warnings": [],
        })
    payload = {
        "source_excel": str(tmp_path / "fake.xlsx"),
        "image_dir": str(tmp_path / "images"),
        "sheet_name": "Sheet1", "record_count": len(records),
        "warning_counts": {}, "mapped_columns": {}, "unmapped_columns": [],
        "records": records,
    }
    p = tmp_path / "clean_slabs.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def _setup(dxf: Path, tmp_path: Path):
    """Run the layout → cut-list → assignment chain and return the artifacts."""
    geom = load_target_geometry_from_dxf(dxf)
    layout = generate_tile_layout_from_inventory(geom, _slabs())
    layout_path = write_layout_json(layout, tmp_path / "layout.json")
    cl_path = write_cut_list_json(
        build_cut_list(layout_path), tmp_path / "cut_list.json",
    )
    inv = _clean_slabs_path(tmp_path)
    asg = build_assignment(cl_path, inv)
    boundary = [tuple(pt) for pt in geom.boundary]
    holes = [[(x, y) for x, y in hole] for hole in geom.holes]
    return asg, boundary, holes


def test_renders_rectangle_assignment_preview(tmp_path: Path):
    asg, boundary, holes = _setup(EX_RECT, tmp_path)
    out = render_assignment_preview(
        asg, tmp_path / "rect.png", boundary=boundary, holes=holes,
    )
    assert out.exists() and out.stat().st_size > 1000


def test_renders_l_shape_assignment_preview(tmp_path: Path):
    asg, boundary, holes = _setup(EX_L, tmp_path)
    out = render_assignment_preview(
        asg, tmp_path / "l.png", boundary=boundary, holes=holes,
    )
    assert out.exists()


def test_renders_apartment_assignment_preview(tmp_path: Path):
    asg, boundary, holes = _setup(EX_APT, tmp_path)
    out = render_assignment_preview(
        asg, tmp_path / "apt.png", boundary=boundary, holes=holes,
    )
    assert out.exists()


def test_renderer_works_without_boundary_or_holes(tmp_path: Path):
    asg, _, _ = _setup(EX_L, tmp_path)
    out = render_assignment_preview(asg, tmp_path / "no_outline.png")
    assert out.exists()


def test_hide_slab_ids_still_renders(tmp_path: Path):
    asg, boundary, holes = _setup(EX_L, tmp_path)
    out = render_assignment_preview(
        asg, tmp_path / "no_ids.png",
        boundary=boundary, holes=holes,
        show_slab_ids=False,
    )
    assert out.exists()
