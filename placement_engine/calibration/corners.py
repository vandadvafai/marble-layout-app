"""Corner detection + perspective correction for slab photos.

Two entry points:

* ``detect_slab_corners(image)`` — best-effort automatic corner
  detection using threshold + morphology + contour approximation.
  Returns ``(corners, confidence)`` or ``None`` when it can't find
  a plausible quadrilateral.
* ``rectify_to_dims(image, corners, target_w_px, target_h_px)`` —
  perspective-warp the image so ``corners`` map to the corners of
  a rectangle sized ``target_w_px × target_h_px``.

Kept pure-NumPy so the calibration pipeline can be tested without
touching disk. Wide-tolerant of small images so unit tests can use
synthetic 128 × 128 fixtures.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from placement_engine.calibration.models import SlabCorners


@dataclass(frozen=True)
class CornerDetectionResult:
    corners: SlabCorners
    confidence: float
    rectangularity: float  # 0..1, how close to a rectangle
    coverage: float        # 0..1, quad area / image area


def _order_corners_tl_tr_br_bl(
    pts: np.ndarray,
) -> tuple[tuple[float, float], ...]:
    """Sort four (x, y) points into (TL, TR, BR, BL) canvas order.

    Uses the classic "sum + diff" trick: TL has the smallest x+y,
    BR the largest. TR has the smallest x-y, BL the largest.
    Works regardless of the input ordering.
    """
    pts = pts.reshape(4, 2).astype(np.float64)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).reshape(-1)
    tl = pts[np.argmin(s)]
    br = pts[np.argmax(s)]
    tr = pts[np.argmin(d)]
    bl = pts[np.argmax(d)]
    return (
        (float(tl[0]), float(tl[1])),
        (float(tr[0]), float(tr[1])),
        (float(br[0]), float(br[1])),
        (float(bl[0]), float(bl[1])),
    )


def _quad_area(corners: SlabCorners) -> float:
    """Signed-shoelace area (positive)."""
    pts = [
        corners.top_left, corners.top_right,
        corners.bottom_right, corners.bottom_left,
    ]
    a = 0.0
    for i in range(4):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % 4]
        a += x0 * y1 - x1 * y0
    return abs(a) * 0.5


def _rectangularity(corners: SlabCorners) -> float:
    """How close the quad is to an axis-aligned rectangle.

    We compare the ratios of opposite side lengths — an ideal
    rectangle scores 1.0. Perspective-skewed quads still score
    reasonably well (side pairs are equal because it's a rectangle
    in world coordinates), so this is a "boxiness" signal more
    than an axis-aligned check.
    """
    def _dist(a, b):
        return float(np.hypot(a[0] - b[0], a[1] - b[1]))
    top = _dist(corners.top_left, corners.top_right)
    right = _dist(corners.top_right, corners.bottom_right)
    bottom = _dist(corners.bottom_left, corners.bottom_right)
    left = _dist(corners.top_left, corners.bottom_left)
    if top <= 0 or bottom <= 0 or left <= 0 or right <= 0:
        return 0.0
    horiz_ratio = min(top, bottom) / max(top, bottom)
    vert_ratio = min(left, right) / max(left, right)
    return float(min(horiz_ratio, vert_ratio))


def detect_slab_corners(
    image_bgr: np.ndarray,
    *,
    min_coverage: float = 0.10,
) -> CornerDetectionResult | None:
    """Auto-detect the slab quadrilateral in ``image_bgr``.

    Pipeline:
      1. Grayscale + Gaussian blur.
      2. Adaptive threshold to isolate the slab from the
         background.
      3. Morphological close to bridge small gaps.
      4. Find the largest external contour.
      5. Approximate to a polygon; accept only 4-vertex results.

    Returns ``None`` when no quadrilateral survives the checks so
    the caller can send the slab to Needs Review.
    """
    if image_bgr is None or image_bgr.size == 0:
        return None
    h, w = image_bgr.shape[:2]
    if w < 8 or h < 8:
        return None

    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    # Otsu picks the threshold automatically; INV so the slab
    # (typically brighter than the darker workshop background)
    # comes out as 1. When it doesn't we fall back to the
    # non-inverted mask below.
    _, mask = cv2.threshold(
        blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU,
    )
    # If Otsu leaves us with tiny foreground area, try inverse.
    if int(mask.sum()) < int(0.05 * 255 * mask.size):
        mask = cv2.bitwise_not(mask)

    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(
        mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE,
    )
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    # ``epsilon`` grows with contour perimeter so noisy edges still
    # simplify to a quad. If the first pass doesn't yield 4 vertices
    # we widen the tolerance one step and retry.
    peri = cv2.arcLength(contour, True)
    approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
    if len(approx) != 4:
        approx = cv2.approxPolyDP(contour, 0.05 * peri, True)
    if len(approx) != 4:
        return None

    corners = SlabCorners(*_order_corners_tl_tr_br_bl(approx))
    coverage = _quad_area(corners) / float(w * h)
    if coverage < min_coverage:
        return None
    rectangularity = _rectangularity(corners)
    # Confidence blends rectangularity and coverage; a small tight
    # rectangle still scores well because the pipeline usually
    # crops close to the slab edges before we run.
    confidence = float(0.5 * rectangularity + 0.5 * min(1.0, coverage * 2.5))

    return CornerDetectionResult(
        corners=corners,
        confidence=confidence,
        rectangularity=rectangularity,
        coverage=float(coverage),
    )


def rectify_to_dims(
    image_bgr: np.ndarray,
    corners: SlabCorners,
    target_width_px: int,
    target_height_px: int,
) -> np.ndarray:
    """Perspective-warp the slab quadrilateral into a flat
    ``target_width_px × target_height_px`` rectangle.

    Uses ``cv2.getPerspectiveTransform`` + ``cv2.warpPerspective``.
    Callers pick the pixel dimensions — we recommend the Excel
    dims (mm → px 1:1) so downstream code can address pixels by
    millimetres.
    """
    src = np.array([
        list(corners.top_left),
        list(corners.top_right),
        list(corners.bottom_right),
        list(corners.bottom_left),
    ], dtype=np.float32)
    tw = float(target_width_px)
    th = float(target_height_px)
    dst = np.array([
        [0.0, 0.0],
        [tw - 1.0, 0.0],
        [tw - 1.0, th - 1.0],
        [0.0, th - 1.0],
    ], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(
        image_bgr, matrix,
        (int(round(tw)), int(round(th))),
        flags=cv2.INTER_LINEAR,
    )


def image_aspect_from_corners(corners: SlabCorners) -> float:
    """Effective width/height ratio of the rectified rectangle.

    Uses the average of top+bottom sides for width, and average
    of left+right sides for height. Returns 0.0 when either axis
    collapses.
    """
    def _dist(a, b):
        return float(np.hypot(a[0] - b[0], a[1] - b[1]))
    w = 0.5 * (
        _dist(corners.top_left, corners.top_right)
        + _dist(corners.bottom_left, corners.bottom_right)
    )
    h = 0.5 * (
        _dist(corners.top_left, corners.bottom_left)
        + _dist(corners.top_right, corners.bottom_right)
    )
    if h <= 0:
        return 0.0
    return w / h


def image_aspect_from_bbox(width: float, height: float) -> float:
    if height <= 0:
        return 0.0
    return float(width) / float(height)


def corners_from_bbox(
    x: float, y: float, width: float, height: float,
) -> SlabCorners:
    """Convenience: build canvas-ordered corners from an axis-
    aligned bbox. Used when a green boundary is detected as an
    axis-aligned rectangle rather than a quad."""
    return SlabCorners(
        top_left=(float(x), float(y)),
        top_right=(float(x + width), float(y)),
        bottom_right=(float(x + width), float(y + height)),
        bottom_left=(float(x), float(y + height)),
    )
