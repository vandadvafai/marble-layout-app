"""Slab inventory consumption for the placement engine.

This layer is the **engine-side** counterpart of ``slab_intake/``.

    slab_intake/   : Excel + images  ->  clean_slabs.json
    inventory/     : clean_slabs.json -> typed slab inventory the engine uses

The engine NEVER reads the raw ERP Excel directly. The only inventory
contract this module exposes is `load_inventory(clean_slabs_json)`, which
returns an `Inventory` of strongly-typed `InventorySlab` records. Each
`InventorySlab` provides a `to_engine_slab()` adapter that produces the
existing `placement_engine.models.Slab` Pydantic instance strategies and
exporters already consume.

Public API:

    InventorySlab           dataclass — V1-rich per-slab record
    Inventory               dataclass — load result + skipped records
    InventoryIssue          dataclass — validation finding
    load_inventory(path)    -> Inventory
    validate_inventory(inv) -> list[InventoryIssue]

The smoke-only shelf packer lives in :mod:`placement_engine.inventory.shelf_pack`
and is intentionally separate from real placement strategies.
"""

from placement_engine.inventory.loader import load_inventory
from placement_engine.inventory.model import Inventory, InventorySlab
from placement_engine.inventory.processed_images import (
    attach_processed_images,
    load_processed_image_metadata,
)
from placement_engine.inventory.validation import (
    InventoryIssue,
    validate_inventory,
)

__all__ = [
    "Inventory",
    "InventoryIssue",
    "InventorySlab",
    "attach_processed_images",
    "load_inventory",
    "load_processed_image_metadata",
    "validate_inventory",
]
