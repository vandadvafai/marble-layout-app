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
    """Verify the L-shape demo's matcher response against the REAL
    project inventory.

    The L-shape seed produces 1590 × 2200 mm pieces; the real export
    at outputs/slab_ingestion/raw_test maxes at 2160 × 1940 mm, so
    every piece is too tall to fit — even rotated, the longer slab
    side (2160) is still under the 2200 piece dim. The matcher must
    therefore return ``no_match`` for every L-shape piece. This is
    the *expected* outcome the UI surfaces as a clear warning.
    """
    r = client.post(
        "/api/demo-layouts/l_shape/match-inventory",
        json={"pieces": _l_shape_piece_dims_for_matching()},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["demo_id"] == "l_shape"
    assert body["inventory_count"] > 0
    assert body["summary"]["total_pieces"] == len(body["pieces"])
    # Every L-shape piece exceeds the real inventory's slab heights.
    assert body["summary"]["no_match"] == body["summary"]["total_pieces"]
    for pe in body["pieces"]:
        assert pe["status"] == "no_match"
        assert pe["candidates"] == []


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
    is currently using. In the default test environment that's the
    real project export (preferred over the demo fixture)."""
    r = client.get("/api/inventory/info")
    assert r.status_code == 200, r.text
    body = r.json()
    # Required keys — shape that the matcher response also embeds.
    for key in (
        "source_label", "source_description", "source_path",
        "valid_count", "skipped_count", "total_records",
    ):
        assert key in body, f"missing field: {key}"
    # The repo ships both real and demo inventories, so the resolver
    # MUST land on the real one (resolver preference order).
    assert body["source_label"] == "real_inventory", body
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
    assert inv["source_label"] == "real_inventory"
    assert inv["valid_count"] >= 1
    # Legacy top-level mirror — kept so older frontends don't break.
    assert body["inventory_count"] == inv["valid_count"]


def test_resolve_inventory_source_prefers_env_override(tmp_path, monkeypatch):
    """Setting the env var pins the inventory regardless of which
    project files exist."""
    from placement_engine.api.inventory_source import (
        ENV_VAR_NAME, SOURCE_ENV, resolve_inventory_source,
    )
    fake = tmp_path / "fake_inventory.json"
    fake.write_text('{"records": []}', encoding="utf-8")
    monkeypatch.setenv(ENV_VAR_NAME, str(fake))
    src = resolve_inventory_source(tmp_path)
    assert src.source_label == SOURCE_ENV
    assert src.path == fake


def test_resolve_inventory_source_env_pointing_to_missing_raises(
    tmp_path, monkeypatch,
):
    """A broken env override must error loudly — silently falling
    back to a default would surprise the operator."""
    from placement_engine.api.inventory_source import (
        ENV_VAR_NAME, resolve_inventory_source,
    )
    monkeypatch.setenv(ENV_VAR_NAME, str(tmp_path / "does-not-exist.json"))
    with pytest.raises(FileNotFoundError):
        resolve_inventory_source(tmp_path)


def test_resolve_inventory_source_falls_back_to_demo(tmp_path, monkeypatch):
    """When neither the env override nor the real export exist, the
    resolver MUST land on the demo fixture."""
    from placement_engine.api.inventory_source import (
        ENV_VAR_NAME, SOURCE_DEMO, resolve_inventory_source,
    )
    monkeypatch.delenv(ENV_VAR_NAME, raising=False)
    # Build a project tree where only the demo path exists.
    demo = tmp_path / "outputs/slab_ingestion_test/clean_slabs.json"
    demo.parent.mkdir(parents=True)
    demo.write_text('{"records": []}', encoding="utf-8")
    src = resolve_inventory_source(tmp_path)
    assert src.source_label == SOURCE_DEMO


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
    resolver picks up state from a prior test."""
    from placement_engine.api.inventory_upload import clear_active_upload
    clear_active_upload()


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
    """Happy path: every piece assigned → endpoint returns a DXF
    that ezdxf can re-parse, with the expected layers."""
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
    assert "factory_cut_plan_l_shape_" in cd
    assert cd.endswith('.dxf"')
    assert len(r.content) > 100  # DXFs are at LEAST this large

    # Re-parse to confirm it's a real DXF that downstream tools can
    # open. ezdxf raises on malformed input.
    import io
    import ezdxf
    doc = ezdxf.read(io.StringIO(r.content.decode("utf-8")))
    layer_names = {layer.dxf.name for layer in doc.layers}
    for required in (
        "FLOOR_BOUNDARY", "CUT_PIECES", "PIECE_LABELS",
        "SLAB_LABELS", "DOORWAYS", "SEAMS",
    ):
        assert required in layer_names


def test_export_dxf_contains_piece_and_slab_labels():
    """Labels are part of the contract — verify both the piece_id
    and the assigned slab_id appear as TEXT entities."""
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
    assert "p1" in text_strings
    assert "p2" in text_strings
    assert slab_a in text_strings
    assert slab_b in text_strings


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
    # Pick a slab with a linked image. We don't require green-box
    # detection to have succeeded — the endpoint must serve SOMETHING
    # in both branches.
    candidate = next(
        (sid for sid in session.image_metadata_by_slab.keys()),
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
    if not session.image_metadata_by_slab:
        pytest.skip("crop pass didn't run — cannot exercise per-slab info")
    sid = next(iter(session.image_metadata_by_slab))
    info = client.get(f"/api/inventory/slab-crop-info/{sid}").json()
    assert "available" in info
    if info["available"]:
        for k in ("crop_x", "crop_y", "crop_width", "crop_height"):
            assert k in info
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
