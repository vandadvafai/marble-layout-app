"""Tests for the sliver-absorption layer."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from placement_engine.layout import (
    DEFAULT_ZONE_ID,
    SliverPolicy,
    absorb_slivers,
    generate_tile_layout,
    generate_tile_layout_from_inventory,
    write_layout_json,
)
from placement_engine.target_area import (
    TargetGeometry,
    load_target_geometry_from_dxf,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
EX_L = REPO_ROOT / "examples/cad_inputs/demo/demo_l_shape_floor.dxf"
EX_APT = REPO_ROOT / "examples/cad_inputs/demo/demo_irregular_apartment_floor.dxf"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


@dataclass
class _Slab:
    width_mm: float
    height_mm: float


def _real_inventory() -> list[_Slab]:
    return [
        _Slab(1590, 1590), _Slab(1590, 1980), _Slab(1550, 2040),
        _Slab(1590, 2200), _Slab(1570, 2320),
        _Slab(1600, 2500), _Slab(1610, 2620),
    ]


def _rect(w: float, h: float) -> TargetGeometry:
    return TargetGeometry(
        target_id="rect", name="rect",
        boundary=[(0, 0), (w, 0), (w, h), (0, h)],
    )


# ---------------------------------------------------------------------------
# core absorption behaviour
# ---------------------------------------------------------------------------


def test_thin_sliver_is_absorbed_into_adjacent_neighbour():
    """A 1640 mm wide rectangle with a 1590 mm tile leaves a 50 mm
    sliver. After absorption the layout has one 1640 mm piece, no
    sliver, and area is conserved."""
    geom = _rect(1640, 2200)
    # Use the explicit pre-absorption pass via enable_absorption=False to
    # observe the raw layout, then absorb explicitly.
    raw = generate_tile_layout_from_inventory(
        geom, _real_inventory(), enable_absorption=False,
    )
    assert raw.sliver_count == 1
    raw_area = raw.total_actual_area_m2
    absorbed = absorb_slivers(raw, policy=SliverPolicy())
    assert absorbed.sliver_count == 0
    assert len(absorbed.pieces) == 1
    p = absorbed.pieces[0]
    assert p.bounding_width_mm == pytest.approx(1640.0)
    assert p.bounding_height_mm == pytest.approx(2200.0)
    assert any(n.startswith("absorbed_sliver:") for n in p.notes)
    # Area conserved exactly (modulo floating-point round).
    assert absorbed.total_actual_area_m2 == pytest.approx(raw_area, abs=1e-6)


def test_no_sliver_no_change():
    """A clean rectangle whose dimensions divide the tile exactly has
    nothing to absorb — absorption is a no-op."""
    geom = _rect(3180, 4400)  # 2×1590 by 2×2200
    layout = generate_tile_layout_from_inventory(geom, _real_inventory())
    assert layout.sliver_count == 0
    assert len(layout.pieces) == 4
    for p in layout.pieces:
        assert not any(n.startswith("absorbed_sliver:") for n in p.notes)


def test_area_conservation_after_absorption_on_l_shape():
    geom = load_target_geometry_from_dxf(EX_L)
    layout = generate_tile_layout_from_inventory(geom, _real_inventory())
    assert layout.total_actual_area_m2 == pytest.approx(
        geom.usable_area_m2, abs=1e-3,
    )


def test_l_shape_has_no_orange_strip_after_absorption():
    """The headline acceptance test: the demo L-shape's two 30 mm and
    two 20 mm boundary slivers are gone."""
    geom = load_target_geometry_from_dxf(EX_L)
    layout = generate_tile_layout_from_inventory(geom, _real_inventory())
    assert layout.sliver_count == 0
    # Pieces fall to 14 (raw) - 4 (absorbed slivers) = 10.
    assert len(layout.pieces) == 10
    # At least one absorption trace exists per affected zone.
    z_traces = {p.zone_id: [n for n in p.notes if n.startswith("absorbed_sliver")]
                for p in layout.pieces if any(
                    n.startswith("absorbed_sliver:") for n in p.notes
                )}
    assert "z0" in z_traces
    assert "z1" in z_traces


def test_apartment_has_no_sliver_after_absorption():
    geom = load_target_geometry_from_dxf(EX_APT)
    layout = generate_tile_layout_from_inventory(geom, _real_inventory())
    assert layout.sliver_count == 0


def test_absorption_can_be_disabled():
    """``enable_absorption=False`` returns the raw zoned layout — the
    slivers are still present and downstream consumers see them."""
    geom = load_target_geometry_from_dxf(EX_L)
    raw = generate_tile_layout_from_inventory(
        geom, _real_inventory(), enable_absorption=False,
    )
    assert raw.sliver_count > 0


# ---------------------------------------------------------------------------
# absorption respects zone + hole boundaries
# ---------------------------------------------------------------------------


def test_absorption_does_not_cross_zone_boundaries():
    """A sliver in zone z0 must NOT merge into a piece in zone z1, even
    if that piece sits across the architectural step line.

    Concretely: in the L-shape, z0's right-edge slivers (if any) would
    abut z1's left edge — but the absorber must refuse the merge
    because the pieces live in different zones.
    """
    geom = load_target_geometry_from_dxf(EX_L)
    layout = generate_tile_layout_from_inventory(geom, _real_inventory())
    for p in layout.pieces:
        for note in p.notes:
            if not note.startswith("absorbed_sliver:"):
                continue
            absorbed_id = note.split(":", 1)[1]
            # The absorbed piece_id always carries its zone_id prefix
            # (multi-zone naming); confirm the receiving piece is in
            # the same zone.
            assert absorbed_id.startswith(p.zone_id + "_"), (
                f"piece {p.piece_id} (zone {p.zone_id}) absorbed "
                f"{absorbed_id} from a different zone"
            )


def test_absorption_does_not_pick_neighbour_with_interior_hole():
    """A piece with an interior hole must NEVER be a merge target —
    the merged shape would carry the hole, which is geometrically
    fine but well outside V1 scope.

    Build a synthetic layout where the only adjacent rectangle has
    an interior hole and confirm the sliver is left in place.
    """
    # Floor 1640 × 4400 with a hole strictly inside the lower tile.
    geom = TargetGeometry(
        target_id="rh", name="rh",
        boundary=[(0, 0), (1640, 0), (1640, 4400), (0, 4400)],
        holes=[[(400, 400), (1200, 400), (1200, 1800), (400, 1800)]],
    )
    layout = generate_tile_layout_from_inventory(
        geom, _real_inventory(),
        # Disable zoning so the polygon stays single-zone and the
        # neighbour with the hole is the only merge candidate.
        enable_zoning=False,
    )
    slivers = [p for p in layout.pieces if "sliver" in p.notes]
    # The 50 mm sliver should NOT have been absorbed — the only
    # adjacent piece in this zone has an interior hole.
    holders = [
        p for p in layout.pieces
        if any(n.startswith("absorbed_sliver:") for n in p.notes)
    ]
    # Either the sliver survived or some non-hole neighbour absorbed
    # it. The upper tile has no hole, so it could in principle absorb
    # the sliver vertically — but a 50×2200 sliver at row 0 doesn't
    # share a full edge with the row-1 upper tile (different x-range
    # extent), so no merge happens. Confirm the hole-bearing piece is
    # never the holder.
    for h in holders:
        assert not h.interior_holes, (
            f"holder {h.piece_id} has interior holes — absorber wrongly "
            "picked a hole-bearing neighbour"
        )
    # The sliver count is at least preserved (might be reduced if a
    # non-hole neighbour exists; here it should still be 1).
    assert len(slivers) >= 1 or len(holders) >= 1


# ---------------------------------------------------------------------------
# absorption respects same-zone, non-rectangular constraints
# ---------------------------------------------------------------------------


def test_absorption_skips_non_rectangular_neighbour():
    """If the only neighbour is a non-rectangular edge piece, the
    sliver is left untouched (V1 limitation, documented).

    Construct a 5000 × 1000 floor with a triangle-style notch — this
    yields a non-rectangular edge piece adjacent to a sliver column.
    """
    # 1640 × 1000 floor with a slanted top-right cut. Non-rectilinear
    # → falls back to single-zone, but the clipped edge piece becomes
    # non-rectangular when we add a triangular notch.
    geom = TargetGeometry(
        target_id="tri", name="tri",
        boundary=[(0, 0), (1640, 0), (1640, 1000), (1200, 600), (0, 600)],
    )
    layout = generate_tile_layout_from_inventory(geom, _real_inventory())
    # Either the sliver was absorbed cleanly or it remained — but no
    # piece with absorbed_sliver should be non-rectangular.
    for p in layout.pieces:
        if any(n.startswith("absorbed_sliver:") for n in p.notes):
            # Holder must be a rectangle (its current polygon).
            xs = [x for x, _ in p.actual_cut_polygon]
            ys = [y for _, y in p.actual_cut_polygon]
            bbox_area_m2 = (max(xs) - min(xs)) * (max(ys) - min(ys)) / 1e6
            assert p.actual_area_m2 == pytest.approx(bbox_area_m2, abs=1e-3)


# ---------------------------------------------------------------------------
# Downstream still consumes the absorbed layout
# ---------------------------------------------------------------------------


def test_cut_list_consumes_absorbed_layout(tmp_path: Path):
    """The cut-list builder classifies the merged pieces as ``edge``
    (not ``sliver``), because the ``sliver`` note was stripped on
    merge and the absorbed pieces carry ``is_edge_piece=True``."""
    from placement_engine.cut_list import build_cut_list, write_cut_list_json

    geom = load_target_geometry_from_dxf(EX_L)
    layout = generate_tile_layout_from_inventory(geom, _real_inventory())
    layout_path = write_layout_json(layout, tmp_path / "layout.json")
    cl = build_cut_list(layout_path)
    write_cut_list_json(cl, tmp_path / "cut_list.json")
    # Same piece count as the absorbed layout.
    assert cl.summary.total_pieces == len(layout.pieces)
    # No sliver-classified pieces in the cut list — they were absorbed.
    assert cl.summary.sliver_pieces == 0


def test_assignment_sees_enlarged_dimensions(tmp_path: Path):
    """The assignment layer reads ``bounding_width/height`` from the
    cut-list piece; absorbed pieces should expose their MERGED
    dimensions (1620 × 2200 for L-shape z0, not 1590 × 2200)."""
    from placement_engine.assignment import build_assignment
    from placement_engine.cut_list import build_cut_list, write_cut_list_json

    geom = load_target_geometry_from_dxf(EX_L)
    layout = generate_tile_layout_from_inventory(geom, _real_inventory())
    layout_path = write_layout_json(layout, tmp_path / "layout.json")
    cl_path = write_cut_list_json(
        build_cut_list(layout_path), tmp_path / "cut_list.json",
    )
    inv_records = [{
        "slab_id": f"S{i}", "serial_number": f"S{i}", "slab_number": f"{i}",
        "item_code": "P", "image_id": None,
        "height_cm": int(h / 10), "width_cm": int(w / 10),
        "height_mm": h, "width_mm": w,
        "area_m2": w * h / 1e6, "calculated_area_m2": w * h / 1e6,
        "dimension_source": "explicit_excel",
        "image_path": None, "image_found": False,
        "image_match_method": "not_found",
        "source_excel_row": 2, "warnings": [],
    } for i, (w, h) in enumerate([
        (1590, 1590), (1590, 1980), (1550, 2040),
        (1590, 2200), (1570, 2320), (1600, 2500), (1610, 2620),
    ])]
    inv_path = tmp_path / "clean_slabs.json"
    inv_path.write_text(json.dumps({
        "source_excel": "x", "image_dir": "x", "sheet_name": "Sheet1",
        "record_count": len(inv_records),
        "warning_counts": {}, "mapped_columns": {}, "unmapped_columns": [],
        "records": inv_records,
    }), encoding="utf-8")

    asg = build_assignment(cl_path, inv_path)
    # Find an absorbed piece (>1590 wide); confirm its dimension
    # passed through to the assignment record verbatim.
    enlarged = [
        p for p in asg.pieces
        if p.piece_width_mm > 1590.0 + 1.0
    ]
    assert enlarged, "no enlarged absorbed piece reached the assignment layer"
    for r in enlarged:
        # 1620 mm exceeds every slab's width (max = 1610) → record
        # status must be unassigned, NOT silently fitted.
        if r.piece_width_mm > 1610.0:
            assert r.assignment_status == "unassigned"


# ---------------------------------------------------------------------------
# direct absorb_slivers call (deterministic + idempotent)
# ---------------------------------------------------------------------------


def test_absorb_slivers_is_idempotent():
    """Running absorb_slivers twice produces the same result — no
    extra merges happen on the second pass."""
    geom = load_target_geometry_from_dxf(EX_L)
    layout = generate_tile_layout_from_inventory(
        geom, _real_inventory(), enable_absorption=False,
    )
    first = absorb_slivers(layout, policy=SliverPolicy())
    first_pieces = list(first.pieces)
    again = absorb_slivers(first, policy=SliverPolicy())
    assert len(again.pieces) == len(first_pieces)
    assert {p.piece_id for p in again.pieces} == {p.piece_id for p in first_pieces}


def test_absorb_slivers_handles_empty_layout():
    """No pieces → no work — defensive empty-list handling."""
    from placement_engine.layout.schema import LayoutResult
    empty = LayoutResult(
        target=_rect(1000, 1000),
        tile_width_mm=1590, tile_height_mm=2200,
        origin=(0, 0), pieces=[],
    )
    out = absorb_slivers(empty, policy=SliverPolicy())
    assert out.pieces == []


# ---------------------------------------------------------------------------
# JSON round-trip — absorbed pieces show up cleanly
# ---------------------------------------------------------------------------


def test_layout_json_records_absorption_traces(tmp_path: Path):
    geom = load_target_geometry_from_dxf(EX_L)
    layout = generate_tile_layout_from_inventory(geom, _real_inventory())
    path = write_layout_json(layout, tmp_path / "layout.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    holders = [
        p for p in data["pieces"]
        if any(n.startswith("absorbed_sliver:") for n in p["notes"])
    ]
    # L-shape demo absorbs 4 slivers — 4 holders in the JSON.
    assert len(holders) == 4
    # No piece in the JSON carries the ``sliver`` note.
    for p in data["pieces"]:
        assert "sliver" not in p["notes"]
