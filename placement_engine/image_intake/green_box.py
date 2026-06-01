"""Green-rectangle detection for slab photos.

The factory tool burns a bright green rectangle into the slab image to
mark the usable-area boundary. (A second, larger red rectangle is also
present but we ignore it — see the module docstring of `__init__.py`.)
This module finds that green rectangle and returns its axis-aligned
bounding box plus a coverage-based confidence score.

No file I/O lives here. Everything operates on NumPy arrays in
**OpenCV BGR convention**. The processor module handles disk reads and
writes around these primitives.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

# HSV thresholds for "bright pure green". OpenCV's H channel is 0-179
# (a half-turn) and pure green sits near 60. The range is intentionally
# wide on H to tolerate JPEG colour drift and narrow on S/V to reject
# faded background pixels.
DEFAULT_GREEN_HSV_LOWER: tuple[int, int, int] = (40, 80, 80)
DEFAULT_GREEN_HSV_UPPER: tuple[int, int, int] = (85, 255, 255)

# The detected bbox must cover at least this fraction of the image area
# to be accepted. Stops a few stray green specks from being mistaken
# for a usable-area rectangle.
MIN_BBOX_AREA_FRACTION: float = 0.05

# Default inset applied when cropping inside the detected rectangle.
# Removes the green line itself plus a small margin of JPEG bleed and
# the morphological-close growth around the line. The absolute floor
# guarantees a clean cut even on tiny test images; the fraction keeps
# the inset proportional on the huge factory photos.
DEFAULT_INSET_FRACTION: float = 0.01  # 1% of the smaller bbox side
DEFAULT_INSET_MIN_PX: int = 10


@dataclass(frozen=True)
class GreenBox:
    """Axis-aligned bbox of the detected green rectangle (pixel units).

    ``confidence`` is a coverage ratio: how much of the bbox's perimeter
    actually showed up as green. 1.0 means a complete, unbroken
    rectangle; values around 0.6+ typically mean a real rectangle with
    minor gaps; very low values (< 0.2) indicate noise that the
    `MIN_BBOX_AREA_FRACTION` guard didn't catch.
    """

    x: int
    y: int
    width: int
    height: int
    confidence: float


def _green_mask(
    image_bgr: np.ndarray,
    lower_hsv: tuple[int, int, int],
    upper_hsv: tuple[int, int, int],
) -> np.ndarray:
    """Return a binary mask of green pixels."""
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array(lower_hsv), np.array(upper_hsv))
    # Close 1–2 pixel gaps in the green line so it shows up as a single
    # connected component rather than dozens of dashes.
    kernel = np.ones((5, 5), np.uint8)
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)


def _largest_component_bbox(
    mask: np.ndarray,
) -> tuple[int, int, int, int, int] | None:
    """Return ``(x, y, w, h, green_pixel_count)`` for the largest blob.

    Largest is measured by *bounding-box area* — a partial rectangle
    with broken sides still wins against tiny solid blobs of noise.
    """
    num, _labels, stats, _centroids = cv2.connectedComponentsWithStats(
        mask, connectivity=8
    )
    if num <= 1:
        return None
    best: tuple[int, int, int, int, int] | None = None
    best_bbox_area = 0
    for i in range(1, num):  # 0 is background
        x, y, w, h, area = (int(v) for v in stats[i])
        bbox_area = w * h
        if bbox_area > best_bbox_area:
            best_bbox_area = bbox_area
            best = (x, y, w, h, area)
    return best


def detect_green_box(
    image_bgr: np.ndarray,
    *,
    lower_hsv: tuple[int, int, int] = DEFAULT_GREEN_HSV_LOWER,
    upper_hsv: tuple[int, int, int] = DEFAULT_GREEN_HSV_UPPER,
    min_area_fraction: float = MIN_BBOX_AREA_FRACTION,
) -> GreenBox | None:
    """Detect the largest plausible green rectangle in ``image_bgr``.

    Returns ``None`` when no candidate is found, when the largest
    candidate covers less than ``min_area_fraction`` of the image, or
    when its perimeter coverage is too low to credibly be a rectangle.
    """
    if image_bgr is None or image_bgr.size == 0:
        return None
    img_h, img_w = image_bgr.shape[:2]

    mask = _green_mask(image_bgr, lower_hsv, upper_hsv)
    component = _largest_component_bbox(mask)
    if component is None:
        return None

    x, y, w, h, green_pixel_count = component
    # Reject obvious noise: tiny components.
    if w * h < min_area_fraction * img_w * img_h:
        return None

    # Confidence = green-pixel count / a 1-pixel-wide perimeter
    # estimate. Real rectangles drawn at 2–5 px line widths will score
    # comfortably above 1.0 here; we cap at 1.0 for readability.
    perimeter_est = 2 * (w + h)
    confidence = min(1.0, green_pixel_count / max(1, perimeter_est))
    if confidence < 0.2:
        return None

    return GreenBox(x=x, y=y, width=w, height=h, confidence=confidence)


def crop_inside_green_box(
    image_bgr: np.ndarray,
    box: GreenBox,
    *,
    inset_fraction: float = DEFAULT_INSET_FRACTION,
    inset_min_px: int = DEFAULT_INSET_MIN_PX,
) -> np.ndarray:
    """Return the image region inside the green rectangle.

    A small inset (``max(inset_min_px, inset_fraction × min(w, h))``) is
    applied so the cropped output does not contain the green line
    itself.
    """
    inset = max(inset_min_px, int(min(box.width, box.height) * inset_fraction))
    x1 = max(0, box.x + inset)
    y1 = max(0, box.y + inset)
    x2 = min(image_bgr.shape[1], box.x + box.width - inset)
    y2 = min(image_bgr.shape[0], box.y + box.height - inset)
    if x2 <= x1 or y2 <= y1:
        # The inset ate the whole box — fall back to the box as-is.
        x1, y1 = box.x, box.y
        x2 = box.x + box.width
        y2 = box.y + box.height
    return image_bgr[y1:y2, x1:x2]
