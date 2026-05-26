"""V1 slab inventory intake from the messy ERP Excel export + image folder.

This subpackage is intentionally **separate** from `placement_engine.cad_intake`,
which reads standardized project DXFs. This one is a temporary bridge that
converts an ERP `.xlsx` export plus a sibling image folder into a clean
per-slab CSV / JSON that the placement engine can consume.

It is V1 only — the real company slab database is a separate, later effort
(see README §14 "Real Slab Database Future Work"). Replace this layer when
that database arrives; do not extend it into a permanent data model.

Key convention (V1):
    * `سریال کالا` / **serial_number** carries the dimension-encoded
      string (first 3 digits = height cm, next 3 = width cm) and is the
      image-matching key.
    * `کد کالا` / **item_code** is preserved as metadata only.

Public API:

    ingest_slab_export(excel_path, image_dir, sheet_name=None)
        -> SlabIngestionResult

    write_outputs(result, output_dir)
        -> dict[str, Path]   keys: csv / json / report

Edit `column_map.py` when the ERP export changes header names.
"""

from placement_engine.slab_intake.column_map import (
    COLUMN_MAP,
    REQUIRED_INTERNAL_COLUMNS,
)
from placement_engine.slab_intake.pipeline import (
    SlabIngestionResult,
    SlabRecord,
    build_image_index,
    ingest_slab_export,
    parse_dimensions_from_serial,
    write_outputs,
)

__all__ = [
    "COLUMN_MAP",
    "REQUIRED_INTERNAL_COLUMNS",
    "SlabIngestionResult",
    "SlabRecord",
    "build_image_index",
    "ingest_slab_export",
    "parse_dimensions_from_serial",
    "write_outputs",
]
