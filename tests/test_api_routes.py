"""Smoke tests for the FastAPI demo-layout endpoints.

Foundation-milestone scope only: confirm the routes are wired up,
each returns the expected shape, and unknown demo IDs 404. Detailed
layout-correctness is exercised by the layout tests, not here.

Tests use FastAPI's TestClient — no real HTTP / no port binding —
so they're fast and don't need a separate server process.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from placement_engine.api.main import app
from placement_engine.api.routes import DEMOS

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_upload_state():
    """Guarantee a clean upload session around every test in this file.

    Individual tests call ``_ensure_no_upload()`` themselves at the
    start/end of their bodies, but that's fragile: an assertion
    failure or ``pytest.skip()`` partway through a test skips any
    trailing cleanup call, leaking the uploaded project (and its
    on-disk directory) into every test that runs after it. This
    fixture makes cleanup unconditional regardless of how a test
    exits.
    """
    _ensure_no_upload()
    yield
    _ensure_no_upload()


def test_health_returns_engine_version():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "engine_version" in body and body["engine_version"]


def test_demo_index_lists_all_registered_demos():
    r = client.get("/api/demo-layouts")
    assert r.status_code == 200
    body = r.json()
    assert "demos" in body
    ids = {d["demo_id"] for d in body["demos"]}
    assert ids == set(DEMOS.keys())
    # Each entry has a label so the picker has something to render.
    for d in body["demos"]:
        assert d.get("label")


def test_demo_layout_shape_for_l_shape():
    """One end-to-end fetch confirms the engine runs and the
    serializer narrows to the editor shape. We DON'T pixel-test
    piece coordinates — that's the layout suite's job."""
    r = client.get("/api/demo-layouts/l_shape")
    assert r.status_code == 200
    body = r.json()
    assert body["demo_id"] == "l_shape"
    assert "layout" in body
    layout = body["layout"]
    # Target carries the boundary the canvas will frame on.
    assert layout["target"]["target_id"]
    assert len(layout["target"]["boundary"]) >= 3
    assert len(layout["target"]["bbox"]) == 4
    # Pieces have a polygon the canvas can draw.
    assert layout["piece_count"] == len(layout["pieces"]) > 0
    p0 = layout["pieces"][0]
    assert p0["piece_id"]
    assert len(p0["polygon"]) >= 3
    # The L-shape demo declares an architectural plan; the response
    # must surface its doorways for the overlay layer.
    assert "plan" in body
    assert len(body["plan"]["doorways"]) >= 1


def test_demo_layout_404_for_unknown_id():
    r = client.get("/api/demo-layouts/does-not-exist")
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert "unknown demo_id" in detail


def test_cors_headers_allow_local_vite_dev_server():
    """Preflight from the Vite dev server origin must succeed."""
    r = client.options(
        "/api/demo-layouts/l_shape",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    # 200 from FastAPI's CORSMiddleware when the origin is allowed.
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


@pytest.mark.parametrize("demo_id", list(DEMOS.keys()))
def test_each_demo_returns_a_layout(demo_id: str):
    """Every registered demo must produce a non-empty layout —
    catches a broken DXF or missing plan file early."""
    r = client.get(f"/api/demo-layouts/{demo_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["layout"]["piece_count"] > 0


# ---------------------------------------------------------------------------
# Real cut dimensions — regression for the Step-4 "159 × 220 cm" bug.
# Every emitted piece must carry polygon-derived bounding dims +
# actual_area_m2, and edge clips must report SMALLER cut dims than
# their nominal tile size. The factory DXF and the Step-4 properties
# panel both rely on these fields.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("demo_id", list(DEMOS.keys()))
def test_pieces_carry_real_cut_dims(demo_id: str):
    """Every piece must expose ``bounding_width_mm``,
    ``bounding_height_mm`` and ``actual_area_m2`` — these are the
    fields the editor / matcher / DXF export read for real cut
    sizes. The polygon bbox must also agree with the bounding
    fields to within a millimetre (rounding tolerance)."""
    r = client.get(f"/api/demo-layouts/{demo_id}")
    assert r.status_code == 200, r.text
    pieces = r.json()["layout"]["pieces"]
    assert pieces, "demo produced no pieces"
    for p in pieces:
        assert "bounding_width_mm" in p, p
        assert "bounding_height_mm" in p, p
        assert "actual_area_m2" in p, p
        # The bounding rect should match the polygon's bbox to mm
        # precision — the serializer reads them from the engine,
        # which derived them from the same polygon, so any mismatch
        # is a regression worth catching.
        xs = [pt[0] for pt in p["polygon"]]
        ys = [pt[1] for pt in p["polygon"]]
        poly_w = max(xs) - min(xs)
        poly_h = max(ys) - min(ys)
        assert abs(p["bounding_width_mm"] - poly_w) <= 1, (
            f"bounding_width_mm disagrees with polygon bbox for "
            f"{p['piece_id']}: {p['bounding_width_mm']} vs {poly_w}"
        )
        assert abs(p["bounding_height_mm"] - poly_h) <= 1, (
            f"bounding_height_mm disagrees with polygon bbox for "
            f"{p['piece_id']}: {p['bounding_height_mm']} vs {poly_h}"
        )
        # Sanity: for unmerged pieces, bounding must never exceed
        # nominal — the polygon is a subset of the nominal grid cell
        # (clipping or equal, never bigger). Absorbed-sliver merges
        # ARE allowed to grow past the nominal rect (the polygon
        # spans both the holder and the swallowed neighbour); skip
        # those via the ``absorbed_sliver:`` note.
        is_merged = any(
            n.startswith("absorbed_sliver:") for n in p.get("notes", [])
        )
        if not is_merged:
            assert p["bounding_width_mm"] <= p["nominal_width_mm"] + 1, p
            assert p["bounding_height_mm"] <= p["nominal_height_mm"] + 1, p


def test_edge_clip_strip_reports_smaller_than_nominal():
    """REGRESSION for the Step-4 bug where the properties panel
    showed a small strip as 159 × 220 cm (== the working slab
    tile). The L-shape demo always produces at least one edge clip
    near the L's concave corner where bounding < nominal. Failure
    here would mean an edge strip is being labelled with the full
    working-slab size — exactly the dangerous mislabel the bug
    report flagged."""
    r = client.get("/api/demo-layouts/l_shape")
    assert r.status_code == 200
    pieces = r.json()["layout"]["pieces"]
    real_strips = [
        p for p in pieces
        if p["bounding_width_mm"] < p["nominal_width_mm"] - 1
        or p["bounding_height_mm"] < p["nominal_height_mm"] - 1
    ]
    assert real_strips, (
        "L-shape demo produced no clipped strips — either the demo "
        "geometry changed or the serializer is reporting nominal dims "
        "where it should report polygon bbox dims"
    )
    # Spot-check: the smallest strip must NOT be reported at the
    # working-slab size. Picking the narrowest one as the canonical
    # "tiny strip" the bug report screenshot showed.
    smallest = min(
        real_strips,
        key=lambda p: p["bounding_width_mm"] * p["bounding_height_mm"],
    )
    assert smallest["bounding_width_mm"] < smallest["nominal_width_mm"] - 1 \
        or smallest["bounding_height_mm"] < smallest["nominal_height_mm"] - 1
    # And the actual_area_m2 must reflect the polygon, not the
    # nominal rect, so the Properties panel's area row is accurate.
    nominal_area_m2 = (
        smallest["nominal_width_mm"] * smallest["nominal_height_mm"]
    ) / 1_000_000.0
    assert smallest["actual_area_m2"] < nominal_area_m2 - 0.01, (
        f"actual_area_m2 for clipped piece {smallest['piece_id']} "
        f"({smallest['actual_area_m2']} m²) is suspiciously close to "
        f"the nominal area ({nominal_area_m2} m²)"
    )


def test_absorbed_sliver_reports_merged_dims():
    """When a sliver is absorbed by a neighbour the merged piece's
    polygon spans both. Its bounding rect must reflect the merged
    geometry, not the holder's nominal tile. Skips quietly if the
    active demo configuration produces no absorbed slivers."""
    r = client.get("/api/demo-layouts/l_shape")
    assert r.status_code == 200
    pieces = r.json()["layout"]["pieces"]
    holders = [
        p for p in pieces
        if any(n.startswith("absorbed_sliver:") for n in p.get("notes", []))
    ]
    if not holders:
        pytest.skip("no absorbed-sliver holders in this demo's layout")
    for h in holders:
        # The merged polygon's bbox is at least as wide OR tall as
        # the holder's nominal rect, because absorbing a neighbour
        # always extends in one direction.
        wider = h["bounding_width_mm"] > h["nominal_width_mm"] + 1
        taller = h["bounding_height_mm"] > h["nominal_height_mm"] + 1
        assert wider or taller, (
            f"absorbed-sliver holder {h['piece_id']} reports its "
            f"nominal tile size; expected merged geometry"
        )


# ---------------------------------------------------------------------------
# POST /api/demo-layouts/{demo_id}/validate
# ---------------------------------------------------------------------------


def _l_shape_pieces_unedited() -> list[dict]:
    """Round-trip the L-shape: fetch the seed layout and rebuild the
    edit-request shape from it. Used as the "unedited" baseline that
    the validate endpoint should report as fully valid."""
    r = client.get("/api/demo-layouts/l_shape")
    layout = r.json()["layout"]
    return [
        {
            "piece_id": p["piece_id"],
            "zone_id": p["zone_id"],
            "nominal_x_mm": p["nominal_x_mm"],
            "nominal_y_mm": p["nominal_y_mm"],
            "nominal_width_mm": p["nominal_width_mm"],
            "nominal_height_mm": p["nominal_height_mm"],
            "polygon": p["polygon"],
            "notes": p["notes"],
        }
        for p in layout["pieces"]
    ]


def test_validate_unedited_l_shape_is_valid():
    """Posting the engine's own L-shape layout back unedited must
    pass every hard rule — the rule layer and the generator agree
    on what valid looks like."""
    r = client.post(
        "/api/demo-layouts/l_shape/validate",
        json={"pieces": _l_shape_pieces_unedited()},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_valid"] is True
    assert body["hard_violation_count"] == 0
    # The rule list must include R1 and R9 by ID.
    rule_ids = {r["rule_id"] for r in body["rules"]}
    assert "R1_min_piece_size" in rule_ids
    assert "R9_full_coverage" in rule_ids


def test_validate_with_50mm_piece_triggers_r1():
    """Shrink one piece's width to 50 mm (below the 100 mm R1
    threshold) — the validator must surface an R1 violation and
    name the offending piece in ``affected_ids``."""
    pieces = _l_shape_pieces_unedited()
    # The first non-edge piece is safe to shrink.
    target = pieces[0]
    target["nominal_width_mm"] = 50.0
    target["polygon"] = [
        [target["nominal_x_mm"], target["nominal_y_mm"]],
        [target["nominal_x_mm"] + 50, target["nominal_y_mm"]],
        [target["nominal_x_mm"] + 50, target["nominal_y_mm"]
            + target["nominal_height_mm"]],
        [target["nominal_x_mm"], target["nominal_y_mm"]
            + target["nominal_height_mm"]],
        [target["nominal_x_mm"], target["nominal_y_mm"]],
    ]
    r = client.post(
        "/api/demo-layouts/l_shape/validate",
        json={"pieces": pieces},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_valid"] is False
    r1 = next(r for r in body["rules"] if r["rule_id"] == "R1_min_piece_size")
    assert r1["status"] == "violation"
    assert target["piece_id"] in r1["affected_ids"]


def test_validate_with_missing_pieces_triggers_r9():
    """Drop half the pieces — coverage drops below 99.9 % and the
    coverage hard rule fires."""
    pieces = _l_shape_pieces_unedited()
    sparse = pieces[: max(1, len(pieces) // 2)]
    r = client.post(
        "/api/demo-layouts/l_shape/validate",
        json={"pieces": sparse},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_valid"] is False
    r9 = next(r for r in body["rules"] if r["rule_id"] == "R9_full_coverage")
    assert r9["status"] == "violation"


def test_validate_unknown_demo_returns_404():
    r = client.post(
        "/api/demo-layouts/does-not-exist/validate",
        json={"pieces": []},
    )
    assert r.status_code == 404


def test_validate_response_carries_per_seam_doorway_flags():
    """The seam evaluations list must surface the crosses_doorways
    field the frontend reads to highlight R2 violations."""
    r = client.post(
        "/api/demo-layouts/l_shape/validate",
        json={"pieces": _l_shape_pieces_unedited()},
    )
    body = r.json()
    assert "seams" in body
    # Unedited layout: no seams should cross the doorway.
    for s in body["seams"]:
        assert "crosses_doorways" in s
        assert isinstance(s["crosses_doorways"], list)


# ---------------------------------------------------------------------------
# /validate — edited-plan path (plan annotation tools milestone)
# ---------------------------------------------------------------------------


def test_validate_with_custom_plan_overrides_demo_plan():
    """Sending an explicit plan in the request body must override the
    demo's file-backed plan. We verify that by sending a plan with
    NO doorways — R2 must then report not_applicable instead of pass
    (a plan with doorways) and R7 must do the same."""
    pieces = _l_shape_pieces_unedited()
    r = client.post(
        "/api/demo-layouts/l_shape/validate",
        json={
            "pieces": pieces,
            "plan": {
                "target_id": "demo_l_shape_floor",
                "spaces": [], "doorways": [], "columns": [], "guide_lines": [],
            },
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    statuses = {r["rule_id"]: r["status"] for r in body["rules"]}
    assert statuses["R2_no_seams_in_doorways"] == "not_applicable"
    assert statuses["R7_full_slabs_in_doorways"] == "not_applicable"


def test_validate_with_added_doorway_triggers_r2_when_seam_crosses_it():
    """Add a doorway in the request that intersects an existing
    layout seam — R2 must fire and flag the seam."""
    pieces = _l_shape_pieces_unedited()
    # L-shape zone-0 pieces are 1590 mm wide starting from x=0, so
    # there's a vertical seam at x=1590. Place a doorway segment
    # that crosses it (horizontal threshold at y=0 with the seam
    # endpoint strictly inside).
    r = client.post(
        "/api/demo-layouts/l_shape/validate",
        json={
            "pieces": pieces,
            "plan": {
                "target_id": "demo_l_shape_floor",
                "doorways": [{
                    "doorway_id": "new_door",
                    "segment": [[1200.0, 0.0], [2000.0, 0.0]],
                    "is_main_entrance": False,
                    "width_mm": 800.0,
                    "name": "added by editor",
                }],
            },
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    r2 = next(r for r in body["rules"] if r["rule_id"] == "R2_no_seams_in_doorways")
    assert r2["status"] == "violation"
    # At least one seam should be reported as crossing the new doorway.
    any_crossing = any(s["crosses_doorways"] for s in body["seams"])
    assert any_crossing


def test_validate_with_added_column_changes_r5_evaluation():
    """Placing a column adjacent to an existing seam should make R5
    transition from not_applicable (no columns) to pass or reward."""
    pieces = _l_shape_pieces_unedited()
    # Place a column around x=1590 so the existing vertical seam at
    # x=1590 is within the column-seam-proximity (200 mm default).
    r = client.post(
        "/api/demo-layouts/l_shape/validate",
        json={
            "pieces": pieces,
            "plan": {
                "target_id": "demo_l_shape_floor",
                "doorways": [{
                    "doorway_id": "main_entrance",
                    "segment": [[3500.0, 0.0], [4500.0, 0.0]],
                    "is_main_entrance": True,
                    "width_mm": 1000.0,
                    "name": "main entrance",
                }],
                "columns": [{
                    "column_id": "new_col",
                    "polygon": [
                        [1500.0, 1000.0], [1700.0, 1000.0],
                        [1700.0, 1200.0], [1500.0, 1200.0],
                    ],
                    "name": "added by editor",
                }],
            },
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    statuses = {r["rule_id"]: r["status"] for r in body["rules"]}
    # R5 was not_applicable on the demo (no columns); now it should
    # actively evaluate (pass or reward, depending on proximity).
    assert statuses["R5_seams_near_columns"] in ("pass", "reward")


# ---------------------------------------------------------------------------
# /match-inventory — read-only inventory preview
# ---------------------------------------------------------------------------


def _l_shape_piece_dims_for_matching() -> list[dict]:
    """Just the dimensions the matcher cares about, pulled from the
    L-shape seed layout."""
    r = client.get("/api/demo-layouts/l_shape")
    return [
        {
            "piece_id": p["piece_id"],
            "nominal_width_mm": p["nominal_width_mm"],
            "nominal_height_mm": p["nominal_height_mm"],
        }
        for p in r.json()["layout"]["pieces"]
    ]


def test_match_inventory_l_shape_pieces_report_correct_status():
    """Verify the L-shape demo's matcher response shape against the
    sample inventory. The specific ``no_match`` counts depend on the
    slab sizes the inventory ships with (which are project-dependent);
    what MUST hold on every install is the response shape + that
    every piece receives a verdict from the four valid statuses."""
    r = client.post(
        "/api/demo-layouts/l_shape/match-inventory",
        json={"pieces": _l_shape_piece_dims_for_matching()},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["demo_id"] == "l_shape"
    assert body["inventory_count"] > 0
    assert body["summary"]["total_pieces"] == len(body["pieces"])
    valid_statuses = {
        "exact_fit", "matched", "multiple_options", "no_match",
    }
    for pe in body["pieces"]:
        assert pe["status"] in valid_statuses
        if pe["status"] == "no_match":
            assert pe["candidates"] == []
        else:
            assert len(pe["candidates"]) >= 1


def test_match_inventory_small_piece_returns_candidates():
    """A small piece (500 × 500) fits inside every real-inventory
    slab — the matcher must return at least one candidate per
    piece with non-negative waste."""
    r = client.post(
        "/api/demo-layouts/l_shape/match-inventory",
        json={"pieces": [{
            "piece_id": "small",
            "nominal_width_mm": 500,
            "nominal_height_mm": 500,
        }]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    pe = body["pieces"][0]
    assert pe["status"] in ("exact_fit", "matched", "multiple_options")
    assert len(pe["candidates"]) >= 1
    for c in pe["candidates"]:
        assert c["width_mm"] >= 500 or c["height_mm"] >= 500
        assert "waste_mm2" in c
        assert "rotation_needed" in c
        assert "material_name" in c
        assert "item_code" in c
        # 0.1.49 — final-cut dimensions must equal the piece dims.
        # The matcher already verified the slab covers the piece, so
        # ``cut_w/h`` is what gets cut out of the slab to make the
        # piece. Slab dims stay original (do NOT swap on rotation).
        assert c["cut_width_mm"] == 500
        assert c["cut_height_mm"] == 500
        assert c["cut_width_mm"] <= c["width_mm"] or c["rotation_needed"]
        assert c["cut_height_mm"] <= c["height_mm"] or c["rotation_needed"]


def test_match_inventory_oversized_piece_has_no_match():
    """A 3 m × 3 m piece exceeds every slab in the test inventory
    (largest is 1610 × 2620 mm). The matcher must return
    ``no_match`` and an empty candidate list."""
    r = client.post(
        "/api/demo-layouts/l_shape/match-inventory",
        json={"pieces": [{
            "piece_id": "huge_piece",
            "nominal_width_mm": 3000,
            "nominal_height_mm": 3000,
        }]},
    )
    body = r.json()
    pe = body["pieces"][0]
    assert pe["status"] == "no_match"
    assert pe["candidates"] == []
    assert body["summary"]["no_match"] == 1


def test_match_inventory_candidates_sorted_by_waste():
    """The matcher must return slabs in least-waste order so the UI
    can show the best fit first."""
    r = client.post(
        "/api/demo-layouts/l_shape/match-inventory",
        json={"pieces": [{
            "piece_id": "p",
            "nominal_width_mm": 1500,
            "nominal_height_mm": 2000,
        }]},
    )
    pe = r.json()["pieces"][0]
    wastes = [c["waste_mm2"] for c in pe["candidates"]]
    assert wastes == sorted(wastes), \
        f"candidates not sorted by waste: {wastes}"


def test_match_inventory_rotation_disabled_drops_rotated_fits():
    """A piece that ONLY fits when rotated should drop to no_match
    with rotation disabled.

    Crafted piece: 2100 × 1500. Against the real inventory the
    largest slab is 1940 × 2160 — so a direct fit needs
    slab.w ≥ 2100 which no slab satisfies, while a rotated fit
    (slab.h ≥ 2100 and slab.w ≥ 1500) matches the 1940 × 2160 slab.
    """
    piece = {
        "piece_id": "rotation_required",
        "nominal_width_mm": 2100,
        "nominal_height_mm": 1500,
    }
    rotated_on = client.post(
        "/api/demo-layouts/l_shape/match-inventory",
        json={"pieces": [piece], "allow_rotation": True},
    ).json()["pieces"][0]
    rotated_off = client.post(
        "/api/demo-layouts/l_shape/match-inventory",
        json={"pieces": [piece], "allow_rotation": False},
    ).json()["pieces"][0]
    assert rotated_on["status"] != "no_match"
    assert any(c["rotation_needed"] for c in rotated_on["candidates"])
    assert rotated_off["status"] == "no_match"


def test_match_inventory_response_carries_summary_counts():
    """The summary block must aggregate counts per status — the UI
    uses it directly to render the panel summary chips."""
    r = client.post(
        "/api/demo-layouts/l_shape/match-inventory",
        json={"pieces": [
            {"piece_id": "fits", "nominal_width_mm": 1500, "nominal_height_mm": 2000},
            {"piece_id": "huge", "nominal_width_mm": 5000, "nominal_height_mm": 5000},
        ]},
    )
    s = r.json()["summary"]
    assert s["total_pieces"] == 2
    assert s["no_match"] == 1
    # The 1500×2000 piece should match at least one slab.
    assert s["exact_fit"] + s["matched"] + s["multiple_options"] == 1


def test_match_inventory_unknown_demo_returns_404():
    r = client.post(
        "/api/demo-layouts/does-not-exist/match-inventory",
        json={"pieces": []},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Inventory source resolution + /api/inventory/info
# ---------------------------------------------------------------------------


def test_inventory_info_returns_resolved_source():
    """The info endpoint must report which clean_slabs.json the API
    is currently using. Under conftest.py the ``AVANDAD_INVENTORY_PATH``
    env var pins the sample inventory, so the resolver lands on the
    ``env_override`` label."""
    r = client.get("/api/inventory/info")
    assert r.status_code == 200, r.text
    body = r.json()
    for key in (
        "source_label", "source_description", "source_path",
        "valid_count", "skipped_count", "total_records",
    ):
        assert key in body, f"missing field: {key}"
    assert body["source_label"] == "env_override", body
    assert body["valid_count"] >= 1
    assert body["valid_count"] + body["skipped_count"] == body["total_records"]


def test_match_inventory_response_embeds_inventory_block():
    """The matcher response carries the same inventory header the
    info endpoint returns — so a client can show source/count
    without a second round trip."""
    r = client.post(
        "/api/demo-layouts/l_shape/match-inventory",
        json={"pieces": [{
            "piece_id": "p1",
            "nominal_width_mm": 500,
            "nominal_height_mm": 500,
        }]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "inventory" in body
    inv = body["inventory"]
    assert inv["source_label"] == "env_override"
    assert inv["valid_count"] >= 1
    # Legacy top-level mirror — kept so older frontends don't break.
    assert body["inventory_count"] == inv["valid_count"]


def test_resolve_inventory_source_prefers_env_override(tmp_path, monkeypatch):
    """Setting the env var pins the inventory regardless of which
    project files exist."""
    from placement_engine.api.inventory_source import (
        SOURCE_ENV, resolve_inventory_source,
    )
    fake = tmp_path / "fake_inventory.json"
    fake.write_text('{"records": []}', encoding="utf-8")
    monkeypatch.setenv("AVANDAD_INVENTORY_PATH", str(fake))
    monkeypatch.delenv("STONELAYOUT_INVENTORY_PATH", raising=False)
    src = resolve_inventory_source(tmp_path)
    assert src.source_label == SOURCE_ENV
    assert src.path == fake


def test_resolve_inventory_source_legacy_env_var_still_works(
    tmp_path, monkeypatch,
):
    """Operators upgrading from V1.0.0 continue to see the legacy
    ``STONELAYOUT_INVENTORY_PATH`` env var work as an alias."""
    from placement_engine.api.inventory_source import (
        SOURCE_ENV, resolve_inventory_source,
    )
    fake = tmp_path / "fake_inventory.json"
    fake.write_text('{"records": []}', encoding="utf-8")
    monkeypatch.delenv("AVANDAD_INVENTORY_PATH", raising=False)
    monkeypatch.setenv("STONELAYOUT_INVENTORY_PATH", str(fake))
    src = resolve_inventory_source(tmp_path)
    assert src.source_label == SOURCE_ENV
    assert src.path == fake


def test_resolve_inventory_source_env_pointing_to_missing_raises(
    tmp_path, monkeypatch,
):
    """A broken env override must error loudly — silently falling
    back to a default would surprise the operator."""
    from placement_engine.api.inventory_source import (
        resolve_inventory_source,
    )
    monkeypatch.setenv(
        "AVANDAD_INVENTORY_PATH", str(tmp_path / "does-not-exist.json"),
    )
    monkeypatch.delenv("STONELAYOUT_INVENTORY_PATH", raising=False)
    with pytest.raises(FileNotFoundError):
        resolve_inventory_source(tmp_path)


def test_resolve_inventory_source_reports_empty_on_fresh_install(
    tmp_path, monkeypatch,
):
    """Portability regression: on a fresh clone with no env override
    and no uploaded session, the resolver MUST return the ``empty``
    label instead of falling through to a demo fixture that only
    exists on the original developer's laptop."""
    from placement_engine.api.inventory_source import (
        SOURCE_EMPTY, resolve_inventory_source,
    )
    monkeypatch.delenv("AVANDAD_INVENTORY_PATH", raising=False)
    monkeypatch.delenv("STONELAYOUT_INVENTORY_PATH", raising=False)
    src = resolve_inventory_source(tmp_path)
    assert src.source_label == SOURCE_EMPTY
    assert src.path is None
    assert src.is_empty is True


