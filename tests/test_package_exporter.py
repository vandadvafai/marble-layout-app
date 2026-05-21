"""Package exporter — file presence + naming + multi-strategy support."""
from pathlib import Path

import pytest

from placement_engine import engine
from placement_engine.exporters.package import write_package

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def test_package_writes_one_file_set_per_option(tmp_path):
    pi = engine.load_input_from_file(
        EXAMPLES / "input_lowest_waste_corridor_offcut.json"
    )
    output = engine.run(pi)
    written = write_package(pi, output, tmp_path, render_preview=False)

    assert set(written.keys()) == {"balanced", "lowest_waste"}
    for strategy, files in written.items():
        names = {f.name for f in files}
        assert f"layout_{strategy}.json" in names
        assert f"layout_{strategy}.dxf" in names
        assert f"layout_{strategy}_report.md" in names
        for f in files:
            assert f.exists(), f"package missed {f}"
            assert f.stat().st_size > 0


def test_package_with_preview_includes_png(tmp_path):
    pi = engine.load_input_from_file(EXAMPLES / "input_floor_simple.json")
    output = engine.run(pi)
    written = write_package(pi, output, tmp_path, render_preview=True)
    files = written["balanced"]
    names = {f.name for f in files}
    assert "layout_balanced_preview.png" in names
    png = next(f for f in files if f.suffix == ".png")
    # PNG magic bytes.
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_package_can_filter_to_a_single_strategy(tmp_path):
    pi = engine.load_input_from_file(
        EXAMPLES / "input_lowest_waste_corridor_offcut.json"
    )
    output = engine.run(pi)
    only_lw = [o for o in output.layout_options if o.strategy == "lowest_waste"]
    written = write_package(pi, output, tmp_path, options=only_lw, render_preview=False)
    assert list(written.keys()) == ["lowest_waste"]
    # Files for the other strategy must NOT be present.
    assert not (tmp_path / "layout_balanced.dxf").exists()


def test_package_per_option_json_contains_only_that_option(tmp_path):
    """The per-option JSON in the package should be the trimmed view —
    if the engine emitted two options, the JSON next to the lowest_waste
    DXF must contain *only* the lowest_waste option, so designers don't
    confuse strategies."""
    import json

    pi = engine.load_input_from_file(
        EXAMPLES / "input_lowest_waste_corridor_offcut.json"
    )
    output = engine.run(pi)
    write_package(pi, output, tmp_path, render_preview=False)

    bal = json.loads((tmp_path / "layout_balanced.json").read_text())
    low = json.loads((tmp_path / "layout_lowest_waste.json").read_text())
    assert [o["strategy"] for o in bal["layout_options"]] == ["balanced"]
    assert [o["strategy"] for o in low["layout_options"]] == ["lowest_waste"]


def test_package_raises_when_no_options_chosen(tmp_path):
    pi = engine.load_input_from_file(EXAMPLES / "input_floor_simple.json")
    output = engine.run(pi)
    with pytest.raises(ValueError, match="no layout options"):
        write_package(pi, output, tmp_path, options=[])
