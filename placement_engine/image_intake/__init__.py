"""Image processing layer: detect the green usable-area rectangle and crop it.

This subpackage is independent of `slab_intake/` (the Excel→JSON bridge)
and `inventory/` (the engine consumption layer). It consumes the same
``clean_slabs.json`` produced by ingestion and produces a parallel set
of *cropped* slab photos plus per-image metadata.

The engine does NOT depend on this layer to compute dimensions —
heights/widths still come from طول/عرض or the serial. Image processing
only affects how slab photos look when previewed/composed in layouts.

Public API:

    GreenBox                dataclass — detection result (bbox + confidence)
    detect_green_box(img)   detect the green usable-area rectangle in a
                            BGR image (OpenCV convention) — returns None
                            if no plausible rectangle was found
    crop_inside_green_box(img, bbox)
                            crop a small inset inside the detected
                            rectangle to remove the green line itself
    process_inventory(...)  top-level batch driver, used by the CLI

See `placement_engine/image_intake/green_box.py` for the detection
heuristics; `processor.py` for the I/O orchestration.
"""

from placement_engine.image_intake.green_box import (
    GreenBox,
    crop_inside_green_box,
    detect_green_box,
)
from placement_engine.image_intake.processor import (
    ImageMetadata,
    ProcessingResult,
    process_inventory,
    write_outputs,
)

__all__ = [
    "GreenBox",
    "ImageMetadata",
    "ProcessingResult",
    "crop_inside_green_box",
    "detect_green_box",
    "process_inventory",
    "write_outputs",
]