# ---------------------------------------------------------------------------
# /api/inventory/upload — Step 3 real upload
# ---------------------------------------------------------------------------


def _read_test_export() -> tuple[bytes, str]:
    """Read the project's test Excel export — same one used by the
    real-inventory ingest. Returns (bytes, filename)."""
    from placement_engine.api.routes import PROJECT_ROOT
    p = PROJECT_ROOT / "data" / "raw_test" / "export.xlsx"
    return p.read_bytes(), p.name


def _read_test_images() -> list[tuple[str, bytes]]:
    from placement_engine.api.routes import PROJECT_ROOT
    d = PROJECT_ROOT / "data" / "raw_test" / "images"
    return [(p.name, p.read_bytes()) for p in sorted(d.glob("*.jpg"))]


def _ensure_no_upload():
    """Each test runs with a clean upload session — otherwise the
    resolver picks up state from a prior test.

    Under V1.2 the upload is persistent (a project directory under
    AVANDAD_DATA_DIR). We also purge that directory so a leftover
    from a prior test doesn't rehydrate on the next
    ``get_active_upload()`` call.
    """
    import shutil
    import placement_engine.api.inventory_upload as _iu
    from placement_engine.api.app_paths import resolve_app_paths
    _iu.clear_active_upload()
    _iu._ACTIVE_UPLOAD = None
    projects_dir = resolve_app_paths().root / "projects"
    if projects_dir.exists():
        shutil.rmtree(projects_dir, ignore_errors=True)


