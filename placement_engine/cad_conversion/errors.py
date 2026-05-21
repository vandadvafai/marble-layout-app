"""Exceptions raised by the DWG → DXF conversion wrapper.

All inherit from `CADConversionError` so callers can catch the whole
family with one `except`. Messages are written to be actionable for
both the designer (what to do in Rhino/AutoCAD) and the developer.
"""

from __future__ import annotations


class CADConversionError(Exception):
    """Base class for every conversion-layer failure."""


class UnsupportedCADFormatError(CADConversionError):
    """The input file extension is neither .dxf nor .dwg."""


class ODANotFoundError(CADConversionError):
    """A DWG needs converting but no ODA File Converter could be located."""


class ConversionFailedError(CADConversionError):
    """The converter ran but did not produce a usable DXF."""
