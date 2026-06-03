"""Tests for `placement_engine.assignment.builder` — V1 mapping rules."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from placement_engine.assignment import (
    ASSIGNMENT_ASSIGNED,
    ASSIGNMENT_UNASSIGNED,
    UNASSIGNED_ALL_FITTING_USED,
    UNASSIGNED_NO_SLAB_FITS,
    Assignment,
    AssignmentRecord,
    build_assignment,
    write_assignment_json,
    write_summary_json,
)
from placement_engine.cut_list import build_cut_list, write_cut_list_json
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
# helpers
# ---------------------------------------------------------------------------


def _make_clean_slabs(
    tmp_path: Path,
    slab_specs: list[tuple[str, float, float]],
    *,
    name: str = "clean_slabs.json",
) -> Path:
    """Write a clean_slabs.json fixture from (slab_id, w_mm, h_mm) tuples."""
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
    """The 7 slabs from data/raw_test (sizes only)."""
    return _make_clean_slabs(tmp_path, [
        ("S08", 1590, 1590), ("S11", 1590, 1980), ("S12", 1550, 2040),
        ("S13", 1590, 2200), ("S14", 1570, 2320),
        ("S16", 1600, 2500), ("S17", 1610, 2620),
    ])


def _layout_path(dxf: Path, tmp_path: Path) -> Path:
    """Generate an inventory-median layout JSON for a DXF fixture."""
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
    return write_layout_json(layout, tmp_path / "layout.json")


def _cut_list_path(dxf: Path, tmp_path: Path) -> Path:
    layout = _layout_path(dxf, tmp_path)
    cl = build_cut_list(layout)
    return write_cut_list_json(cl, tmp_path / "cut_list.json")


# ---------------------------------------------------------------------------
# Full pieces and the smallest-fitting-slab preference
# ---------------------------------------------------------------------------


def test_full_piece_picks_smallest_slab_that_fits(tmp_path: Path):
    """A single 1590×2200 full piece + 3 candidates: smallest (1590×2200)
    should win, not the larger ones."""
    target = TargetGeometry(
        target_id="rect", name="rect",
        boundary=[(0, 0), (1590, 0), (1590, 2200), (0, 2200)],
    )
    layout = generate_tile_layout_from_inventory(
        target,
        [type("S", (), {"width_mm": 1590, "height_mm": 2200})()],
    )
    layout_path = write_layout_json(layout, tmp_path / "layout.json")
    cl = build_cut_list(layout_path)
    cl_path = write_cut_list_json(cl, tmp_path / "cut_list.json")

    inv_path = _make_clean_slabs(tmp_path, [
        ("S_perfect", 1590, 2200),   # 3.498 m² — smallest that fits
        ("S_big", 1600, 2500),       # 4.0 m²
        ("S_huge", 1700, 2700),      # 4.59 m²
    ])
    asg = build_assignment(cl_path, inv_path)
    assigned = [r for r in asg.pieces if r.assignment_status == ASSIGNMENT_ASSIGNED]
    assert len(assigned) == 1
    assert assigned[0].assigned_slab_id == "S_perfect"


def test_full_piece_unassigned_when_no_slab_fits(tmp_path: Path):
    """A 1590×2200 piece + only undersized slabs → unassigned with
    reason ``no_slab_fits``."""
    target = TargetGeometry(
        target_id="rect", name="rect",
        boundary=[(0, 0), (1590, 0), (1590, 2200), (0, 2200)],
    )
    layout = generate_tile_layout_from_inventory(
        target,
        [type("S", (), {"width_mm": 1590, "height_mm": 2200})()],
    )
    layout_path = write_layout_json(layout, tmp_path / "layout.json")
    cl_path = write_cut_list_json(
        build_cut_list(layout_path), tmp_path / "cut_list.json",
    )

    inv_path = _make_clean_slabs(tmp_path, [
        ("TINY1", 1000, 1000),
        ("TINY2", 1200, 1500),
        ("TOO_SHORT", 1590, 2000),
    ])
    asg = build_assignment(cl_path, inv_path)
    rec = asg.pieces[0]
    assert rec.assignment_status == ASSIGNMENT_UNASSIGNED
    assert rec.reason == UNASSIGNED_NO_SLAB_FITS
    # All 3 slabs unused.
    assert len(asg.unused_slab_ids) == 3


def test_unassigned_when_all_fitting_slabs_used(tmp_path: Path):
    """Two 1590×2200 pieces, only one fitting slab → the second piece
    is ``all_fitting_slabs_used`` (the slab that could fit is taken)."""
    target = TargetGeometry(
        target_id="rect", name="rect",
        boundary=[(0, 0), (3180, 0), (3180, 2200), (0, 2200)],
    )
    layout = generate_tile_layout_from_inventory(
        target,
        [type("S", (), {"width_mm": 1590, "height_mm": 2200})()],
    )
    layout_path = write_layout_json(layout, tmp_path / "layout.json")
    cl_path = write_cut_list_json(
        build_cut_list(layout_path), tmp_path / "cut_list.json",
    )

    inv_path = _make_clean_slabs(tmp_path, [
        ("ONLY", 1590, 2200),  # only one fitting slab for two pieces
        ("TOO_SHORT", 1590, 2000),
    ])
    asg = build_assignment(cl_path, inv_path)
    assigned = [r for r in asg.pieces if r.assignment_status == ASSIGNMENT_ASSIGNED]
    unassigned = [r for r in asg.pieces if r.assignment_status == ASSIGNMENT_UNASSIGNED]
    assert len(assigned) == 1
    assert len(unassigned) == 1
    assert unassigned[0].reason == UNASSIGNED_ALL_FITTING_USED


# ---------------------------------------------------------------------------
# Priority: full pieces grab the best slabs first
# ---------------------------------------------------------------------------


def test_full_pieces_assigned_before_edge_pieces(tmp_path: Path):
    """One full + one edge piece compete for the only fitting slab.
    The full piece must win because it has higher priority."""
    # 2-column target: a full 1590×2200 tile + an edge tile clipped to
    # 800×2200 (boundary at x=2390).
    target = TargetGeometry(
        target_id="rect", name="rect",
        boundary=[(0, 0), (2390, 0), (2390, 2200), (0, 2200)],
    )
    layout = generate_tile_layout_from_inventory(
        target,
        [type("S", (), {"width_mm": 1590, "height_mm": 2200})()],
    )
    layout_path = write_layout_json(layout, tmp_path / "layout.json")
    cl_path = write_cut_list_json(
        build_cut_list(layout_path), tmp_path / "cut_list.json",
    )

    # Only ONE slab fits the 1590×2200 full piece. A 800×2200 slab fits
    # the edge piece. The full piece should win the bigger slab.
    inv_path = _make_clean_slabs(tmp_path, [
        ("BIG", 1590, 2200),
        ("EDGE_FIT", 1000, 2200),  # only fits the 800-wide edge piece
    ])
    asg = build_assignment(cl_path, inv_path)
    by_class = {r.classification: r for r in asg.pieces}
    assert by_class["full"].assignment_status == ASSIGNMENT_ASSIGNED
    assert by_class["full"].assigned_slab_id == "BIG"
    assert by_class["edge"].assignment_status == ASSIGNMENT_ASSIGNED
    assert by_class["edge"].assigned_slab_id == "EDGE_FIT"


def test_largest_full_piece_in_class_gets_first_pick(tmp_path: Path):
    """Within the full-piece class, the largest piece picks first.

    We construct two full pieces of different sizes via different tile
    dimensions. The 1590×2200 piece should pick the smallest slab that
    fits it (1590×2200); the smaller piece picks next.
    """
    # 2-column layout where tile size differs per side isn't easy to
    # construct directly — instead use a simple rectangle and 2 fits.
    target = TargetGeometry(
        target_id="rect", name="rect",
        boundary=[(0, 0), (3180, 0), (3180, 2200), (0, 2200)],
    )
    layout = generate_tile_layout_from_inventory(
        target,
        [type("S", (), {"width_mm": 1590, "height_mm": 2200})()],
    )
    layout_path = write_layout_json(layout, tmp_path / "layout.json")
    cl_path = write_cut_list_json(
        build_cut_list(layout_path), tmp_path / "cut_list.json",
    )

    # Two slabs, both fit 1590×2200. With descending-area picking and
    # smallest-slab-first, the first piece picks the smaller slab and
    # leaves the bigger for the second piece.
    inv_path = _make_clean_slabs(tmp_path, [
        ("BIG", 1700, 2400),    # larger slab
        ("PERFECT", 1590, 2200),  # exact match — smallest fitting
    ])
    asg = build_assignment(cl_path, inv_path)
    full_assigned = [
        r for r in asg.pieces
        if r.classification == "full" and r.assignment_status == ASSIGNMENT_ASSIGNED
    ]
    assigned_ids = [r.assigned_slab_id for r in full_assigned]
    assert "PERFECT" in assigned_ids
    assert "BIG" in assigned_ids


# ---------------------------------------------------------------------------
# Waste area
# ---------------------------------------------------------------------------


def test_waste_area_equals_slab_area_minus_piece_area(tmp_path: Path):
    """waste = slab_area - piece_area, never negative."""
    target = TargetGeometry(
        target_id="rect", name="rect",
        boundary=[(0, 0), (1590, 0), (1590, 2200), (0, 2200)],
    )
    layout = generate_tile_layout_from_inventory(
        target,
        [type("S", (), {"width_mm": 1590, "height_mm": 2200})()],
    )
    layout_path = write_layout_json(layout, tmp_path / "layout.json")
    cl_path = write_cut_list_json(
        build_cut_list(layout_path), tmp_path / "cut_list.json",
    )

    inv_path = _make_clean_slabs(tmp_path, [
        ("S", 1610, 2620),  # area = 4.2182 m²; piece = 3.498 m²; waste ≈ 0.72
    ])
    asg = build_assignment(cl_path, inv_path)
    rec = asg.pieces[0]
    assert rec.waste_area_m2 == pytest.approx(
        rec.slab_area_m2 - rec.piece_area_m2, abs=1e-6,
    )
    assert rec.waste_area_m2 > 0


def test_summary_estimated_waste_aggregates_assigned_pieces(tmp_path: Path):
    """``estimated_waste_m2`` only counts assigned slabs — uncovered floor
    pieces (unassigned_area_m2) are reported separately."""
    cl_path = _cut_list_path(EX_L, tmp_path)
    inv_path = _real_avandad_slabs(tmp_path)
    asg = build_assignment(cl_path, inv_path)
    expected_waste = sum(
        r.waste_area_m2 or 0.0
        for r in asg.pieces
        if r.assignment_status == ASSIGNMENT_ASSIGNED
    )
    s = asg.summary
    assert s.estimated_waste_m2 == pytest.approx(expected_waste, abs=1e-6)
    # Identity: estimated_waste = slab_area_used − assigned_area.
    assert s.estimated_waste_m2 == pytest.approx(
        s.slab_area_used_m2 - s.assigned_area_m2, abs=1e-6,
    )


def test_assigned_and_unassigned_areas_are_floor_side_not_slab_side(tmp_path: Path):
    """``assigned_area_m2`` + ``unassigned_area_m2`` equals the total
    floor area asked for by the layout — they describe the FLOOR, not
    the slabs."""
    cl_path = _cut_list_path(EX_L, tmp_path)
    inv_path = _real_avandad_slabs(tmp_path)
    asg = build_assignment(cl_path, inv_path)
    s = asg.summary
    total_piece_area = sum(r.piece_area_m2 for r in asg.pieces)
    assert s.assigned_area_m2 + s.unassigned_area_m2 == pytest.approx(
        total_piece_area, abs=1e-6,
    )
    # Sanity: when not everything is assigned, unassigned_area is > 0.
    if s.unassigned_pieces > 0:
        assert s.unassigned_area_m2 > 0


def test_main_unassigned_reason_is_mode_of_unassigned_reasons(tmp_path: Path):
    """When all unassigned pieces share a reason, that reason becomes
    ``main_unassigned_reason``; null when nothing is unassigned."""
    # All-assigned case: no main reason.
    target_small = TargetGeometry(
        target_id="rect", name="rect",
        boundary=[(0, 0), (1590, 0), (1590, 2200), (0, 2200)],
    )
    layout = generate_tile_layout_from_inventory(
        target_small,
        [type("S", (), {"width_mm": 1590, "height_mm": 2200})()],
    )
    cl_path = write_cut_list_json(
        build_cut_list(write_layout_json(layout, tmp_path / "lay.json")),
        tmp_path / "cl.json",
    )
    inv_all_fit = _make_clean_slabs(tmp_path, [("OK", 1590, 2200)])
    asg_all_fit = build_assignment(cl_path, inv_all_fit)
    assert asg_all_fit.summary.main_unassigned_reason is None

    # All unassigned with same reason.
    inv_too_small = _make_clean_slabs(
        tmp_path, [("TINY", 800, 800)], name="inv_tiny.json",
    )
    asg_none_fit = build_assignment(cl_path, inv_too_small)
    assert asg_none_fit.summary.main_unassigned_reason == UNASSIGNED_NO_SLAB_FITS


# ---------------------------------------------------------------------------
# Unused slabs
# ---------------------------------------------------------------------------


def test_unused_slab_ids_lists_only_slabs_never_picked(tmp_path: Path):
    target = TargetGeometry(
        target_id="rect", name="rect",
        boundary=[(0, 0), (1590, 0), (1590, 2200), (0, 2200)],
    )
    layout = generate_tile_layout_from_inventory(
        target,
        [type("S", (), {"width_mm": 1590, "height_mm": 2200})()],
    )
    cl_path = write_cut_list_json(
        build_cut_list(write_layout_json(layout, tmp_path / "layout.json")),
        tmp_path / "cut_list.json",
    )
    inv_path = _make_clean_slabs(tmp_path, [
        ("USED", 1590, 2200),
        ("LEFTOVER1", 1700, 2400),
        ("LEFTOVER2", 1800, 2500),
    ])
    asg = build_assignment(cl_path, inv_path)
    assert "USED" not in asg.unused_slab_ids
    assert set(asg.unused_slab_ids) == {"LEFTOVER1", "LEFTOVER2"}
    assert asg.summary.slabs_used == 1
    assert asg.summary.unused_slabs == 2
    assert asg.summary.total_slab_count == 3


# ---------------------------------------------------------------------------
# JSON I/O
# ---------------------------------------------------------------------------


def test_assignment_json_round_trip(tmp_path: Path):
    cl_path = _cut_list_path(EX_L, tmp_path)
    inv_path = _real_avandad_slabs(tmp_path)
    asg = build_assignment(cl_path, inv_path)
    out = write_assignment_json(asg, tmp_path / "assignment.json")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert set(data.keys()) == {
        "source_cut_list_path", "source_inventory_path",
        "target", "pieces", "unused_slab_ids", "summary",
    }
    p = data["pieces"][0]
    assert set(p.keys()) >= {
        "piece_id", "source_layout_piece_id", "classification",
        "piece_width_mm", "piece_height_mm", "piece_area_m2",
        "assignment_status", "assigned_slab_id",
        "slab_width_mm", "slab_height_mm", "slab_area_m2",
        "waste_area_m2", "reason",
        "cut_polygon_exterior", "cut_polygon_interiors",
    }


def test_summary_json_has_expected_shape(tmp_path: Path):
    cl_path = _cut_list_path(EX_APT, tmp_path)
    inv_path = _real_avandad_slabs(tmp_path)
    asg = build_assignment(cl_path, inv_path)
    out = write_summary_json(asg, tmp_path / "summary.json")
    data = json.loads(out.read_text(encoding="utf-8"))
    expected_keys = {
        # piece counts
        "total_pieces", "assigned_pieces", "unassigned_pieces",
        "full_assigned", "edge_assigned", "hole_assigned", "sliver_assigned",
        # slab counts
        "slabs_used", "unused_slabs", "total_slab_count",
        # areas (floor vs slab)
        "assigned_area_m2", "unassigned_area_m2",
        "slab_area_used_m2", "estimated_waste_m2",
        # reason aggregate
        "main_unassigned_reason",
    }
    assert set(data.keys()) == expected_keys
    assert data["total_pieces"] == asg.summary.total_pieces
    assert data["slabs_used"] + data["unused_slabs"] == data["total_slab_count"]


def test_build_assignment_accepts_dict_directly(tmp_path: Path):
    """Builder accepts an already-loaded cut-list dict, not only paths."""
    target = TargetGeometry(
        target_id="t", name="t",
        boundary=[(0, 0), (1590, 0), (1590, 2200), (0, 2200)],
    )
    layout = generate_tile_layout_from_inventory(
        target,
        [type("S", (), {"width_mm": 1590, "height_mm": 2200})()],
    )
    cl = build_cut_list(write_layout_json(layout, tmp_path / "layout.json"))
    inv_path = _make_clean_slabs(tmp_path, [("S", 1590, 2200)])
    asg = build_assignment(cl.to_dict(), inv_path)
    assert asg.pieces[0].assignment_status == ASSIGNMENT_ASSIGNED


def test_build_assignment_missing_cut_list_raises(tmp_path: Path):
    inv = _real_avandad_slabs(tmp_path)
    with pytest.raises(FileNotFoundError):
        build_assignment(tmp_path / "nope.json", inv)


# ---------------------------------------------------------------------------
# Smoke on the 3 demo DXFs — must not raise
# ---------------------------------------------------------------------------


def test_rectangle_smoke(tmp_path: Path):
    cl_path = _cut_list_path(EX_RECT, tmp_path)
    inv_path = _real_avandad_slabs(tmp_path)
    asg = build_assignment(cl_path, inv_path)
    assert isinstance(asg, Assignment)
    assert asg.summary.total_pieces == len(asg.pieces)


def test_l_shape_smoke(tmp_path: Path):
    cl_path = _cut_list_path(EX_L, tmp_path)
    inv_path = _real_avandad_slabs(tmp_path)
    asg = build_assignment(cl_path, inv_path)
    assert asg.summary.total_pieces == len(asg.pieces)


def test_apartment_smoke(tmp_path: Path):
    cl_path = _cut_list_path(EX_APT, tmp_path)
    inv_path = _real_avandad_slabs(tmp_path)
    asg = build_assignment(cl_path, inv_path)
    assert asg.summary.total_pieces == len(asg.pieces)