def test_upload_returns_summary_and_activates_session():
    """Round trip: upload Excel + images, get back a summary, and
    the inventory-info endpoint should then report the upload."""
    _ensure_no_upload()
    excel_bytes, excel_name = _read_test_export()
    images = _read_test_images()
    files = [("excel", (excel_name, excel_bytes,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))]
    for fname, data in images:
        files.append(("images", (fname, data, "image/jpeg")))

    r = client.post("/api/inventory/upload", files=files)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["excel_filename"] == excel_name
    assert body["image_count"] == len(images)
    summary = body["summary"]
    # The test export ships with 7 rows + 7 photos that match.
    assert summary["total_rows"] >= 1
    assert summary["valid_slabs"] >= 1
    assert summary["linked_photos"] >= 1
    assert "preview" in summary and isinstance(summary["preview"], list)

    # The resolver now points at the uploaded session.
    info = client.get("/api/inventory/info")
    assert info.status_code == 200
    assert info.json()["source_label"] == "uploaded"
    _ensure_no_upload()


def test_upload_replaces_previous_session(tmp_path):
    """Uploading twice in a row keeps only the latest session."""
    _ensure_no_upload()
    excel_bytes, excel_name = _read_test_export()
    files = [("excel", (excel_name, excel_bytes, "application/octet-stream"))]
    r1 = client.post("/api/inventory/upload", files=files)
    assert r1.status_code == 200
    sid1 = r1.json()["session_id"]

    r2 = client.post("/api/inventory/upload", files=files)
    assert r2.status_code == 200
    sid2 = r2.json()["session_id"]

    assert sid1 != sid2  # each upload gets a fresh UUID

    current = client.get("/api/inventory/current").json()
    assert current["active"] is True
    assert current["session_id"] == sid2
    _ensure_no_upload()


