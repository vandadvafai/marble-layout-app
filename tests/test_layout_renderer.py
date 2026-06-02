"""Smoke tests for the layout renderer."""

from __future__ import annotations

from pathlib import Path

from placement_engine.layout import (
    generate_tile_layout,
    render_layout_geometric,
)
from placement_engine.target_area import (
    TargetGeometry,
    load_target_geometry_from_dxf,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
EX_L = REPO_ROOT / "examples/cad_inputs/demo/demo_l_shape_floor.dxf"
EX_APT = REPO_ROOT / "examples/cad_inputs/demo/demo_irregular_apartment_floor.dxf"


def _rect(w: float, h: float) -> TargetGeometry:
    return TargetGeometry(
        target_id="rect", name="rect",
        boundary=[(0, 0), (w, 0), (w, h), (0, h)],
    )


def test_renders_clean_rectangle_layout_without_error(tmp_path: Path):
    layout = generate_tile_layout(_rect(6000, 3600), 1200, 600)
    out = render_layout_geometric(layout, tmp_path / "layout.png")
    assert out.exists() and out.stat().st_size > 1000


def test_renders_l_shape_layout_with_edge_pieces(tmp_path: Path):
    layout = generate_tile_layout(load_target_geometry_from_dxf(EX_L), 1200, 600)
    # L-shape must produce both flavours.
    assert layout.full_tile_count > 0 and layout.edge_piece_count > 0
    out = render_layout_geometric(layout, tmp_path / "l.png")
    assert out.exists()


def test_renders_apartment_layout_with_holes_and_labels(tmp_path: Path):
    layout = generate_tile_layout(load_target_geometry_from_dxf(EX_APT), 1200, 600)
    out = render_layout_geometric(layout, tmp_path / "apt.png", show_labels=True)
    assert out.exists()


def test_renderer_handles_empty_pieces_list_gracefully(tmp_path: Path):
    """Tile size larger than the target → at most one big edge piece."""
    layout = generate_tile_layout(_rect(500, 500), 10000, 10000)
    out = render_layout_geometric(layout, tmp_path / "single.png")
    assert out.exists()
