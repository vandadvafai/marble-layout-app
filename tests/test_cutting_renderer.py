"""Tests for `placement_engine.cutting.renderer` — preview generation."""

from __future__ import annotations

import json
from pathlib import Path

from placement_engine.cut_list import build_cut_list, write_cut_list_json
from placement_engine.cutting import (
    CuttingPlan,
    build_cutting_plan,
    render_cutting_plan_preview,
)
from placement_engine.layout import (
    generate_tile_layout_from_inventory,
    write_layout_json,
)
from placement_engine.target_area import TargetGeometry


def _make_clean_slabs(
    tmp_path: Path, slab_specs: list[tuple[str, float, float]],
) -> Path:
    records = []
    for sid, w, h in slab_specs:
        records.append({
            "slab_id": sid, "serial_number": sid, "slab_number": sid,
            "item_code": "P", "image_id": None,
            "height_cm": int(h / 10), "width_cm": int(w / 10),
            "height_mm": h, "width_mm": w,
            "area_m2": w * h / 1e6, "calculated_area_m2": w * h / 1e6,
            "dimension_source": "explicit_excel",
            "image_path": None, "image_found": False,
            "image_match_method": "not_found",
            "source_excel_row": 2, "warnings": [],
        })
    payload = {
        "source_excel": "x", "image_dir": "x", "sheet_name": "Sheet1",
        "record_count": len(records),
        "warning_counts": {}, "mapped_columns": {}, "unmapped_columns": [],
        "records": records,
    }
    p = tmp_path / "clean_slabs.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def _cut_list_path(tmp_path: Path, w: float, h: float) -> Path:
    target = TargetGeometry(
        target_id="rect", name="rect",
        boundary=[(0, 0), (w, 0), (w, h), (0, h)],
    )
    layout = generate_tile_layout_from_inventory(
        target, [type("S", (), {"width_mm": 1590, "height_mm": 2200})()],
    )
    layout_path = write_layout_json(layout, tmp_path / "layout.json")
    return write_cut_list_json(
        build_cut_list(layout_path), tmp_path / "cut_list.json",
    )


def test_preview_writes_png(tmp_path: Path):
    cl = _cut_list_path(tmp_path, 1590, 2200)
    inv = _make_clean_slabs(tmp_path, [("BIG", 2000, 3000)])
    plan = build_cutting_plan(cl, inv)
    png = render_cutting_plan_preview(plan, tmp_path / "preview.png")
    assert png.exists()
    assert png.stat().st_size > 0


def test_preview_renders_with_unassigned(tmp_path: Path):
    cl = _cut_list_path(tmp_path, 1590, 2200)
    inv = _make_clean_slabs(tmp_path, [("TINY", 800, 800)])
    plan = build_cutting_plan(cl, inv)
    # All pieces unassigned, no used slabs — must still produce a PNG.
    png = render_cutting_plan_preview(plan, tmp_path / "preview.png")
    assert png.exists()


def test_preview_renders_empty_plan(tmp_path: Path):
    """A plan with no slabs and no unassigned (empty cut list) still renders."""
    plan = CuttingPlan(
        source_cut_list_path="",
        source_inventory_path="",
        target_id="x", target_name="x",
    )
    png = render_cutting_plan_preview(plan, tmp_path / "preview.png")
    assert png.exists()
