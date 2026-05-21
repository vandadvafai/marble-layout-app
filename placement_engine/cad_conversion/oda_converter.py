"""ODA File Converter backend — DWG → DXF via subprocess.

The ODA File Converter is a free external tool from the Open Design
Alliance. It is **not** bundled; the user installs it and the wrapper
locates it. We never parse DWG ourselves — ODA does the conversion and
we hand the resulting DXF to the existing intake pipeline.

ODA File Converter's CLI takes *folders*, not single files:

    ODAFileConverter <in_dir> <out_dir> <out_version> <out_type> <recurse> <audit> [filter]

so `convert_with_oda` stages the single DWG in a private temp folder,
runs the converter, and reads the produced DXF out of `output_dir`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from placement_engine.cad_conversion.errors import (
    ConversionFailedError,
    ODANotFoundError,
)

# Shown whenever a DWG needs converting but no converter is available.
ODA_MISSING_MESSAGE = (
    "Cannot convert DWG to DXF because ODA File Converter was not found. "
    "Set ODA_FILE_CONVERTER_PATH or pass --oda-path. Alternatively, export "
    "the DWG to DXF manually from Rhino/AutoCAD and run the tool with the DXF."
)

# Best-effort install locations checked after --oda-path / the env var.
# Real installs vary by version, so PATH lookup is the most reliable
# fallback — these are just conveniences.
_COMMON_PATHS: tuple[str, ...] = (
    "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter",
    r"C:\Program Files\ODA\ODAFileConverter\ODAFileConverter.exe",
    "/usr/bin/ODAFileConverter",
)

# DXF version ODA should emit. ACAD2018 is modern and read cleanly by
# ezdxf; the intake never depends on a specific version.
_OUTPUT_VERSION = "ACAD2018"


def find_oda_executable(explicit_path: str | os.PathLike | None = None) -> Path | None:
    """Locate the ODA File Converter executable.

    Search order: explicit `--oda-path`, then the
    `ODA_FILE_CONVERTER_PATH` environment variable, then a few common
    install locations, then anything named `ODAFileConverter` on PATH.
    Returns `None` if nothing usable is found.
    """
    candidates: list[str] = []
    if explicit_path:
        candidates.append(str(explicit_path))
    env_path = os.environ.get("ODA_FILE_CONVERTER_PATH")
    if env_path:
        candidates.append(env_path)
    candidates.extend(_COMMON_PATHS)

    for candidate in candidates:
        p = Path(candidate).expanduser()
        if p.is_file() and os.access(p, os.X_OK):
            return p

    on_path = shutil.which("ODAFileConverter")
    return Path(on_path) if on_path else None


def build_oda_command(
    executable: str | os.PathLike,
    input_dir: str | os.PathLike,
    output_dir: str | os.PathLike,
    output_version: str = _OUTPUT_VERSION,
) -> list[str]:
    """Build the ODA File Converter argument list.

    Factored out as a pure function so it can be unit-tested without an
    ODA install. Args: in-folder, out-folder, output version, output
    type (DXF), recurse (0 = no), audit (1 = yes).
    """
    return [
        str(executable),
        str(input_dir),
        str(output_dir),
        output_version,
        "DXF",
        "0",  # recurse
        "1",  # audit
    ]


def convert_with_oda(
    dwg_path: str | os.PathLike,
    output_dir: str | os.PathLike,
    oda_path: str | os.PathLike | None = None,
    *,
    timeout_seconds: int = 300,
) -> Path:
    """Convert one DWG to DXF using ODA File Converter.

    Returns the path to the produced DXF (inside `output_dir`). Raises
    `ODANotFoundError` if no converter is available, or
    `ConversionFailedError` if the converter runs but no DXF appears.
    """
    executable = find_oda_executable(oda_path)
    if executable is None:
        raise ODANotFoundError(ODA_MISSING_MESSAGE)

    dwg_path = Path(dwg_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    expected_dxf = output_dir / f"{dwg_path.stem}.dxf"

    # ODA converts every matching file in the input folder, so the DWG
    # is staged alone in a private temp folder.
    with tempfile.TemporaryDirectory(prefix="oda_dwg_in_") as staging:
        staged_dwg = Path(staging) / dwg_path.name
        shutil.copy2(dwg_path, staged_dwg)
        command = build_oda_command(executable, staging, output_dir)
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise ConversionFailedError(
                "DWG to DXF conversion timed out. The DWG may be very "
                "large or the converter may be waiting on a dialog. Try "
                "exporting to DXF manually from Rhino/AutoCAD."
            ) from exc
        except OSError as exc:
            raise ConversionFailedError(
                f"Could not run ODA File Converter ({executable}): {exc}"
            ) from exc

    # ODA File Converter is unreliable about exit codes, so the real
    # success signal is whether the output DXF was written.
    if not expected_dxf.is_file():
        raise ConversionFailedError(
            "DWG to DXF conversion failed. Please check that the DWG opens "
            "correctly in Rhino/AutoCAD and that it has been saved in a "
            "supported AutoCAD version. "
            f"(ODA File Converter exit code: {result.returncode})"
        )
    return expected_dxf
