"""M3 — Factory Fit Checker & DXF policy regressions.

Confirmed factory policy under test:

  * Excel dimensions stay the PHYSICAL slab size, kept only for
    traceability / labels.
  * Usable slab dimensions = Excel dims − 20 mm/side, applied exactly
    ONCE, inside calibration
    (``placement_engine.calibration.policy.usable_dimensions_mm``).
  * Downstream (fit checker + DXF writer), ``edge_trim_mm`` defaults
    to 0 so that 20 mm/side is never deducted a second time.
  * Multiple pieces sharing one slab get exactly 5 mm total spacing
    between neighbouring cut contours — the blade kerf is NOT added
    on top of that spacing.
  * Only APPROVED calibration records reach the layout engine (the
    matcher / fit checker / DXF writer all read the same
    ``clean_slabs.json``, which only ever contains approved rows).
  * DXF geometry (slab boundary rectangle, piece placement) uses the
    USABLE rectangle; only the DIMENSIONS label text shows the
    physical Excel size.
"""
from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from placement_engine.api.main import app
from placement_engine.api.inventory_upload import clear_active_upload
from placement_engine.calibration.policy import (
    EDGE_DEDUCTION_TOTAL_MM, INTER_PIECE_SPACING_MM,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch, tmp_path):
    """Isolated ``AVANDAD_DATA_DIR`` + no leftover upload per test —
    same pattern as ``tests/test_portability.py``."""
    monkeypatch.delenv("AVANDAD_INVENTORY_PATH", raising=False)
    monkeypatch.delenv("STONELAYOUT_INVENTORY_PATH", raising=False)
    monkeypatch.setenv("AVANDAD_DATA_DIR", str(tmp_path / "data"))
    clear_active_upload()
    import placement_engine.api.inventory_upload as _iu
    _iu._ACTIVE_UPLOAD = None
    yield
    clear_active_upload()
    _iu._ACTIVE_UPLOAD = None


