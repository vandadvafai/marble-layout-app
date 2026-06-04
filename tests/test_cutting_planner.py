"""Tests for `placement_engine.cutting.planner` — V1 packing rules."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from placement_engine.cut_list import build_cut_list, write_cut_list_json
from placement_engine.cutting import (
    CUTTING_UNASSIGNED_ALL_FITTING_USED,
    CUTTING_UNASSIGNED_NO_SLAB_FITS,
    CuttingPlan,
    build_cutting_plan,
    write_cutting_plan_json,
    write_cutting_plan_summary_json,
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


# ---------------------------------------------------------------------------
# helpers (mirroring the assignment test conventions)
# ---------------------------------------------------------------------------


def _make_clean_slabs(
    tmp_path: Path,
    slab_specs: list[tuple[str, float, float]],
    *,
    name: str = "clean_slabs.json",
) -> Path:
    records = []
    for sid, w, h in slab_specs:
        records.append({
            "slab_id": sid,
            "serial_number": sid,
            "slab_number": sid,
            "item_code": "P",
            "image_id": None,
            "height_cm": int(h / 10), "width_cm": int(w / 10),
            "height_mm": h, "width_mm": w,
            "area_m2": w * h / 1e6, "calculated_area_m2": w * h / 1e6,
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
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _real_avandad_slabs(tmp_path: Path) -> Path:
    return _make_clean_slabs(tmp_path, [
        ("S08", 1590, 1590), ("S11", 1590, 1980), ("S12", 1550, 2040),
        ("S13", 1590, 2200), ("S14", 1570, 2320),
        ("S16", 1600, 2500), ("S17", 1610, 2620),
    ])


def _cut_list_from_dxf(dxf: Path, tmp_path: Path) -> Path:
    from dataclasses import dataclass

    @dataclass
    class _S:
        width_mm: float
        height_mm: float

    slabs = [
        _S(1590, 1590), _S(1590, 1980), _S(1550, 2040),
        _S(1590, 2200), _S(1570, 2320),
        _S(1600, 2500), _S(1610, 2620),
    ]
    geometry = load_target_geometry_from_dxf(dxf)
    layout = generate_tile_layout_from_inventory(geometry, slabs)
    layout_path = write_layout_json(layout, tmp_path / "layout.json")
    return write_cut_list_json(
        build_cut_list(layout_path), tmp_path / "cut_list.json",
    )


def _cut_list_from_rect(
    tmp_path: Path,
    width_mm: float,
    height_mm: float,
    tile_w: float = 1590,
    tile_h: float = 2200,
) -> Path:
    target = TargetGeometry(
        target_id="rect", name="rect",
        boundary=[(0, 0), (width_mm, 0), (width_mm, height_mm), (0, height_mm)],
    )
    layout = generate_tile_layout_from_inventory(
        target,
        [type("S", (), {"width_mm": tile_w, "height_mm": tile_h})()],
    )
    layout_path = write_layout_json(layout, tmp_path / "layout.json")
    return write_cut_list_json(
        build_cut_list(layout_path), tmp_path / "cut_list.json",
    )


# ---------------------------------------------------------------------------
# multi-piece-per-slab — the core new behaviour
# ---------------------------------------------------------------------------


def test_one_slab_can_hold_multiple_pieces(tmp_path: Path):
    """Two 1590×2200 full tiles. Only one slab is big enough to hold
    them both, and no other slab fits either piece. The planner must
    place BOTH pieces on that one slab — the new multi-piece-per-slab
    capability that the assignment layer can't express."""
    cl = _cut_list_from_rect(tmp_path, 1590, 4400)
    inv = _make_clean_slabs(tmp_path, [
        ("BIG", 1590, 4400),    # fits both stacked
        ("TINY", 800, 800),     # fits neither — sanity ballast
    ])
    plan = build_cutting_plan(cl, inv)
    by_slab: dict[str, int] = {}
    for s in plan.slabs:
        by_slab[s.slab_id] = len(s.placements)
    assert by_slab.get("BIG", 0) == 2
    assert "TINY" not in by_slab
    assert "TINY" in plan.unused_slab_ids
    assert plan.summary.assigned_cut_pieces == 2
    assert plan.summary.unassigned_cut_pieces == 0


def test_least_waste_prefers_exact_fit_over_consolidation(tmp_path: Path):
    """When a perfect-fit single-use slab exists, V1 prefers it over
    cramming the piece into a larger slab. Documents the heuristic so
    later milestones know what they're changing."""
    cl = _cut_list_from_rect(tmp_path, 1590, 2200)
    inv = _make_clean_slabs(tmp_path, [
        ("HUGE", 1590, 4400),
        ("PERFECT", 1590, 2200),
    ])
    plan = build_cutting_plan(cl, inv)
    placed_slabs = {s.slab_id for s in plan.slabs}
    assert placed_slabs == {"PERFECT"}
    assert "HUGE" in plan.unused_slab_ids


