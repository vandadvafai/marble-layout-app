"""Persian ERP header → internal field name mapping.

Edit this file when the ERP export changes header names. Headers are
matched after stripping whitespace and a few invisible Unicode characters
(ZWJ, ZWNJ, BOM). Multiple Persian spellings can point at the same internal
name — the first match wins.

V1 keeps the mapping minimal and engine-focused. Only the headers needed
for the placement engine's V1 contract are mapped:

    serial_number   سریال کالا — **dimension-encoded** field; also the
                    primary image-matching key. First 3 digits = height_cm,
                    next 3 = width_cm. Becomes `slab_id`.
    item_code       کد کالا — product/item code, kept as metadata only.
                    NOT used for dimension parsing in V1. Used as a
                    last-resort image fallback.
    area_m2         مساحت (M2) — area in m² as recorded by the ERP, used
                    to cross-check the parsed dimensions.

Business metadata (material_name, status, block_id, batch_id, finish,
order numbers, ...) is intentionally NOT mapped yet. Those headers will
be added when the real slab database integration brings the matching
fields into `SlabRecord`.
"""

from __future__ import annotations

# Persian (ERP header, exactly as exported) → internal name used by the
# pipeline. Add new aliases by repeating the value with another key.
COLUMN_MAP: dict[str, str] = {
    # --- identity -----------------------------------------------------
    "کد کالا": "item_code",
    "سریال کالا": "serial_number",
    "سریال": "serial_number",
    # --- geometry / area ---------------------------------------------
    "مساحت (M2)": "area_m2",
    "مساحت M2": "area_m2",
    "مساحت": "area_m2",
}

# Internal columns the pipeline actively needs. A missing column does not
# block the export — every row simply ends up with the corresponding
# warning instead (e.g. `missing_serial_number`, `missing_area_m2`).
REQUIRED_INTERNAL_COLUMNS: frozenset[str] = frozenset({"serial_number", "area_m2"})
