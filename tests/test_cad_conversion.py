"""DWG → DXF conversion wrapper.

ODA File Converter is an external tool that may not be installed, so
the suite never depends on it: the ODA-backend tests mock `subprocess`,
and the one real end-to-end conversion test is skipped unless
`ODA_FILE_CONVERTER_PATH` points at a working executable.
"""
import os
from pathlib import Path
from unittest import mock

import ezdxf
import pytest

from placement_engine.cad_conversion import (
    CADConversionError,
    ConversionResult,
    ODANotFoundError,
    UnsupportedCADFormatError,
    convert_cad_to_dxf,
)
from placement_engine.cad_conversion.oda_converter import (
    build_oda_command,
    convert_with_oda,
    find_oda_executable,
)

EXAMPLES_CAD = Path(__file__).resolve().parents[1] / "examples" / "cad_inputs"


# ---------------------------------------------------------------------------
# 1. DXF passthrough
# ---------------------------------------------------------------------------


def test_dxf_input_passes_through_unchanged(tmp_path):
    dxf = EXAMPLES_CAD / "demo" / "demo_rectangle_floor.dxf"
    result = convert_cad_to_dxf(dxf, tmp_path / "intermediate")
    assert isinstance(result, ConversionResult)
    assert result.dxf_path == dxf
    assert result.original_path == dxf
    assert result.was_converted is False
    assert result.backend == "passthrough"
    # A passthrough must not create the intermediate folder.
    assert not (tmp_path / "intermediate").exists()


def test_dxf_passthrough_works_with_backend_none(tmp_path):
    """backend='none' blocks DWG conversion but DXF still passes through."""
    dxf = EXAMPLES_CAD / "demo" / "demo_l_shape_floor.dxf"
    result = convert_cad_to_dxf(dxf, tmp_path, backend="none")
    assert result.was_converted is False


# ---------------------------------------------------------------------------
# 2. Unsupported extension
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ext", [".pdf", ".txt", ".png"])
def test_unsupported_extension_raises(tmp_path, ext):
    bad = tmp_path / f"plan{ext}"
    bad.write_text("not a cad file")
    with pytest.raises(UnsupportedCADFormatError, match="Unsupported CAD input format"):
        convert_cad_to_dxf(bad, tmp_path / "out")


def test_missing_input_file_raises(tmp_path):
    with pytest.raises(CADConversionError, match="does not exist"):
        convert_cad_to_dxf(tmp_path / "nope.dxf", tmp_path / "out")


# ---------------------------------------------------------------------------
# 3. DWG without a converter
# ---------------------------------------------------------------------------


def test_dwg_with_backend_none_raises_actionable_error(tmp_path):
    dwg = tmp_path / "project.dwg"
    dwg.write_bytes(b"fake dwg bytes")  # content irrelevant — backend=none short-circuits
    with pytest.raises(ODANotFoundError) as exc:
        convert_cad_to_dxf(dwg, tmp_path / "out", backend="none")
    msg = str(exc.value)
    assert "ODA File Converter" in msg
    assert "ODA_FILE_CONVERTER_PATH" in msg
    assert "manually" in msg  # mentions the manual-export fallback


def test_dwg_with_auto_backend_and_no_oda_raises(tmp_path):
    """With backend='auto' but no ODA found anywhere, conversion fails clearly."""
    dwg = tmp_path / "project.dwg"
    dwg.write_bytes(b"fake dwg bytes")
    with mock.patch(
        "placement_engine.cad_conversion.oda_converter.find_oda_executable",
        return_value=None,
    ):
        with pytest.raises(ODANotFoundError):
            convert_cad_to_dxf(dwg, tmp_path / "out", backend="auto")


# ---------------------------------------------------------------------------
# 4. ODA command construction (no real ODA needed)
# ---------------------------------------------------------------------------


def test_build_oda_command_shape():
    cmd = build_oda_command("/opt/ODAFileConverter", "/in", "/out")
    assert cmd == [
        "/opt/ODAFileConverter", "/in", "/out", "ACAD2018", "DXF", "0", "1",
    ]


def test_build_oda_command_respects_output_version():
    cmd = build_oda_command("/x/ODA", "/in", "/out", output_version="ACAD2013")
    assert cmd[3] == "ACAD2013"
    assert cmd[4] == "DXF"


