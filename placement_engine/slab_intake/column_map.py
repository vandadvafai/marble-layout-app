"""Persian ERP header → internal field name mapping.

Edit this file when the ERP export changes header names. Headers are
matched after stripping whitespace and a few invisible Unicode characters
(ZWJ, ZWNJ, BOM). Multiple Persian spellings can point at the same internal
name — the first match wins.

V1 keeps the mapping minimal and engine-focused. Only the headers needed
for the placement engine's V1 contract are mapped:

    height_cm_excel سریال طول (CM) — explicit height/length cell (cm).
                    **Source of truth** for `height_cm` when present.
    width_cm_excel  عرض (CM) — explicit width cell (cm). Source of
                    truth for `width_cm` when present.
    serial_number   سریال کالا — dimension-encoded fallback. First 3
                    digits of the leading chunk = height_cm, next 3 =
                    width_cm. Becomes `slab_id`. Used to cross-check the
                    explicit Excel dimensions; warns when they disagree.
                    Also the fallback image-matching key.
    slab_number     شماره — per-rack slab index. **Primary image-matching
                    key**: matched against the trailing numeric suffix
                    of the image filename stem (e.g. ``...-27.jpeg`` ⇒
                    slab_number 27).
    item_code       کد کالا — product/item code, kept as metadata only.
                    NOT used for dimension parsing or image matching.
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
    # --- identity (Persian) -------------------------------------------
    "کد کالا": "item_code",
    "سریال کالا": "serial_number",
    "سریال": "serial_number",
    "شماره": "slab_number",
    # --- identity (English aliases so uploads from non-Persian ERPs
    #   don't fail with "no recognised columns"). Matched after
    #   whitespace normalisation, case-insensitive at the loader.
    "item code": "item_code",
    "item_code": "item_code",
    "code": "item_code",
    "serial number": "serial_number",
    "serial_number": "serial_number",
    "serial": "serial_number",
    "slab id": "serial_number",
    "slab_id": "serial_number",
    "id": "serial_number",
    "slab number": "slab_number",
    "slab_number": "slab_number",
    "photo id": "slab_number",
    "number": "slab_number",
    # --- geometry / area (Persian) -----------------------------------
    "طول (CM)": "height_cm_excel",
    "طول": "height_cm_excel",
    "عرض (CM)": "width_cm_excel",
    "عرض": "width_cm_excel",
    "مساحت (M2)": "area_m2",
    "مساحت M2": "area_m2",
    "مساحت": "area_m2",
    # --- geometry / area (English aliases) ---------------------------
    "height": "height_cm_excel",
    "height (cm)": "height_cm_excel",
    "height_cm": "height_cm_excel",
    "length": "height_cm_excel",
    "length (cm)": "height_cm_excel",
    "length_cm": "height_cm_excel",
    "width": "width_cm_excel",
    "width (cm)": "width_cm_excel",
    "width_cm": "width_cm_excel",
    "area": "area_m2",
    "area (m2)": "area_m2",
    "area_m2": "area_m2",
    "area m2": "area_m2",
}

# Internal columns the pipeline actively needs. A missing column does not
# block the export — every row simply ends up with the corresponding
# warning instead (e.g. `missing_serial_number`, `missing_area_m2`).
REQUIRED_INTERNAL_COLUMNS: frozenset[str] = frozenset({"serial_number", "area_m2"})