def test_pieces_assigned_increases_vs_one_slab_per_piece(tmp_path: Path):
    """With the same 7-slab inventory, the cutting plan should be able
    to assign *more* pieces than the 1:1 assignment whenever multiple
    pieces can share a slab — checked here on the L-shape demo."""
    cl = _cut_list_from_dxf(EX_L, tmp_path)
    inv = _real_avandad_slabs(tmp_path)

    from placement_engine.assignment import build_assignment
    asg = build_assignment(cl, inv)
    plan = build_cutting_plan(cl, inv)

    assert plan.summary.assigned_cut_pieces >= asg.summary.assigned_pieces


# ---------------------------------------------------------------------------
# offcut tracking + waste accounting
# ---------------------------------------------------------------------------


def test_offcuts_tracked_after_first_cut(tmp_path: Path):
    """A 1590×2200 piece on a 2000×3000 slab leaves a right strip and
    a top strip — both reported as offcuts with positive area."""
    cl = _cut_list_from_rect(tmp_path, 1590, 2200)
    inv = _make_clean_slabs(tmp_path, [("BIG", 2000, 3000)])
    plan = build_cutting_plan(cl, inv)
    assert len(plan.slabs) == 1
    slab = plan.slabs[0]
    assert len(slab.placements) == 1
    # 2 offcuts expected: right strip (410×3000) + top strip (1590×800)
    assert len(slab.offcuts) == 2
    total_offcut_area = sum(o.area_m2 for o in slab.offcuts)
    # Identity: offcut area ≈ slab area − placed piece area.
    slab_area = (2000 * 3000) / 1e6
    piece_area = (1590 * 2200) / 1e6
    assert total_offcut_area == pytest.approx(slab_area - piece_area, abs=1e-6)


def test_waste_calculation_matches_slab_minus_placed(tmp_path: Path):
    """waste_area_m2 == original_area_m2 − used_area_m2 for every slab."""
    cl = _cut_list_from_dxf(EX_L, tmp_path)
    inv = _real_avandad_slabs(tmp_path)
    plan = build_cutting_plan(cl, inv)
    for s in plan.slabs:
        assert s.waste_area_m2 == pytest.approx(
            s.original_area_m2 - s.used_area_m2, abs=1e-6,
        )
        assert s.waste_area_m2 >= 0


def test_summary_aggregates_per_slab_areas(tmp_path: Path):
    cl = _cut_list_from_dxf(EX_APT, tmp_path)
    inv = _real_avandad_slabs(tmp_path)
    plan = build_cutting_plan(cl, inv)
    s = plan.summary
    assert s.total_slab_area_m2 == pytest.approx(
        sum(x.original_area_m2 for x in plan.slabs), abs=1e-6,
    )
    assert s.used_cut_area_m2 == pytest.approx(
        sum(x.used_area_m2 for x in plan.slabs), abs=1e-6,
    )
    assert s.estimated_waste_m2 == pytest.approx(
        sum(x.waste_area_m2 for x in plan.slabs), abs=1e-6,
    )
    assert s.estimated_waste_m2 == pytest.approx(
        s.total_slab_area_m2 - s.used_cut_area_m2, abs=1e-6,
    )


# ---------------------------------------------------------------------------
# prioritisation
# ---------------------------------------------------------------------------


def test_full_pieces_placed_before_edge_or_sliver(tmp_path: Path):
    """A 2-column target gives one full (1590×2200) + one edge
    (800×2200). Only one slab fits the full piece. The full piece
    must be the one that lands on that slab."""
    cl = _cut_list_from_rect(tmp_path, 2390, 2200)
    # BIG fits the full; SMALL only fits the edge piece.
    inv = _make_clean_slabs(tmp_path, [
        ("BIG", 1590, 2200),
        ("SMALL", 850, 2200),
    ])
    plan = build_cutting_plan(cl, inv)
    placements_by_slab: dict[str, list] = {}
    for s in plan.slabs:
        placements_by_slab[s.slab_id] = s.placements
    big_class = {p.classification for p in placements_by_slab.get("BIG", [])}
    small_class = {p.classification for p in placements_by_slab.get("SMALL", [])}
    assert "full" in big_class
    assert "edge" in small_class


# ---------------------------------------------------------------------------
# no-fit handling
# ---------------------------------------------------------------------------


def test_no_fit_piece_marked_unassigned(tmp_path: Path):
    cl = _cut_list_from_rect(tmp_path, 1590, 2200)
    inv = _make_clean_slabs(tmp_path, [("TINY", 800, 800)])
    plan = build_cutting_plan(cl, inv)
    assert len(plan.unassigned) == 1
    assert plan.unassigned[0].reason == CUTTING_UNASSIGNED_NO_SLAB_FITS
    # No slab was touched.
    assert len(plan.slabs) == 0
    assert "TINY" in plan.unused_slab_ids