def test_convert_with_oda_invokes_converter_and_returns_dxf(tmp_path):
    """Mock the ODA subprocess: verify the command and that the produced
    DXF path is returned. No real ODA needed."""
    dwg = tmp_path / "myfloor.dwg"
    dwg.write_bytes(b"fake dwg")
    out_dir = tmp_path / "converted"
    fake_exe = tmp_path / "ODAFileConverter"
    fake_exe.write_text("#!/bin/sh\n")

    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        # ODA writes <stem>.dxf into the output folder (cmd[2]).
        produced = Path(command[2]) / "myfloor.dxf"
        produced.parent.mkdir(parents=True, exist_ok=True)
        produced.write_text("0\nSECTION\n")  # minimal placeholder
        return mock.Mock(returncode=0, stdout="", stderr="")

    with mock.patch(
        "placement_engine.cad_conversion.oda_converter.find_oda_executable",
        return_value=fake_exe,
    ), mock.patch(
        "placement_engine.cad_conversion.oda_converter.subprocess.run",
        side_effect=fake_run,
    ):
        dxf_path = convert_with_oda(dwg, out_dir)

    assert dxf_path == out_dir / "myfloor.dxf"
    assert dxf_path.is_file()
    cmd = captured["command"]
    assert cmd[0] == str(fake_exe)
    assert cmd[2] == str(out_dir)
    assert cmd[3:] == ["ACAD2018", "DXF", "0", "1"]


def test_convert_with_oda_raises_when_no_dxf_produced(tmp_path):
    """If the converter runs but writes no DXF, raise ConversionFailedError."""
    dwg = tmp_path / "broken.dwg"
    dwg.write_bytes(b"fake dwg")
    fake_exe = tmp_path / "ODAFileConverter"
    fake_exe.write_text("#!/bin/sh\n")

    with mock.patch(
        "placement_engine.cad_conversion.oda_converter.find_oda_executable",
        return_value=fake_exe,
    ), mock.patch(
        "placement_engine.cad_conversion.oda_converter.subprocess.run",
        return_value=mock.Mock(returncode=1, stdout="", stderr="boom"),
    ):
        with pytest.raises(CADConversionError, match="conversion failed"):
            convert_with_oda(dwg, tmp_path / "out")


def test_find_oda_executable_prefers_explicit_path(tmp_path):
    fake_exe = tmp_path / "ODAFileConverter"
    fake_exe.write_text("#!/bin/sh\n")
    fake_exe.chmod(0o755)
    found = find_oda_executable(explicit_path=fake_exe)
    assert found == fake_exe


def test_find_oda_executable_returns_none_when_nothing_found(tmp_path, monkeypatch):
    monkeypatch.delenv("ODA_FILE_CONVERTER_PATH", raising=False)
    with mock.patch(
        "placement_engine.cad_conversion.oda_converter.shutil.which",
        return_value=None,
    ), mock.patch(
        "placement_engine.cad_conversion.oda_converter._COMMON_PATHS", ()
    ):
        assert find_oda_executable() is None


# ---------------------------------------------------------------------------
# 5 & 6. DXF still works through the intake + inspection
# ---------------------------------------------------------------------------


def test_cad_to_input_dxf_still_works(tmp_path):
    from placement_engine.cad_intake import build_project_input_dict

    payload = build_project_input_dict(
        EXAMPLES_CAD / "demo" / "demo_rectangle_floor.dxf",
        project_id="conv_dxf_001",
        include_test_slabs=True,
    )
    sf = payload["layout"]["source_file"]
    assert sf["type"] == "standardized_dxf"
    assert sf["converted_dxf_path"] is None
    assert payload["slabs"]


def test_inspect_cad_file_dxf_still_works():
    from placement_engine.cad_intake.inspection import inspect_cad_file

    report = inspect_cad_file(EXAMPLES_CAD / "demo" / "demo_floor_with_column.dxf")
    assert report.errors == []
    assert report.boundary_polyline_count == 1
    assert report.original_format == ".dxf"
    assert report.conversion_backend == "passthrough"
    assert report.converted_dxf_path is None  # no conversion for a DXF


# ---------------------------------------------------------------------------
# 7. Optional real ODA integration test
# ---------------------------------------------------------------------------


_ODA_ENV = os.environ.get("ODA_FILE_CONVERTER_PATH")
_DWG_FIXTURE = EXAMPLES_CAD / "demo" / "demo_rectangle_floor.dwg"


@pytest.mark.skipif(
    not _ODA_ENV or not _DWG_FIXTURE.is_file(),
    reason="needs ODA_FILE_CONVERTER_PATH set and a real .dwg fixture present",
)
def test_real_dwg_conversion_and_intake(tmp_path):
    """End-to-end with a real ODA install (skipped in normal CI)."""
    from placement_engine.cad_intake import build_project_input_dict
    from placement_engine.cad_intake.inspection import inspect_cad_file

    result = convert_cad_to_dxf(_DWG_FIXTURE, tmp_path / "conv", backend="oda")
    assert result.was_converted is True
    assert result.dxf_path.is_file()

    report = inspect_cad_file(_DWG_FIXTURE, intermediate_dir=tmp_path / "insp")
    assert report.original_format == ".dwg"
    assert report.converted_dxf_path is not None

    payload = build_project_input_dict(
        _DWG_FIXTURE, project_id="real_dwg_001", include_test_slabs=True,
        intermediate_dir=tmp_path / "build",
    )
    assert payload["layout"]["source_file"]["type"] == "standardized_dwg_converted_to_dxf"
