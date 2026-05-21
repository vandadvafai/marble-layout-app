"""Front door for CAD input conversion.

`convert_cad_to_dxf` is the single entry point the rest of the engine
calls. It normalises any supported CAD input to a DXF path:

  * `.dxf` → returned unchanged (passthrough; the intake reads it directly)
  * `.dwg` → converted to DXF via the selected backend
  * anything else → `UnsupportedCADFormatError`

This is a **wrapper**, not a parser: DWG bytes are never interpreted
here — an external converter (ODA File Converter) does that, and the
existing standardized-DXF intake pipeline runs unchanged afterwards.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from placement_engine.cad_conversion.errors import (
    CADConversionError,
    ODANotFoundError,
    UnsupportedCADFormatError,
)
from placement_engine.cad_conversion.oda_converter import (
    ODA_MISSING_MESSAGE,
    convert_with_oda,
)

Backend = Literal["auto", "oda", "none"]

SUPPORTED_EXTENSIONS = (".dxf", ".dwg")


@dataclass(frozen=True)
class ConversionResult:
    """Outcome of normalising a CAD input to DXF.

    `dxf_path` is always a usable DXF. `was_converted` is False for a
    DXF passthrough (in which case `dxf_path == original_path`) and
    True when a DWG was converted. `backend` records how: `passthrough`
    or `oda`.
    """

    dxf_path: Path
    original_path: Path
    was_converted: bool
    backend: str

    @property
    def original_format(self) -> str:
        """Lower-case extension of the file the user supplied."""
        return self.original_path.suffix.lower()


def convert_cad_to_dxf(
    input_path: str | os.PathLike,
    output_dir: str | os.PathLike,
    backend: Backend = "auto",
    oda_path: str | os.PathLike | None = None,
) -> ConversionResult:
    """Normalise a CAD input to a DXF and report how it was obtained.

    `output_dir` is only used (and only created) when a real conversion
    happens — a DXF passthrough touches nothing on disk.

    `backend`:
      * `auto` — convert DWG via ODA if available (the normal choice)
      * `oda`  — same as auto today; explicit for forward-compatibility
      * `none` — no conversion; DXF passes through, DWG fails clearly
                 (useful in tests so ODA is never invoked accidentally)
    """
    input_path = Path(input_path)
    if not input_path.is_file():
        raise CADConversionError(f"CAD input file does not exist: {input_path}")

    ext = input_path.suffix.lower()

    if ext == ".dxf":
        return ConversionResult(
            dxf_path=input_path,
            original_path=input_path,
            was_converted=False,
            backend="passthrough",
        )

    if ext == ".dwg":
        if backend == "none":
            raise ODANotFoundError(ODA_MISSING_MESSAGE)
        # 'auto' and 'oda' both route to the ODA backend today.
        dxf_path = convert_with_oda(input_path, Path(output_dir), oda_path)
        return ConversionResult(
            dxf_path=dxf_path,
            original_path=input_path,
            was_converted=True,
            backend="oda",
        )

    raise UnsupportedCADFormatError(
        f"Unsupported CAD input format: {ext or '(none)'}. "
        f"Supported formats are .dxf and .dwg."
    )
