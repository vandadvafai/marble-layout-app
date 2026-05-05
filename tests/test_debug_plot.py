"""Smoke tests for the matplotlib debug plot.

These are deliberately minimal: matplotlib output is hard to assert against
beyond "a non-empty PNG was produced." The point is to catch import errors,
schema-shape mismatches, and crashes on hole geometry.
"""
from pathlib import Path

import pytest

from placement_engine import engine
from placement_engine.visualization.debug_plot import render_layout

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.mark.parametrize("input_name", [
    "input_floor_simple.json",
    "input_floor_with_hole.json",
])
def test_render_layout_writes_png(tmp_path, input_name):
    project = engine.load_input_from_file(EXAMPLES / input_name)
    output = engine.run(project)

    target = tmp_path / "plot.png"
    written = render_layout(project, output, target)

    assert written == target
    assert written.exists()
    assert written.stat().st_size > 1024, "PNG looks empty (smaller than 1 KiB)"
    # PNG magic bytes — confirms matplotlib actually wrote a PNG, not a stub.
    assert written.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
