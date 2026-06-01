"""Typed slab inventory models for the placement engine (V1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from placement_engine.models import Slab as PydanticSlab

# Default thickness assumed when constructing a `placement_engine.models.Slab`
# from an `InventorySlab`. V1 ingestion intentionally drops thickness from
# clean_slabs.json, but the engine's Pydantic `Slab` requires a positive
# value. 20 mm is the standard slab thickness for marble in the workflow
# the engine was built around. Override per call via
# `to_engine_slab(default_thickness_mm=…)` when needed.
DEFAULT_THICKNESS_MM: float = 20.0


@dataclass
class InventorySlab:
    """A single slab as the engine sees it (V1).

    Built from one record in ``clean_slabs.json``. Carries enough
    metadata to (a) place the slab geometrically, (b) link back to its
    real photo when one is on disk, and (c) trace any warnings the
    ingestion pipeline raised on the source row.
    """

    # Identity
    slab_id: str
    serial_number: str | None
    slab_number: str | None
    item_code: str | None
    # Geometry — millimetres, matching the engine's coordinate system.
    width_mm: float
    height_mm: float
    # Areas in m² as recorded by ingestion. `area_m2` is what the ERP
    # wrote; `calculated_area_m2` is `width_mm × height_mm / 1e6`.
    area_m2: float | None
    calculated_area_m2: float | None
    # Image link. `image_path` is the resolved on-disk path of the
    # ORIGINAL slab photo when it was found at load time;
    # `image_available` reflects whether that file actually exists.
    # `image_placeholder_reason` is human-readable text used in previews
    # when no usable image is available.
    image_path: Path | None
    image_available: bool
    image_placeholder_reason: str | None
    # Source traceability + ingestion warnings (verbatim).
    source_excel_row: int | None
    ingestion_warnings: list[str] = field(default_factory=list)
    # Processed (green-box cropped) image, when the image_intake layer
    # has produced one for this slab. Populated by
    # `attach_processed_images()`. None when no crop is available;
    # callers should fall back to `image_path` in that case.
    processed_image_path: Path | None = None

    def to_engine_slab(
        self,
        *,
        default_thickness_mm: float = DEFAULT_THICKNESS_MM,
    ) -> PydanticSlab:
        """Adapt to the engine's strategy-facing Pydantic `Slab`.

        The engine's `Slab` requires a positive `thickness`; V1 ingestion
        does not export thickness, so a default is applied. `image_path`
        is propagated as a string only when the photo is actually
        available on disk — never as a dangling link.
        """
        return PydanticSlab(
            slab_id=self.slab_id,
            width=float(self.width_mm),
            height=float(self.height_mm),
            thickness=float(default_thickness_mm),
            image_path=(
                str(self.image_path) if self.image_available and self.image_path else None
            ),
        )


@dataclass
class Inventory:
    """Outcome of a single ``clean_slabs.json`` load.

    `slabs` are the usable records — every one has positive width and
    height. `skipped_records` keeps the raw dicts of rows the loader
    dropped (e.g. missing dimensions) so callers can report them
    without re-parsing the file.
    """

    slabs: list[InventorySlab]
    source_json: Path
    skipped_records: list[dict] = field(default_factory=list)

    def __len__(self) -> int:  # convenience: `len(inv)`
        return len(self.slabs)

    def __iter__(self):  # convenience: `for s in inv:`
        return iter(self.slabs)
