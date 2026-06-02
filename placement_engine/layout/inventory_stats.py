"""Inventory dimension statistics → layout tile sizes.

The layout generator's default tile size is the **median** slab
width × height of the supplied inventory. Median is the right choice
here because a few unusually large or small slabs in the ERP export
shouldn't drag the nominal tile away from the design batch — and
because real Avandad inventories are tight clusters around a few
standard sizes anyway, so median ≈ mode in practice.

This module is intentionally inventory-shape-agnostic: it takes any
iterable of objects exposing ``width_mm`` and ``height_mm`` attributes.
That keeps it usable from the CLI (which loads InventorySlab via
``load_inventory``) and from tests (which can pass anonymous dataclasses
or namedtuples).
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Iterable, Protocol


class HasDimensions(Protocol):
    """Anything with mm-scale width + height attributes."""

    width_mm: float
    height_mm: float


@dataclass
class InventoryDimensionSummary:
    """Aggregate dimensional view of an inventory.

    Median is the canonical "representative tile" choice; mean is
    reported for sanity (large gap between median and mean ⇒ inventory
    is bimodal or has outliers). Mode is reported only when at least
    one value repeats — otherwise it's left ``None`` to avoid pretending
    arbitrary first-seen values are "the mode."
    """

    slab_count: int
    median_width_mm: float
    median_height_mm: float
    mean_width_mm: float
    mean_height_mm: float
    min_width_mm: float
    max_width_mm: float
    min_height_mm: float
    max_height_mm: float
    mode_width_mm: float | None
    mode_height_mm: float | None
    # Counts how many slabs share the modal value, when a mode exists.
    mode_width_count: int | None
    mode_height_count: int | None

    def to_dict(self) -> dict:
        return asdict(self)


def compute_inventory_dimension_summary(
    slabs: Iterable[HasDimensions],
) -> InventoryDimensionSummary:
    """Reduce a slab inventory to a single `InventoryDimensionSummary`.

    Raises ``ValueError`` if no slabs are supplied; an empty inventory
    can't anchor a layout tile size and the caller needs to know.
    """
    widths: list[float] = []
    heights: list[float] = []
    for s in slabs:
        w = float(s.width_mm)
        h = float(s.height_mm)
        if w <= 0 or h <= 0:
            continue  # ignore degenerate rows; loader already skips most
        widths.append(w)
        heights.append(h)
    if not widths:
        raise ValueError(
            "inventory has no slabs with positive dimensions; "
            "cannot derive a layout tile size"
        )

    mode_w, mode_w_count = _safe_mode(widths)
    mode_h, mode_h_count = _safe_mode(heights)
    return InventoryDimensionSummary(
        slab_count=len(widths),
        median_width_mm=float(statistics.median(widths)),
        median_height_mm=float(statistics.median(heights)),
        mean_width_mm=float(statistics.mean(widths)),
        mean_height_mm=float(statistics.mean(heights)),
        min_width_mm=min(widths),
        max_width_mm=max(widths),
        min_height_mm=min(heights),
        max_height_mm=max(heights),
        mode_width_mm=mode_w,
        mode_height_mm=mode_h,
        mode_width_count=mode_w_count,
        mode_height_count=mode_h_count,
    )


def _safe_mode(values: list[float]) -> tuple[float | None, int | None]:
    """Most frequent value if at least one value repeats; else ``(None, None)``.

    Returns ``(mode_value, count)``. Ties are broken by sort order
    (Counter.most_common is stable) — fine for the V1 use case where
    we just want a representative tile size.
    """
    if not values:
        return None, None
    counts = Counter(values)
    value, count = counts.most_common(1)[0]
    if count <= 1:
        return None, None
    return float(value), int(count)
