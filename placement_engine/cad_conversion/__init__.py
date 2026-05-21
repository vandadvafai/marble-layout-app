"""DWG → DXF conversion wrapper.

The engine's intake pipeline only understands standardized DXF. This
package lets a DWG be accepted by converting it to DXF first, then
handing the result to the unchanged DXF intake. It does **not** parse
DWG natively — an external converter (ODA File Converter) does that.

Public surface:
    convert_cad_to_dxf   normalise a .dxf/.dwg input to a DXF path
    ConversionResult     what conversion produced (paths, backend, flag)
    CADConversionError   base exception for the whole family
"""

from placement_engine.cad_conversion.converter import (
    SUPPORTED_EXTENSIONS,
    ConversionResult,
    convert_cad_to_dxf,
)
from placement_engine.cad_conversion.errors import (
    CADConversionError,
    ConversionFailedError,
    ODANotFoundError,
    UnsupportedCADFormatError,
)

__all__ = [
    "convert_cad_to_dxf",
    "ConversionResult",
    "SUPPORTED_EXTENSIONS",
    "CADConversionError",
    "ConversionFailedError",
    "ODANotFoundError",
    "UnsupportedCADFormatError",
]
