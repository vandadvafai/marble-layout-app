"""Seam detection — pure-function geometry over PlacedSlabView lists.

A seam exists where two axis-aligned slab rectangles share a line
segment of non-zero length. Point-only contact (touching corners) is
NOT a seam. The output is a list of `SeamView` line segments suitable
for direct rendering and JSON serialization.
"""

from __future__ import annotations

from placement_engine.preview.schema import PlacedSlabView, SeamView

# Edges within this distance (mm) are treated as touching. Slabs in the
# current packers are placed with exact float coordinates, so a 1 mm
# tolerance comfortably covers any rounding artifacts without falsely
# joining slabs that are intentionally separated.
DEFAULT_TOL_MM: float = 1.0

# Minimum overlap length (mm) required to count as a real seam. Below
# this, two rectangles only share a near-point and that's not a
# meaningful join.
MIN_SEAM_LENGTH_MM: float = 1.0


def detect_seams(
    placements: list[PlacedSlabView],
    *,
    tol_mm: float = DEFAULT_TOL_MM,
    min_length_mm: float = MIN_SEAM_LENGTH_MM,
) -> list[SeamView]:
    """Return every shared-edge seam between pairs of placements.

    O(N²) over placements. For V1 inventories (≤ ~50 slabs) this is
    free; if a future strategy produces hundreds of placements we can
    sort by edge coordinates and run a sweep.
    """
    seams: list[SeamView] = []
    n = len(placements)
    for i in range(n):
        a = placements[i]
        for j in range(i + 1, n):
            b = placements[j]
            seam = _shared_edge(a, b, tol_mm, min_length_mm)
            if seam is not None:
                seams.append(seam)
    return seams


def _shared_edge(
    a: PlacedSlabView,
    b: PlacedSlabView,
    tol: float,
    min_length: float,
) -> SeamView | None:
    """Return the line segment along which `a` and `b` share an edge.

    Tries the four possible orientations (a's right = b's left, a's
    left = b's right, a's top = b's bottom, a's bottom = b's top).
    Returns the first match — two axis-aligned rectangles can only
    share an edge in one orientation.
    """
    ax0, ay0, aw, ah = a.x_mm, a.y_mm, a.width_mm, a.height_mm
    bx0, by0, bw, bh = b.x_mm, b.y_mm, b.width_mm, b.height_mm
    ax1, ay1 = ax0 + aw, ay0 + ah
    bx1, by1 = bx0 + bw, by0 + bh

    # Vertical seam: a's right edge meets b's left edge (or symmetric).
    if abs(ax1 - bx0) < tol:
        y0 = max(ay0, by0)
        y1 = min(ay1, by1)
        if y1 - y0 > min_length:
            return SeamView(
                from_slab_id=a.slab_id, to_slab_id=b.slab_id,
                x0_mm=ax1, y0_mm=y0, x1_mm=ax1, y1_mm=y1,
                length_mm=y1 - y0,
            )
    if abs(bx1 - ax0) < tol:
        y0 = max(ay0, by0)
        y1 = min(ay1, by1)
        if y1 - y0 > min_length:
            return SeamView(
                from_slab_id=b.slab_id, to_slab_id=a.slab_id,
                x0_mm=bx1, y0_mm=y0, x1_mm=bx1, y1_mm=y1,
                length_mm=y1 - y0,
            )

    # Horizontal seam: a's top edge meets b's bottom edge (or symmetric).
    if abs(ay1 - by0) < tol:
        x0 = max(ax0, bx0)
        x1 = min(ax1, bx1)
        if x1 - x0 > min_length:
            return SeamView(
                from_slab_id=a.slab_id, to_slab_id=b.slab_id,
                x0_mm=x0, y0_mm=ay1, x1_mm=x1, y1_mm=ay1,
                length_mm=x1 - x0,
            )
    if abs(by1 - ay0) < tol:
        x0 = max(ax0, bx0)
        x1 = min(ax1, bx1)
        if x1 - x0 > min_length:
            return SeamView(
                from_slab_id=b.slab_id, to_slab_id=a.slab_id,
                x0_mm=x0, y0_mm=by1, x1_mm=x1, y1_mm=by1,
                length_mm=x1 - x0,
            )
    return None