def test_all_fitting_slabs_used_reason(tmp_path: Path):
    """Two 1590×2200 pieces, one fitting slab → second piece's
    remaining offcut is too small, so reason is all_fitting_slabs_used."""
    cl = _cut_list_from_rect(tmp_path, 3180, 2200)
    inv = _make_clean_slabs(tmp_path, [("ONE", 1590, 2200)])
    plan = build_cutting_plan(cl, inv)
    # First piece placed; second cannot find any rectangle.
    assert plan.summary.assigned_cut_pieces == 1
    assert plan.summary.unassigned_cut_pieces == 1
    assert plan.unassigned[0].reason == CUTTING_UNASSIGNED_ALL_FITTING_USED


# ---------------------------------------------------------------------------
# JSON round-trip + smoke
# ---------------------------------------------------------------------------


def test_json_round_trip(tmp_path: Path):
    cl = _cut_list_from_dxf(EX_L, tmp_path)
    inv = _real_avandad_slabs(tmp_path)
    plan = build_cutting_plan(cl, inv)
    out = write_cutting_plan_json(plan, tmp_path / "cutting_plan.json")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert set(data.keys()) == {
        "source_cut_list_path", "source_inventory_path",
        "target", "slabs", "unassigned", "unused_slab_ids", "summary",
    }
    if data["slabs"]:
        s0 = data["slabs"][0]
        assert set(s0.keys()) >= {
            "slab_id", "original_width_mm", "original_height_mm",
            "original_area_m2", "used_area_m2", "waste_area_m2",
            "placements", "offcuts",
        }
        if s0["placements"]:
            p0 = s0["placements"][0]
            assert set(p0.keys()) >= {
                "cut_piece_id", "source_layout_piece_id", "slab_id",
                "x_mm", "y_mm", "width_mm", "height_mm",
                "area_m2", "classification",
            }


def test_summary_json_round_trip(tmp_path: Path):
    cl = _cut_list_from_dxf(EX_RECT, tmp_path)
    inv = _real_avandad_slabs(tmp_path)
    plan = build_cutting_plan(cl, inv)
    out = write_cutting_plan_summary_json(plan, tmp_path / "summary.json")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert set(data.keys()) == {
        "total_cut_pieces", "assigned_cut_pieces", "unassigned_cut_pieces",
        "total_slab_area_m2", "used_cut_area_m2", "estimated_waste_m2",
        "slabs_used", "unused_slabs",
    }


# ---------------------------------------------------------------------------
# demo smoke — every demo DXF runs end-to-end
# ---------------------------------------------------------------------------


def test_rectangle_smoke(tmp_path: Path):
    cl = _cut_list_from_dxf(EX_RECT, tmp_path)
    inv = _real_avandad_slabs(tmp_path)
    plan = build_cutting_plan(cl, inv)
    assert isinstance(plan, CuttingPlan)
    # Every piece is accounted for (assigned or unassigned).
    cl_total = len(json.loads(cl.read_text(encoding="utf-8"))["pieces"])
    assert plan.summary.total_cut_pieces == cl_total


def test_l_shape_smoke(tmp_path: Path):
    cl = _cut_list_from_dxf(EX_L, tmp_path)
    inv = _real_avandad_slabs(tmp_path)
    plan = build_cutting_plan(cl, inv)
    cl_total = len(json.loads(cl.read_text(encoding="utf-8"))["pieces"])
    assert plan.summary.total_cut_pieces == cl_total


def test_apartment_smoke(tmp_path: Path):
    cl = _cut_list_from_dxf(EX_APT, tmp_path)
    inv = _real_avandad_slabs(tmp_path)
    plan = build_cutting_plan(cl, inv)
    cl_total = len(json.loads(cl.read_text(encoding="utf-8"))["pieces"])
    assert plan.summary.total_cut_pieces == cl_total


def test_build_cutting_plan_accepts_dict_directly(tmp_path: Path):
    cl_path = _cut_list_from_rect(tmp_path, 1590, 2200)
    cl_dict = json.loads(cl_path.read_text(encoding="utf-8"))
    inv = _make_clean_slabs(tmp_path, [("S", 1590, 2200)])
    plan = build_cutting_plan(cl_dict, inv)
    assert plan.summary.assigned_cut_pieces == 1


def test_build_cutting_plan_missing_cut_list_raises(tmp_path: Path):
    inv = _real_avandad_slabs(tmp_path)
    with pytest.raises(FileNotFoundError):
        build_cutting_plan(tmp_path / "nope.json", inv)


# ---------------------------------------------------------------------------
# unused slabs
# ---------------------------------------------------------------------------


def test_unused_slabs_listed_when_not_touched(tmp_path: Path):
    cl = _cut_list_from_rect(tmp_path, 1590, 2200)
    inv = _make_clean_slabs(tmp_path, [
        ("PICK", 1590, 2200),
        ("LEFT1", 1700, 2400),
        ("LEFT2", 1800, 2500),
    ])
    plan = build_cutting_plan(cl, inv)
    used = {s.slab_id for s in plan.slabs}
    assert "PICK" in used
    assert set(plan.unused_slab_ids) == {"LEFT1", "LEFT2"}
    assert plan.summary.slabs_used == 1
    assert plan.summary.unused_slabs == 2