def _upload_one_slab(
    slab_id: str = "M3-1", width_cm: float = 160, height_cm: float = 160,
    *, approve: bool = True,
) -> str:
    """Upload a single-row inventory (no photo) and optionally
    force-approve it via the review UI's Approve action — the same
    endpoint the frontend calls when the operator clicks Approve."""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["Serial", "Width (cm)", "Height (cm)"])
    ws.append([slab_id, width_cm, height_cm])
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post(
        "/api/inventory/upload",
        files={"excel": ("m3.xlsx", buf.getvalue(),
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert r.status_code == 200, r.text
    if approve:
        approved = client.post(
            f"/api/calibration/{slab_id}/status", json={"status": "approved"},
        )
        assert approved.status_code == 200, approved.text
    return slab_id


def _square_piece(piece_id: str, size_mm: float, x0: float = 0.0) -> dict:
    return {
        "piece_id": piece_id,
        "polygon": [
            [x0, 0], [x0 + size_mm, 0],
            [x0 + size_mm, size_mm], [x0, size_mm], [x0, 0],
        ],
        "nominal_width_mm": size_mm,
        "nominal_height_mm": size_mm,
    }


# ---------------------------------------------------------------------------
# edge_trim_mm defaults to 0 — no double deduction
# ---------------------------------------------------------------------------


def test_edge_deduction_applied_exactly_once():
    """160 cm Excel slab -> usable is 1600 - 40 = 1560 mm. The fit
    checker's ``slab_width_mm`` (what the matcher/export already
    resolved) and its ``usable_width_mm`` (after the policy's OWN
    edge-trim step) must be the SAME number — proving the 20 mm/side
    deduction happened once, at calibration, and V1's default
    ``edge_trim_mm=0`` doesn't deduct it again."""
    assert EDGE_DEDUCTION_TOTAL_MM == 40.0
    slab_id = _upload_one_slab(width_cm=160, height_cm=160)
    piece = _square_piece("p1", 1560.0)
    r = client.post(
        "/api/demo-layouts/l_shape/validate-factory-fit",
        json={"pieces": [piece], "assignments": {"p1": slab_id}},
    )
    assert r.status_code == 200, r.text
    row = r.json()["results"][0]
    assert row["slab_width_mm"] == pytest.approx(1560.0, abs=0.5)
    assert row["slab_height_mm"] == pytest.approx(1560.0, abs=0.5)
    assert row["usable_width_mm"] == pytest.approx(1560.0, abs=0.5)
    assert row["usable_height_mm"] == pytest.approx(1560.0, abs=0.5)
    assert row["verdict"] == "ready"
    assert row["factory_ready"] is True


# ---------------------------------------------------------------------------
# DXF: usable rectangle for geometry, physical Excel dims for labels
# ---------------------------------------------------------------------------


def test_dxf_geometry_uses_usable_rect_label_uses_excel_dims():
    slab_id = _upload_one_slab(width_cm=160, height_cm=160)
    piece = _square_piece("p1", 1560.0)
    r = client.post(
        "/api/demo-layouts/l_shape/export-dxf",
        json={"pieces": [piece], "assignments": {"p1": slab_id}},
    )
    assert r.status_code == 200, r.text

    import ezdxf
    doc = ezdxf.read(io.StringIO(r.content.decode("utf-8")))
    msp = doc.modelspace()

    boundary = next(
        lw for lw in msp.query("LWPOLYLINE") if lw.dxf.layer == "SLAB_BOUNDARY"
    )
    xs = [v[0] for v in boundary.vertices()]
    ys = [v[1] for v in boundary.vertices()]
    # Geometry is the USABLE rectangle (1560), not the physical Excel
    # size (1600) — the deduction already happened at calibration.
    assert max(xs) - min(xs) == pytest.approx(1560.0, abs=0.5)
    assert max(ys) - min(ys) == pytest.approx(1560.0, abs=0.5)

    dims_texts = [
        e.dxf.text for e in msp.query("TEXT") if e.dxf.layer == "DIMENSIONS"
    ]
    all_text = " ".join(dims_texts)
    # Label carries the PHYSICAL Excel dims for traceability.
    assert "1600" in all_text, all_text
    # And the usable rectangle is still noted for the operator, just
    # not as the primary/only figure.
    assert "1560" in all_text, all_text


# ---------------------------------------------------------------------------
# Multiple pieces on one slab — exactly 5 mm spacing, no kerf added
# ---------------------------------------------------------------------------


def test_multi_piece_slab_uses_exact_inter_piece_spacing():
    assert INTER_PIECE_SPACING_MM == 5.0
    slab_id = _upload_one_slab(width_cm=160, height_cm=200)
    pieces = [_square_piece("p1", 500.0), _square_piece("p2", 500.0, x0=600.0)]
    r = client.post(
        "/api/demo-layouts/l_shape/export-factory-package",
        json={
            "pieces": pieces,
            "assignments": {"p1": slab_id, "p2": slab_id},
        },
    )
    assert r.status_code == 200, r.text
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    slab_file = next(n for n in zf.namelist() if "_Slab_" in n)

    import ezdxf
    doc = ezdxf.read(io.StringIO(zf.read(slab_file).decode("utf-8")))
    msp = doc.modelspace()
    cuts = [
        list(lw.vertices())
        for lw in msp.query("LWPOLYLINE") if lw.dxf.layer == "CUT_PIECES"
    ]
    assert len(cuts) == 2

    def y_range(pts):
        ys = [p[1] for p in pts]
        return min(ys), max(ys)

    ranges = sorted((y_range(c) for c in cuts), key=lambda r: r[0])
    gap = ranges[1][0] - ranges[0][1]
    # Exactly the factory's 5 mm spacing — NOT kerf(3)*2 + trim(0) = 6,
    # and not the historic kerf*2 + edge_trim(5) = 11 formula either.
    assert gap == pytest.approx(INTER_PIECE_SPACING_MM, abs=0.1)


# ---------------------------------------------------------------------------
# Only approved slabs enter the layout engine
# ---------------------------------------------------------------------------


def test_only_approved_slabs_enter_layout_engine():
    """A slab left ``missing_photo`` (never approved) must not be a
    match candidate — the matcher, fit checker and DXF writer all
    read the same ``clean_slabs.json``, which only ever contains
    APPROVED rows."""
    slab_id = _upload_one_slab(
        slab_id="UNAPPROVED-1", width_cm=160, height_cm=160, approve=False,
    )
    counts = client.get("/api/calibration/records").json()["counts"]
    assert counts["approved"] == 0
    assert counts["missing_photo"] == 1

    match = client.post(
        "/api/demo-layouts/l_shape/match-inventory",
        json={
            "pieces": [{
                "piece_id": "p1",
                "nominal_width_mm": 500, "nominal_height_mm": 500,
            }],
            "top_k": 50,
        },
    )
    assert match.status_code == 200
    candidates = match.json()["pieces"][0]["candidates"]
    slab_ids = {c["slab_id"] for c in candidates}
    assert slab_id not in slab_ids

    # The export endpoint can't resolve the unapproved slab's
    # metadata either — it 400s instead of silently emitting a DXF
    # against phantom dimensions.
    piece = _square_piece("p1", 500.0)
    export = client.post(
        "/api/demo-layouts/l_shape/export-dxf",
        json={"pieces": [piece], "assignments": {"p1": slab_id}},
    )
    assert export.status_code == 400