def test_current_inventory_inactive_by_default():
    _ensure_no_upload()
    r = client.get("/api/inventory/current")
    assert r.status_code == 200
    assert r.json()["active"] is False


def test_delete_current_inventory_returns_to_fallback():
    """After delete, resolver should fall back to demo / real."""
    _ensure_no_upload()
    excel_bytes, excel_name = _read_test_export()
    files = [("excel", (excel_name, excel_bytes, "application/octet-stream"))]
    client.post("/api/inventory/upload", files=files)

    info_uploaded = client.get("/api/inventory/info").json()
    assert info_uploaded["source_label"] == "uploaded"

    d = client.delete("/api/inventory/current")
    assert d.status_code == 200
    assert d.json()["active"] is False

    info_fallback = client.get("/api/inventory/info").json()
    assert info_fallback["source_label"] != "uploaded"


def test_upload_then_match_uses_uploaded_inventory():
    """Step 4's matcher must transparently use the uploaded inventory
    as soon as it's active."""
    _ensure_no_upload()
    excel_bytes, excel_name = _read_test_export()
    images = _read_test_images()
    files = [("excel", (excel_name, excel_bytes, "application/octet-stream"))]
    for fname, data in images:
        files.append(("images", (fname, data, "image/jpeg")))
    client.post("/api/inventory/upload", files=files)

    r = client.post(
        "/api/demo-layouts/l_shape/match-inventory",
        json={"pieces": [{
            "piece_id": "p1",
            "nominal_width_mm": 500,
            "nominal_height_mm": 500,
        }]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["inventory"]["source_label"] == "uploaded"
    _ensure_no_upload()


def test_upload_missing_excel_returns_400():
    _ensure_no_upload()
    # Empty multipart — FastAPI rejects with 422 (required field).
    r = client.post("/api/inventory/upload", files=[])
    assert r.status_code in (400, 422)


def test_slab_image_endpoint_uses_uploaded_inventory():
    """After upload, /api/inventory/slab-image/{slab_id} should
    stream the uploaded photo (image bytes, image/* content type)."""
    _ensure_no_upload()
    excel_bytes, excel_name = _read_test_export()
    images = _read_test_images()
    files = [("excel", (excel_name, excel_bytes, "application/octet-stream"))]
    for fname, data in images:
        files.append(("images", (fname, data, "image/jpeg")))
    client.post("/api/inventory/upload", files=files)

    info = client.get("/api/inventory/info").json()
    assert info["source_label"] == "uploaded"

    # Find a slab id with an image. The pipeline guarantees the JSON
    # records carry slab_id + image_path for matched rows.
    import json as _json
    from placement_engine.api.inventory_upload import get_active_upload
    session = get_active_upload()
    assert session is not None
    payload = _json.loads(session.clean_slabs_path.read_text(encoding="utf-8"))
    candidate = next(
        (r for r in payload["records"]
         if r.get("slab_id") and r.get("image_path") and r.get("image_found")),
        None,
    )
    if candidate is None:
        pytest.skip("uploaded test inventory matched no photos — cannot test endpoint")

    img = client.get(f"/api/inventory/slab-image/{candidate['slab_id']}")
    assert img.status_code == 200, img.text
    assert img.headers["content-type"].startswith("image/")
    assert len(img.content) > 0
    _ensure_no_upload()


def _green_boundary_jpeg_bytes(size: int = 512, margin: int = 40) -> bytes:
    """In-memory synthetic photo with a bright-green boundary — the
    same shape ``tests/test_calibration.py``'s
    ``_synthetic_slab_with_green_boundary`` writes to disk, encoded
    straight to JPEG bytes for a multipart upload."""
    import cv2
    import numpy as np
    img = np.zeros((size, size, 3), dtype=np.uint8)
    img[:, :] = (60, 60, 60)
    cv2.rectangle(
        img, (margin, margin), (size - margin, size - margin),
        (0, 255, 0), thickness=4,
    )
    cv2.rectangle(
        img, (margin + 4, margin + 4),
        (size - margin - 4, size - margin - 4),
        (200, 180, 150), thickness=-1,
    )
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


def test_replace_image_recalibrates_slab():
    """A slab uploaded with no photo (missing_photo) must recover
    once the operator replaces it with a real photo through the
    manual-review modal's "Replace image" action — and the new
    photo must go through the SAME classifier every other slab does
    (auto-approves here because it's a clean green-boundary shot)."""
    import io
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["Serial", "Width (cm)", "Height (cm)"])
    ws.append(["REPLACE-1", 160, 160])
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post(
        "/api/inventory/upload",
        files={"excel": ("replace.xlsx", buf.getvalue(),
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert r.status_code == 200, r.text
    before = client.get("/api/calibration/records").json()
    before_rec = next(x for x in before["records"] if x["slab_id"] == "REPLACE-1")
    assert before_rec["calibration_status"] == "missing_photo"

    photo = _green_boundary_jpeg_bytes()
    replace = client.post(
        "/api/calibration/REPLACE-1/replace-image",
        files={"image": ("new_photo.jpg", photo, "image/jpeg")},
    )
    assert replace.status_code == 200, replace.text
    record = replace.json()["record"]
    assert record["source_type"] == "green_boundary"
    assert record["calibration_status"] == "approved"
    assert record["original_image_path"] is not None
    assert record["calibrated_image_path"] is not None

    after = client.get("/api/calibration/records").json()
    after_rec = next(x for x in after["records"] if x["slab_id"] == "REPLACE-1")
    assert after_rec["calibration_status"] == "approved"
    assert after["counts"]["approved"] == 1
    assert after["counts"]["missing_photo"] == 0
    _ensure_no_upload()


def test_replace_image_unknown_slab_404s():
    import io
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["Serial", "Width (cm)", "Height (cm)"])
    ws.append(["ANY-1", 160, 160])
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post(
        "/api/inventory/upload",
        files={"excel": ("any.xlsx", buf.getvalue(),
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert r.status_code == 200
    photo = _green_boundary_jpeg_bytes()
    replace = client.post(
        "/api/calibration/DOES-NOT-EXIST/replace-image",
        files={"image": ("new_photo.jpg", photo, "image/jpeg")},
    )
    assert replace.status_code == 404
    _ensure_no_upload()


def test_legacy_green_box_project_migrates_through_real_route(tmp_path, monkeypatch):
    """M4.5 — integration proof (not just the unit-tested pure
    function in ``test_calibration.py``) that a project directory
    shaped like a pre-calibration install gets promoted through the
    ACTUAL ``GET /api/calibration/records`` route the frontend calls,
    the first time it's read after a restart.

    Seeds ``calibrations.json`` by hand the way an old install would
    have left it — one ``RAW_PHOTO`` / ``NEEDS_REVIEW`` record — plus
    a sibling legacy ``image_metadata.json`` (the pre-M1
    ``image_intake`` pipeline's output) marking that slab's green box
    as detected. No calibration endpoint is called directly; the
    promotion must happen purely from the on-disk shape via
    rehydration, exactly as it would for a real operator restarting
    the backend after upgrading."""
    import json
    from placement_engine.calibration import (
        CalibrationRecord, CalibrationStatus, SourceType,
    )
    from placement_engine.calibration.storage import (
        new_project, save_meta, save_records,
    )

    monkeypatch.setenv("AVANDAD_DATA_DIR", str(tmp_path / "data"))
    _ensure_no_upload()

    projects_root = tmp_path / "data" / "projects"
    project = new_project(projects_root)

    legacy_record = CalibrationRecord(
        slab_id="LEGACY-1",
        source_type=SourceType.RAW_PHOTO,
        excel_width_mm=1600.0,
        excel_height_mm=1600.0,
        usable_width_mm=1560.0,
        usable_height_mm=1560.0,
        calibration_status=CalibrationStatus.NEEDS_REVIEW,
        factory_policy_version="1.0",
        original_image_path=str(project.original_images / "legacy.jpg"),
        calibrated_image_path=str(project.calibrated_images / "LEGACY-1.jpg"),
        calibration_confidence=0.4,
        warnings=["low_confidence"],
    )
    save_records(project, [legacy_record])
    save_meta(project, {
        "session_id": project.session_id,
        "uploaded_at": "2025-01-01T00:00:00+00:00",
        "excel_filename": "legacy_export.xlsx",
        "image_count": 1,
        "factory_policy_version": "1.0",
        "summary": {},
    })
    # The pre-M1 image_intake pipeline's output, sitting alongside the
    # project exactly as an old install would have left it.
    (project.root / "image_metadata.json").write_text(
        json.dumps({"images": [
            {"slab_id": "LEGACY-1", "green_box_detected": True},
        ]}),
        encoding="utf-8",
    )

    # Simulate a restart: drop the in-memory cache so the very next
    # call rehydrates from disk instead of reusing a live session.
    import placement_engine.api.inventory_upload as _iu
    _iu._ACTIVE_UPLOAD = None

    r = client.get("/api/calibration/records")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["active"] is True
    record = next(
        rec for rec in body["records"] if rec["slab_id"] == "LEGACY-1"
    )
    assert record["source_type"] == "green_boundary"
    assert record["calibration_status"] == "approved"
    assert record["approved_by"] == "legacy_migration"
    assert body["counts"]["approved"] == 1
    assert body["counts"]["needs_review"] == 0

    # The promotion must also be PERSISTED back to disk, not just
    # reflected in the in-memory response — a second restart (or a
    # second read) should see the same approved state, not re-derive
    # it from the stale needs_review record every time.
    reloaded = json.loads(
        project.calibrations_file.read_text(encoding="utf-8"),
    )
    persisted = next(
        r for r in reloaded["records"] if r["slab_id"] == "LEGACY-1"
    )
    assert persisted["source_type"] == "green_boundary"
    assert persisted["calibration_status"] == "approved"
    _ensure_no_upload()


# ---------------------------------------------------------------------------
# /export-dxf — Step 4 factory cut plan
# ---------------------------------------------------------------------------


def _two_piece_layout_pieces():
    """Two 500 × 500 mm rectangles side by side. Small enough to fit
    inside every real-inventory slab so the DXF route doesn't 400
    on assignment mismatches."""
    return [
        {
            "piece_id": "p1",
            "polygon": [[0, 0], [500, 0], [500, 500], [0, 500], [0, 0]],
            "nominal_width_mm": 500.0,
            "nominal_height_mm": 500.0,
        },
        {
            "piece_id": "p2",
            "polygon": [[500, 0], [1000, 0], [1000, 500], [500, 500], [500, 0]],
            "nominal_width_mm": 500.0,
            "nominal_height_mm": 500.0,
        },
    ]


def _two_slab_ids_for_assignment():
    """Pick two distinct slab_ids from the active inventory so the
    export tests don't depend on which inventory is resolved."""
    r = client.post(
        "/api/demo-layouts/l_shape/match-inventory",
        json={
            "pieces": [{
                "piece_id": "probe",
                "nominal_width_mm": 500,
                "nominal_height_mm": 500,
            }],
            "top_k": 10,
        },
    )
    assert r.status_code == 200
    cands = [c["slab_id"] for c in r.json()["pieces"][0]["candidates"]]
    assert len(cands) >= 2
    return cands[0], cands[1]


def test_export_dxf_rejects_incomplete_assignment():
    """The endpoint must 400 when even one piece has no assigned
    slab — exporting a partial plan would be a production
    footgun."""
    slab_a, _ = _two_slab_ids_for_assignment()
    r = client.post(
        "/api/demo-layouts/l_shape/export-dxf",
        json={
            "pieces": _two_piece_layout_pieces(),
            "assignments": {"p1": slab_a, "p2": None},
        },
    )
    assert r.status_code == 400
    assert "unassigned" in r.json()["detail"].lower()


def test_export_dxf_rejects_empty_pieces():
    r = client.post(
        "/api/demo-layouts/l_shape/export-dxf",
        json={"pieces": [], "assignments": {}},
    )
    assert r.status_code == 400


def test_export_dxf_404_on_unknown_demo():
    r = client.post(
        "/api/demo-layouts/does-not-exist/export-dxf",
        json={"pieces": [], "assignments": {}},
    )
    assert r.status_code == 404


def test_export_dxf_returns_valid_dxf_when_all_assigned():
    """Happy path: every piece assigned → endpoint returns a factory
    DXF that ezdxf can re-parse, with the expected factory layers."""
    slab_a, slab_b = _two_slab_ids_for_assignment()
    r = client.post(
        "/api/demo-layouts/l_shape/export-dxf",
        json={
            "pieces": _two_piece_layout_pieces(),
            "assignments": {"p1": slab_a, "p2": slab_b},
        },
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/dxf")
    cd = r.headers.get("content-disposition", "")
    assert cd.startswith("attachment")
    # V1.1 filename format: ``<Project>_FactoryCutPlan_Overview_YYYY-MM-DD.dxf``
    # No project_name supplied → falls back to the demo id.
    assert "l_shape_FactoryCutPlan_Overview_" in cd
    assert cd.endswith('.dxf"')
    assert len(r.content) > 100

    import io
    import ezdxf
    doc = ezdxf.read(io.StringIO(r.content.decode("utf-8")))
    layer_names = {layer.dxf.name for layer in doc.layers}
    # Factory writer layers — one per contractually promised surface.
    for required in (
        "SLAB_BOUNDARY", "SLAB_USABLE_AREA",
        "CUT_PIECES", "LABELS", "DIMENSIONS",
    ):
        assert required in layer_names, layer_names


def test_export_dxf_contains_piece_and_slab_labels():
    """Labels are part of the contract — verify piece_ids appear as
    TEXT entities, and the slab id shows up inside a SLAB_INFO
    header line (``SLAB {slab_id} · S/N ...``)."""
    slab_a, slab_b = _two_slab_ids_for_assignment()
    r = client.post(
        "/api/demo-layouts/l_shape/export-dxf",
        json={
            "pieces": _two_piece_layout_pieces(),
            "assignments": {"p1": slab_a, "p2": slab_b},
        },
    )
    assert r.status_code == 200

    import io
    import ezdxf
    doc = ezdxf.read(io.StringIO(r.content.decode("utf-8")))
    msp = doc.modelspace()
    text_strings = [e.dxf.text for e in msp.query("TEXT")]
    # Piece ids are their own labels.
    assert "p1" in text_strings
    assert "p2" in text_strings
    # Slab ids land inside the SLAB_INFO header line so a plain
    # substring check across every TEXT entity is what the caller
    # actually reads.
    all_text = " ".join(text_strings)
    assert slab_a in all_text
    assert slab_b in all_text


def test_export_dxf_places_cut_pieces_inside_slab_boundaries():
    """REGRESSION for the "factory vs. floor" upgrade: every cut
    contour must lie entirely inside the slab boundary rectangle
    that the writer emitted for it, otherwise the DXF is a floor
    reconstruction and not a factory cut plan."""
    slab_a, slab_b = _two_slab_ids_for_assignment()
    r = client.post(
        "/api/demo-layouts/l_shape/export-dxf",
        json={
            "pieces": _two_piece_layout_pieces(),
            "assignments": {"p1": slab_a, "p2": slab_b},
        },
    )
    assert r.status_code == 200

    import io
    import ezdxf
    doc = ezdxf.read(io.StringIO(r.content.decode("utf-8")))
    msp = doc.modelspace()
    slab_rects = [
        list(lw.vertices()) for lw in msp.query("LWPOLYLINE")
        if lw.dxf.layer == "SLAB_BOUNDARY"
    ]
    cut_rects = [
        list(lw.vertices()) for lw in msp.query("LWPOLYLINE")
        if lw.dxf.layer == "CUT_PIECES"
    ]
    # Two assigned pieces → two slabs → two cuts. Each cut must fit
    # inside at least one of the slab boundaries.
    assert len(slab_rects) == 2
    assert len(cut_rects) == 2
    for cut in cut_rects:
        cxs = [v[0] for v in cut]
        cys = [v[1] for v in cut]
        cx0, cx1 = min(cxs), max(cxs)
        cy0, cy1 = min(cys), max(cys)
        contained = False
        for slab in slab_rects:
            sxs = [v[0] for v in slab]
            sys = [v[1] for v in slab]
            sx0, sx1 = min(sxs), max(sxs)
            sy0, sy1 = min(sys), max(sys)
            if cx0 >= sx0 - 0.5 and cx1 <= sx1 + 0.5 \
                    and cy0 >= sy0 - 0.5 and cy1 <= sy1 + 0.5:
                contained = True
                break
        assert contained, (
            f"cut piece bbox ({cx0:.1f},{cy0:.1f})–({cx1:.1f},{cy1:.1f}) "
            "does not fit inside any SLAB_BOUNDARIES rectangle"
        )


def test_export_dxf_rejects_insufficient_margin():
    """Tight cut (piece almost as large as the slab) must be refused
    by the manufacturing-fit gate. The endpoint returns 400 with the
    per-piece verdict so the frontend can surface it."""
    slab_a, _ = _two_slab_ids_for_assignment()
    # A comically large piece: 5 m × 5 m — much larger than every
    # slab in the sample inventory, so the fit check must reject.
    huge_piece = {
        "piece_id": "huge",
        "polygon": [[0, 0], [5000, 0], [5000, 5000], [0, 5000], [0, 0]],
        "nominal_width_mm": 5000.0,
        "nominal_height_mm": 5000.0,
    }
    r = client.post(
        "/api/demo-layouts/l_shape/export-dxf",
        json={
            "pieces": [huge_piece],
            "assignments": {"huge": slab_a},
        },
    )
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail["error"] == "manufacturing_fit_failed"
    assert detail["failing"], "expected a failing entry"
    assert detail["failing"][0]["piece_id"] == "huge"


def _slab_dims_for(slab_id: str) -> tuple[float, float]:
    """Look up a slab's real (width, height) from the matcher."""
    r = client.post(
        "/api/demo-layouts/l_shape/match-inventory",
        json={"pieces": [{"piece_id": "p1", "nominal_width_mm": 500,
                          "nominal_height_mm": 500}]},
    )
    slab = next(
        c for c in r.json()["pieces"][0]["candidates"]
        if c["slab_id"] == slab_id
    )
    return slab["width_mm"], slab["height_mm"]


def _exact_edge_piece_body(slab_a: str, slab_w: float, slab_h: float,
                           policy: dict | None = None) -> dict:
    """Piece polygon that exactly matches the slab's dimensions —
    used across the exact-edge profile tests below."""
    body = {
        "pieces": [{
            "piece_id": "p1",
            "polygon": [
                [0, 0], [slab_w, 0], [slab_w, slab_h],
                [0, slab_h], [0, 0],
            ],
            "nominal_width_mm": slab_w,
            "nominal_height_mm": slab_h,
        }],
        "assignments": {"p1": slab_a},
    }
    if policy is not None:
        body["manufacturing_policy"] = policy
    return body


def test_exact_edge_default_profile_allows_export():
    """REGRESSION for the "1610 × 1610 slab, 1610 × 1610 piece"
    report — an exact-edge fit MUST NOT be blocked under the V1
    default policy (``profile=exact``, ``exact_edge_action=allow``).
    The verdict reads ``ready`` and ``factory_ready`` stays True."""
    slab_a, _ = _two_slab_ids_for_assignment()
    slab_w, slab_h = _slab_dims_for(slab_a)
    r = client.post(
        "/api/demo-layouts/l_shape/validate-factory-fit",
        json=_exact_edge_piece_body(slab_a, slab_w, slab_h),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["factory_ready"] is True
    row = body["results"][0]
    # V1 default treats a flush fit as ``ready`` (no user-visible
    # warning). The advanced-settings "warn" mode still fires the
    # ``exact_edge`` verdict — see the block/allow tests below.
    assert row["verdict"] == "ready"
    assert row["factory_ready"] is True
    # Both raw geometric margins are ~0 (the point of "exact edge").
    assert abs(row["geometric_margin_width_mm"]) < 1
    assert abs(row["geometric_margin_height_mm"]) < 1
    # And the actual export endpoint agrees — no HTTP 400.
    export = client.post(
        "/api/demo-layouts/l_shape/export-dxf",
        json=_exact_edge_piece_body(slab_a, slab_w, slab_h),
    )
    assert export.status_code == 200


def test_exact_edge_action_block_refuses_export():
    """When the designer opts into ``exact_edge_action = "block"``
    the same exact-edge fit is refused. The export endpoint returns
    400 with the ``exact_edge`` verdict — same code path the old
    zero-margin gate used, just now under an explicit setting."""
    slab_a, _ = _two_slab_ids_for_assignment()
    slab_w, slab_h = _slab_dims_for(slab_a)
    body = _exact_edge_piece_body(slab_a, slab_w, slab_h, policy={
        "blade_kerf_mm": 3.0,
        "edge_trim_mm": 5.0,
        "tolerance_mm": 2.0,
        "profile": "standard",
        "exact_edge_action": "block",
    })
    r = client.post("/api/demo-layouts/l_shape/export-dxf", json=body)
    assert r.status_code == 400
    detail = r.json()["detail"]
    verdicts = {f["verdict"] for f in detail["failing"]}
    assert "exact_edge" in verdicts, detail


def test_exact_profile_ignores_kerf_and_trim():
    """The ``exact`` profile evaluates raw geometry only — a piece
    that fits (even with a small positive margin) reports ``ready``
    regardless of the kerf / trim values the designer left over."""
    slab_a, _ = _two_slab_ids_for_assignment()
    slab_w, slab_h = _slab_dims_for(slab_a)
    r = client.post(
        "/api/demo-layouts/l_shape/validate-factory-fit",
        json={
            "pieces": [{
                "piece_id": "p1",
                "polygon": [[0, 0], [slab_w - 1, 0],
                            [slab_w - 1, slab_h - 1],
                            [0, slab_h - 1], [0, 0]],
                "nominal_width_mm": slab_w - 1,
                "nominal_height_mm": slab_h - 1,
            }],
            "assignments": {"p1": slab_a},
            "manufacturing_policy": {
                "blade_kerf_mm": 999.0,  # would fail under standard
                "edge_trim_mm": 999.0,   # would fail under strict
                "tolerance_mm": 999.0,
                "profile": "exact",
            },
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["factory_ready"] is True
    row = body["results"][0]
    assert row["verdict"] == "ready"
    # The exposed manufacturing_margin equals the geometric margin
    # under the exact profile (no allowances subtracted).
    assert abs(
        row["manufacturing_margin_width_mm"]
        - row["geometric_margin_width_mm"]
    ) < 0.01


def test_strict_profile_still_blocks_thin_clearance():
    """The strict profile keeps the old behaviour — kerf + trim +
    tolerance all apply. A piece that fits raw but only barely
    clears the strict allowances is refused, so operators who want
    the conservative check can still opt in."""
    slab_a, _ = _two_slab_ids_for_assignment()
    slab_w, slab_h = _slab_dims_for(slab_a)
    r = client.post(
        "/api/demo-layouts/l_shape/export-dxf",
        json={
            "pieces": [{
                "piece_id": "p1",
                "polygon": [[0, 0], [slab_w - 1, 0],
                            [slab_w - 1, slab_h - 1],
                            [0, slab_h - 1], [0, 0]],
                "nominal_width_mm": slab_w - 1,
                "nominal_height_mm": slab_h - 1,
            }],
            "assignments": {"p1": slab_a},
            "manufacturing_policy": {
                "blade_kerf_mm": 3.0,
                "edge_trim_mm": 5.0,
                "tolerance_mm": 2.0,
                "profile": "strict",
            },
        },
    )
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    verdicts = {f["verdict"] for f in detail["failing"]}
    # Insufficient margin (kerf + trim leaves the piece hanging over)
    # or tight — both are acceptable failure modes here.
    assert verdicts & {"insufficient_margin", "tight"}


def test_fit_response_carries_both_margins():
    """The preflight response exposes BOTH geometric and manufacturing
    margins on every row + the profile in use, so the UI can explain
    why a visually valid fit failed the manufacturing check."""
    slab_a, _ = _two_slab_ids_for_assignment()
    r = client.post(
        "/api/demo-layouts/l_shape/validate-factory-fit",
        json={
            "pieces": _two_piece_layout_pieces(),
            "assignments": {"p1": slab_a, "p2": slab_a},
            "manufacturing_policy": {"profile": "standard"},
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["policy"]["profile"] == "standard"
    # ``exact_edge_action`` retains its V1 default ("allow") when
    # the request only overrides ``profile``.
    assert body["policy"]["exact_edge_action"] == "allow"
    for row in body["results"]:
        for k in (
            "geometric_margin_width_mm",
            "geometric_margin_height_mm",
            "manufacturing_margin_width_mm",
            "manufacturing_margin_height_mm",
            "profile",
        ):
            assert k in row, row


def test_v1_default_policy_is_exact_allow():
    """V1 disables the manufacturing tolerance system by default.
    The preflight response's ``policy`` block must reflect the
    ``exact`` profile with ``exact_edge_action = "allow"`` when the
    request omits ``manufacturing_policy``."""
    slab_a, slab_b = _two_slab_ids_for_assignment()
    r = client.post(
        "/api/demo-layouts/l_shape/validate-factory-fit",
        json={
            "pieces": _two_piece_layout_pieces(),
            "assignments": {"p1": slab_a, "p2": slab_b},
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["policy"]["profile"] == "exact"
    assert body["policy"]["exact_edge_action"] == "allow"
    # Every result is either ``ready`` (positive margin) or, in the
    # 1610×1610 flush case, still ``ready`` because ``allow`` upgrades
    # the exact-edge branch. Nothing under the V1 default should
    # report ``tight`` / ``insufficient_margin`` / ``exact_edge``.
    for row in body["results"]:
        assert row["verdict"] == "ready", row
        assert row["factory_ready"] is True


def test_v1_default_still_blocks_pieces_bigger_than_slab():
    """The V1 default keeps the raw geometry check — a piece larger
    than the assigned slab is still ``does_not_fit`` regardless of
    the profile. Otherwise the operator could ship a plan the
    factory can't physically cut."""
    slab_a, _ = _two_slab_ids_for_assignment()
    r = client.post(
        "/api/demo-layouts/l_shape/validate-factory-fit",
        json={
            "pieces": [{
                "piece_id": "huge",
                "polygon": [[0, 0], [5000, 0], [5000, 5000],
                            [0, 5000], [0, 0]],
                "nominal_width_mm": 5000.0,
                "nominal_height_mm": 5000.0,
            }],
            "assignments": {"huge": slab_a},
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["factory_ready"] is False
    assert body["results"][0]["verdict"] == "does_not_fit"


def test_validate_factory_fit_reports_ready_and_tight():
    """Preflight endpoint the frontend calls to gate the button.
    Returns a per-piece verdict + a top-level factory_ready flag."""
    slab_a, slab_b = _two_slab_ids_for_assignment()
    r = client.post(
        "/api/demo-layouts/l_shape/validate-factory-fit",
        json={
            "pieces": _two_piece_layout_pieces(),
            "assignments": {"p1": slab_a, "p2": slab_b},
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert "policy" in body
    assert "results" in body
    assert body["factory_ready"] is True
    assert len(body["results"]) == 2
    for row in body["results"]:
        assert row["verdict"] == "ready"
        assert row["factory_ready"] is True
        # Margin must be positive on both axes for a ready verdict.
        assert row["margin_width_mm"] > 0
        assert row["margin_height_mm"] > 0


def test_validate_factory_fit_flags_bad_assignment():
    """Preflight surfaces the failing verdicts even when other
    pieces pass, so the frontend can highlight only the affected
    rows without blocking the caller."""
    slab_a, _ = _two_slab_ids_for_assignment()
    r = client.post(
        "/api/demo-layouts/l_shape/validate-factory-fit",
        json={
            "pieces": [
                {
                    "piece_id": "p1",
                    "polygon": [[0, 0], [500, 0], [500, 500],
                                [0, 500], [0, 0]],
                    "nominal_width_mm": 500.0,
                    "nominal_height_mm": 500.0,
                },
                {
                    "piece_id": "huge",
                    "polygon": [[0, 0], [4000, 0], [4000, 4000],
                                [0, 4000], [0, 0]],
                    "nominal_width_mm": 4000.0,
                    "nominal_height_mm": 4000.0,
                },
            ],
            "assignments": {"p1": slab_a, "huge": slab_a},
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["factory_ready"] is False
    rows = {r["piece_id"]: r for r in body["results"]}
    assert rows["p1"]["factory_ready"] is True
    assert rows["huge"]["factory_ready"] is False
    assert rows["huge"]["verdict"] in ("does_not_fit", "insufficient_margin")


def test_export_dxf_filename_uses_sanitized_project_name():
    """The Content-Disposition filename follows
    ``<Project>_FactoryCutPlan_Overview_YYYY-MM-DD.dxf`` with the
    project name sanitized so unsafe characters can't sneak into
    the download."""
    slab_a, slab_b = _two_slab_ids_for_assignment()
    r = client.post(
        "/api/demo-layouts/l_shape/export-dxf",
        json={
            "pieces": _two_piece_layout_pieces(),
            "assignments": {"p1": slab_a, "p2": slab_b},
            "project_name": "Villa Rosa 2026 / North Wing",
        },
    )
    assert r.status_code == 200
    cd = r.headers.get("content-disposition", "")
    # Slash and space collapse to underscores; the extension survives.
    assert "Villa_Rosa_2026_North_Wing_FactoryCutPlan_Overview_" in cd
    assert cd.endswith('.dxf"')
    # No suspicious characters in the delivered filename.
    for bad in ("/", "\\", " ", "..", ":", "?", "*"):
        assert bad not in cd.split('filename="', 1)[1].rstrip('"')


def test_export_factory_package_returns_zip():
    """The package endpoint returns a ZIP containing the overview
    DXF and one DXF per assigned slab, named per the V1.1 spec."""
    slab_a, slab_b = _two_slab_ids_for_assignment()
    r = client.post(
        "/api/demo-layouts/l_shape/export-factory-package",
        json={
            "pieces": _two_piece_layout_pieces(),
            "assignments": {"p1": slab_a, "p2": slab_b},
            "project_name": "Villa Rosa",
        },
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/zip")
    cd = r.headers.get("content-disposition", "")
    assert "Villa_Rosa_FactoryPackage_" in cd
    assert cd.endswith('.zip"')

    import io
    import zipfile
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    # Exactly one overview + two per-slab files.
    overview = [n for n in names if "FactoryCutPlan_Overview_" in n]
    slabs = [n for n in names if "_Slab_" in n]
    assert len(overview) == 1, names
    assert len(slabs) == 2, names
    # Each per-slab file is itself a parseable DXF with the four
    # spec'd factory layers.
    import ezdxf
    for n in slabs:
        payload = zf.read(n)
        doc = ezdxf.read(io.StringIO(payload.decode("utf-8")))
        layer_names = {layer.dxf.name for layer in doc.layers}
        for required in (
            "SLAB_BOUNDARY", "CUT_PIECES", "DIMENSIONS", "LABELS",
        ):
            assert required in layer_names, (n, layer_names)


def test_export_factory_package_refuses_bad_fit():
    """A failing fit blocks the ZIP endpoint the same way it blocks
    the single-DXF endpoint (same code path underneath)."""
    slab_a, _ = _two_slab_ids_for_assignment()
    huge = {
        "piece_id": "huge",
        "polygon": [[0, 0], [5000, 0], [5000, 5000], [0, 5000], [0, 0]],
        "nominal_width_mm": 5000.0,
        "nominal_height_mm": 5000.0,
    }
    r = client.post(
        "/api/demo-layouts/l_shape/export-factory-package",
        json={
            "pieces": [huge],
            "assignments": {"huge": slab_a},
            "project_name": "Villa Rosa",
        },
    )
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail["error"] == "manufacturing_fit_failed"


def test_export_dxf_polygon_uses_request_geometry():
    """REGRESSION for the Step-4 dimension bug — the cut polygons
    landed in the DXF must match the polygons the request body sent
    (the editor sends the polygon-derived bbox/area as ``cut`` dims
    too, so the LWPOLYLINE we get out is the authoritative cut
    geometry that the factory should follow). A 500 × 500 input
    polygon must NOT be exported as a full working-slab tile."""
    slab_a, slab_b = _two_slab_ids_for_assignment()
    pieces = _two_piece_layout_pieces()
    r = client.post(
        "/api/demo-layouts/l_shape/export-dxf",
        json={
            "pieces": pieces,
            "assignments": {"p1": slab_a, "p2": slab_b},
        },
    )
    assert r.status_code == 200

    import io
    import ezdxf
    doc = ezdxf.read(io.StringIO(r.content.decode("utf-8")))
    msp = doc.modelspace()
    cut_polygons = [
        list(lw.vertices()) for lw in msp.query("LWPOLYLINE")
        if lw.dxf.layer == "CUT_PIECES"
    ]
    # Two cut-piece polygons, one per request piece. Both should
    # have a bbox of 500 × 500 mm — confirming the DXF reflects the
    # REAL polygon, not a 1590 × 2200 working-slab tile.
    assert len(cut_polygons) == 2
    for verts in cut_polygons:
        xs = [v[0] for v in verts]
        ys = [v[1] for v in verts]
        bbox_w = max(xs) - min(xs)
        bbox_h = max(ys) - min(ys)
        assert abs(bbox_w - 500.0) < 1.0, (
            f"DXF cut piece bbox width = {bbox_w} mm, expected ~500 — "
            "the factory would cut the wrong size"
        )
        assert abs(bbox_h - 500.0) < 1.0, (
            f"DXF cut piece bbox height = {bbox_h} mm, expected ~500 — "
            "the factory would cut the wrong size"
        )


def test_match_inventory_top_k_returns_more_candidates():
    """The matcher must surface MORE than 3 candidates per piece when
    ``top_k`` is given, otherwise Step-4 auto-assignment can't fan
    unique slabs across tile-uniform layouts."""
    r = client.post(
        "/api/demo-layouts/l_shape/match-inventory",
        json={
            "pieces": [{
                "piece_id": "p1",
                "nominal_width_mm": 500,
                "nominal_height_mm": 500,
            }],
            "top_k": 50,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # The real-inventory test fixture has 8 slabs; with top_k=50 we
    # should see ALL of them as candidates for a small piece.
    candidates = body["pieces"][0]["candidates"]
    assert len(candidates) >= 4, candidates  # default would be 3
    assert len(candidates) <= 50


def test_match_inventory_top_k_clamped_to_200():
    """Defensive: huge top_k values clamp to the matcher's cap."""
    r = client.post(
        "/api/demo-layouts/l_shape/match-inventory",
        json={
            "pieces": [{
                "piece_id": "p1",
                "nominal_width_mm": 500,
                "nominal_height_mm": 500,
            }],
            "top_k": 10_000,
        },
    )
    assert r.status_code == 200
    # We won't get 10_000 candidates (inventory is small), but the
    # request must succeed without erroring out.
    assert len(r.json()["pieces"][0]["candidates"]) > 0


def test_slab_image_safe_crop_after_upload():
    """After upload, ?crop=safe-area should serve the cropped (green-
    box) image with X-Slab-Image-Crop=safe-area when detection
    succeeded, or fall back to the full image with
    X-Slab-Image-Crop=fallback otherwise. Either way HTTP 200."""
    _ensure_no_upload()
    excel_bytes, excel_name = _read_test_export()
    images = _read_test_images()
    files = [("excel", (excel_name, excel_bytes, "application/octet-stream"))]
    for fname, data in images:
        files.append(("images", (fname, data, "image/jpeg")))
    client.post("/api/inventory/upload", files=files)

    from placement_engine.api.inventory_upload import get_active_upload
    session = get_active_upload()
    assert session is not None
    # V1.2 — the calibration pipeline replaced the standalone green-box
    # metadata. Look for any slab with a linked photo; the endpoint
    # must serve SOMETHING in the safe-area branch either way.
    candidate = next(
        (r.slab_id for r in session.calibration_records if r.original_image_path),
        None,
    )
    if candidate is None:
        pytest.skip("upload didn't link any images — cannot test crop endpoint")

    r = client.get(
        f"/api/inventory/slab-image/{candidate}?crop=safe-area",
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/")
    assert r.headers.get("X-Slab-Image-Crop") in ("safe-area", "fallback")
    assert len(r.content) > 0
    _ensure_no_upload()


def test_slab_crop_info_reports_availability():
    """The crop-info endpoint must report green_box_detected status
    per slab so the frontend can show the right warning text."""
    _ensure_no_upload()
    r = client.get("/api/inventory/slab-crop-info/anything")
    assert r.status_code == 200
    assert r.json() == {"available": False, "reason": "no_active_upload"}

    excel_bytes, excel_name = _read_test_export()
    files = [("excel", (excel_name, excel_bytes, "application/octet-stream"))]
    for fname, data in _read_test_images():
        files.append(("images", (fname, data, "image/jpeg")))
    client.post("/api/inventory/upload", files=files)

    from placement_engine.api.inventory_upload import get_active_upload
    session = get_active_upload()
    assert session is not None
    if not session.calibration_records:
        pytest.skip("upload produced no calibration records")
    sid = session.calibration_records[0].slab_id
    info = client.get(f"/api/inventory/slab-crop-info/{sid}").json()
    assert "available" in info
    if info["available"]:
        # V1.2 crop-info schema replaces crop_x/y/w/h with source
        # metadata; every approved record reports its source_type.
        assert "source_type" in info
    _ensure_no_upload()


def test_slab_image_endpoint_404s_unknown_slab():
    _ensure_no_upload()
    r = client.get("/api/inventory/slab-image/does-not-exist")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Inventory stats + custom-tile regeneration (0.1.44)
# ---------------------------------------------------------------------------


def test_inventory_info_includes_dimension_stats():
    """`stats` block on /api/inventory/info exposes median / mean /
    min / max + slab count so the Step-3 panel can show "1790 × 1730
    mm · 8 valid slabs" without computing it client-side."""
    r = client.get("/api/inventory/info")
    assert r.status_code == 200
    body = r.json()
    assert body["valid_count"] >= 1
    stats = body["stats"]
    assert stats is not None
    for k in (
        "slab_count", "median_width_mm", "median_height_mm",
        "mean_width_mm", "mean_height_mm",
        "min_width_mm", "max_width_mm",
        "min_height_mm", "max_height_mm",
        "is_inconsistent",
    ):
        assert k in stats, f"missing stat: {k}"
    assert stats["slab_count"] == body["valid_count"]
    assert stats["median_width_mm"] > 0
    assert stats["median_height_mm"] > 0


def test_regenerate_layout_uses_inventory_median_by_default():
    """POST /regenerate with no body falls back to inventory-median
    sizing. tile_choice.basis should report ``inventory_median``."""
    r = client.post("/api/demo-layouts/l_shape/regenerate", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "layout" in body
    assert body["tile_choice"]["basis"] == "inventory_median"
    assert body["tile_choice"]["tile_width_mm"] > 0
    assert body["tile_choice"]["tile_height_mm"] > 0
    assert "inventory_source_label" in body


def test_regenerate_layout_with_explicit_tile_size():
    """When a custom tile size is given, the layout uses exactly that
    and the basis is reported as explicit_override."""
    r = client.post(
        "/api/demo-layouts/l_shape/regenerate",
        json={"tile_width_mm": 1200, "tile_height_mm": 800},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tile_choice"]["basis"] == "explicit_override"
    assert body["tile_choice"]["tile_width_mm"] == 1200
    assert body["tile_choice"]["tile_height_mm"] == 800
    # All emitted pieces should have nominal_width_mm/height_mm at or
    # below the explicit tile size (edges may be smaller from clipping).
    for p in body["layout"]["pieces"]:
        assert p["nominal_width_mm"] <= 1200 + 1
        assert p["nominal_height_mm"] <= 800 + 1


def test_regenerate_rejects_non_positive_tile():
    r = client.post(
        "/api/demo-layouts/l_shape/regenerate",
        json={"tile_width_mm": 0, "tile_height_mm": 800},
    )
    assert r.status_code == 400


def test_regenerate_unknown_demo_returns_404():
    r = client.post(
        "/api/demo-layouts/does-not-exist/regenerate", json={},
    )
    assert r.status_code == 404


def test_load_inventory_slabs_tracks_skipped_records(tmp_path):
    """Records missing dimensions must NOT appear in ``slabs`` but
    MUST count toward ``skipped_count``."""
    import json
    from placement_engine.api.inventory_matching import load_inventory_slabs
    p = tmp_path / "clean_slabs.json"
    p.write_text(json.dumps({"records": [
        {"slab_id": "ok", "width_mm": 1000, "height_mm": 2000},
        {"slab_id": "bad", "width_mm": None, "height_mm": 2000},
        {"slab_id": "alsobad", "width_mm": 1000},  # no height
    ]}), encoding="utf-8")
    result = load_inventory_slabs(p)
    assert result.valid_count == 1
    assert result.skipped_count == 2
    assert result.total_records == 3
    assert result.slabs[0].slab_id == "ok"


def test_validate_accepts_added_guide_line_without_error():
    """Guide lines are surfaced in the plan but not enforced by V1
    rules — the endpoint must accept them and continue evaluating."""
    pieces = _l_shape_pieces_unedited()
    r = client.post(
        "/api/demo-layouts/l_shape/validate",
        json={
            "pieces": pieces,
            "plan": {
                "target_id": "demo_l_shape_floor",
                "doorways": [{
                    "doorway_id": "main_entrance",
                    "segment": [[3500.0, 0.0], [4500.0, 0.0]],
                    "is_main_entrance": True,
                    "width_mm": 1000.0,
                    "name": "",
                }],
                "guide_lines": [{
                    "guide_line_id": "centre_axis",
                    "segment": [[4000.0, 0.0], [4000.0, 4000.0]],
                    "priority": 1,
                    "name": "centre axis",
                }],
            },
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # Layout is otherwise unchanged → still valid.
    assert body["is_valid"] is True
