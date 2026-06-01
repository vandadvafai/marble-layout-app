# Marble Placement Engine — MVP

A rule-based Python engine that takes a 2D project layout (a floor outline,
optional cutouts) plus a list of available marble slabs, and produces a
first-draft slab placement that a designer can later refine in Blender.

Two strategies ship today:
  * `balanced` — row-based; one piece per slab placement.
  * `lowest_waste` — same row-based main pass, then a second-pass
    offcut filler that reuses leftover material from already-consumed
    slabs to push project coverage higher and slab waste lower.

The engine is deterministic, validates its own output, detects seams
between adjacent pieces (including same-slab cuts), flags risky
pieces, and emits a stable JSON schema that Blender, AI, and front-end
layers will eventually consume.

## Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) — module map, data flow, design decisions
- [SCHEMA.md](SCHEMA.md) — every input and output field in plain English
- [LIMITATIONS.md](LIMITATIONS.md) — what the MVP does **not** solve yet

## Quick start

```bash
cd marble-placement-engine
python3 -m pip install -r requirements.txt

# Run on the bundled examples
python3 run_engine.py -i examples/input_floor_simple.json    -o outputs/layout_simple.json    -p outputs/plot_simple.png
python3 run_engine.py -i examples/input_floor_with_hole.json -o outputs/layout_with_hole.json -p outputs/plot_with_hole.png

python3 -m pytest
```

### Inspect seams from the output JSON

```bash
python3 -c "import json; opt = json.load(open('outputs/layout_simple.json'))['layout_options'][0]; \
  print(f\"{opt['metrics']['seam_count']} seams, {opt['metrics']['total_seam_length']} mm total\"); \
  [print(f\"  {s['seam_id']}  pieces={s['piece_ids']}  len={s['length']:.0f}\") for s in opt['seams']]"
```

## Local Streamlit interface

A local, internal-only Streamlit UI sits on top of the one-command
workflow — upload a standardized DXF, click one button, preview and
download the results. It is **not deployed**, has no authentication,
and is for local testing only.

### Install & run

```bash
python3 -m pip install -r requirements.txt   # installs streamlit
streamlit run streamlit_app.py
```

The app opens in your browser (default `http://localhost:8501`).

### What it does

1. Upload a **standardized DXF** (`.dxf` only — DWG is not part of the
   normal UI flow; export DXF from Rhino/AutoCAD first).
2. Enter a project ID and pick a project type.
3. Click **Generate Layout Package** — both `balanced` and
   `lowest_waste` run automatically with a synthetic test slab
   inventory.
4. Review per-strategy previews, headline metrics, warnings, and the
   full report; download the **PDF designer report** (primary), the
   editable DXF, the layout JSON, the preview, or the complete `.zip`
   package. The Markdown source of the report stays available as a
   secondary technical download.

### Required CAD layers

Same standard as the rest of the tool: exactly one closed polyline on
`AI_PROJECT_BOUNDARY`, optional closed polylines on `AI_HOLES_CUTOUTS`,
millimetre units. Unsupported geometry must be cleaned in
Rhino/AutoCAD first.

The slab inventory is **synthetic test data**, not the real Avandad
slab database. Outputs are AI-generated first drafts for designer
review in Rhino/AutoCAD. Each run is written to
`outputs/ui_runs/latest/` (only the latest run is kept; not
version-controlled).

## One-command package workflow

The recommended MVP workflow is a **single command**. The designer
prepares a standardized DXF (see "Standardized CAD input workflow"
below), then runs `make_package.py` — no need to chain the lower-level
scripts by hand.

### Designer workflow

1. Open the customer DWG in Rhino/AutoCAD.
2. Standardize the geometry:
   - exactly one closed polyline on `AI_PROJECT_BOUNDARY`
   - optional closed polylines on `AI_HOLES_CUTOUTS`
   - millimetre units, closed polylines only
3. Export / save as **DXF**.
4. Run `make_package.py` on the DXF.
5. Open the generated layout DXF in Rhino/AutoCAD; read the report.

### Recommended command

```bash
python3 make_package.py \
    --cad examples/cad_inputs/demo/demo_floor_with_column.dxf \
    --project-id demo_floor_with_column_001 \
    --project-type floor \
    --out outputs/layout_packages/demo_floor_with_column \
    --strategies balanced lowest_waste \
    --include-test-slabs \
    --test-slab-count auto
```

Useful optional flags: `--no-preview` (skip PNGs), `--keep-intermediate`
(also write `internal/full_engine_output.json`), `--clean-output`
(wipe the output folder first), `--test-slab-count 20` (explicit count),
`--oda-path` (only if feeding a `.dwg` — see "DWG input support").

### Output package layout

```
outputs/layout_packages/demo_floor_with_column/
├── cad_inspection.md            what the CAD intake saw
├── generated_engine_input.json  the engine input that was run
├── internal/                    only with --keep-intermediate
│   └── full_engine_output.json
├── balanced/
│   ├── layout.json              engine output (this strategy only)
│   ├── layout.dxf               editable layout geometry for Rhino/AutoCAD
│   ├── layout_report.pdf        designer review report (primary)
│   ├── layout_report.md         Markdown source of the report (technical)
│   └── preview.png              matplotlib preview (unless --no-preview)
└── lowest_waste/
    └── … same five files …
```

The terminal summary prints, per strategy, the `layout_status`,
`inventory_status`, coverage %, waste %, piece count, slabs used, and
seam count, plus the report and DXF paths.

> **DXF is the recommended MVP input.** `make_package.py` also accepts a
> `.dwg` *if* an external converter is configured (see "DWG input
> support"), but for the MVP just export DXF from Rhino/AutoCAD —
> automatic DWG conversion is an optional convenience, not a
> requirement.
>
> The lower-level scripts (`cad_to_input.py`, `run_engine.py`,
> `export_package.py`, `inspect_cad.py`) remain available for
> step-by-step debugging.

## Standardized CAD input workflow

Designers typically receive customer plans as **DWG** files. The
engine does **not** try to parse messy customer DWGs directly.
Instead, the design team **standardizes** the surface to be clad in
Rhino/AutoCAD, exports a clean **DXF**, and feeds that to the intake
tool.

### Designer steps (Rhino / AutoCAD)

1. Open the customer's DWG.
2. Isolate the exact surface to be clad.
3. Place the geometry on the standard layers:
   - `AI_PROJECT_BOUNDARY` — exactly one closed polyline (the outer surface)
   - `AI_HOLES_CUTOUTS` — zero or more closed polylines (columns, drains, ...)
   - `AI_IGNORE` — anything that should be ignored (helper lines, notes)
4. Save as DXF (R2013 or newer).

### Tool steps

```bash
# 1. (Optional) Inspect the DXF before converting.
python3 inspect_cad.py --cad examples/cad_inputs/floor_with_hole_standardized.dxf

# 2. Convert the DXF to engine input JSON.
python3 cad_to_input.py \
    --cad examples/cad_inputs/floor_with_hole_standardized.dxf \
    --out examples/generated/input_floor_with_hole_from_cad.json \
    --project-id cad_floor_with_hole_001 \
    --include-test-slabs \
    --strategy balanced --strategy lowest_waste

# 3. Run the placement engine on the generated JSON.
python3 run_engine.py \
    -i examples/generated/input_floor_with_hole_from_cad.json \
    -o outputs/cad_pipeline.json

# 4. Bundle the result into a CAD hand-off package.
python3 export_package.py \
    -i examples/generated/input_floor_with_hole_from_cad.json \
    -l outputs/cad_pipeline.json \
    -o outputs/layout_packages/cad_floor_with_hole
```

Without `--include-test-slabs`, `cad_to_input.py` writes a
**geometry-only draft** with `"slabs": []`; the designer fills the
inventory in by hand before running the engine.

### Supported CAD entities (MVP)

- `LWPOLYLINE` (closed)
- `POLYLINE` (closed)

Splines, arcs, hatches, blocks, and individual lines are **not**
supported. The intake raises a clear error if it finds them on a
required layer with a hint about how to convert (typically Rhino's
`_Convert` or AutoCAD's `PEDIT`).

## DWG input support

DXF remains the engine's internal parsed format. **DWG input is
supported by automatically converting the DWG to a temporary DXF**,
then running the unchanged DXF intake. The engine never parses DWG
bytes itself — an external converter does that.

Requirements and rules:

- The DWG must still be **standardized** by the designer: exactly one
  closed polyline on `AI_PROJECT_BOUNDARY`, optional closed polylines
  on `AI_HOLES_CUTOUTS`, millimetre scale.
- Automatic DWG conversion needs **ODA File Converter** (a free tool
  from the Open Design Alliance) installed.
- If no converter is available, manual DXF export from Rhino/AutoCAD
  remains the fallback — just pass the `.dxf` to `--cad`.

### Configuring ODA File Converter

The tool locates the converter in this order:

1. `--oda-path /path/to/ODAFileConverter` CLI flag
2. `ODA_FILE_CONVERTER_PATH` environment variable
3. Common install locations (macOS `.app`, Windows `Program Files`)
4. `ODAFileConverter` on `PATH`

If a DWG is given and none of these resolve, the tool fails with an
actionable message explaining how to install/configure ODA or export
DXF manually.

### Example commands

```bash
# DXF input (unchanged)
python3 cad_to_input.py \
    --cad examples/cad_inputs/demo/demo_rectangle_floor.dxf \
    --out examples/generated/input_demo_rectangle.json \
    --project-id demo_rectangle_001 --include-test-slabs

# DWG input — converted internally to DXF first
python3 cad_to_input.py \
    --cad path/to/standardized_project.dwg \
    --out examples/generated/input_from_dwg.json \
    --project-id demo_project_dwg_001 --include-test-slabs \
    --oda-path "/path/to/ODAFileConverter"

# Inspect a DWG (converts, then inspects the resulting DXF)
python3 inspect_cad.py \
    --cad path/to/standardized_project.dwg \
    --out outputs/cad_inspections/project_dwg_report.md \
    --oda-path "/path/to/ODAFileConverter"

# One-shot: DWG/DXF → engine input → layout → hand-off package
python3 make_package.py \
    --cad path/to/standardized_project.dwg \
    --project-id project_001 \
    --out outputs/layout_packages/project_001 \
    --strategies balanced lowest_waste \
    --include-test-slabs --test-slab-count auto \
    --oda-path "/path/to/ODAFileConverter"
```

Converted DXFs are written to `outputs/intermediate_cad/<project-id>/`
and kept for debugging (gitignored, never committed). The generated
JSON's `source_file` records both the original DWG path and the
converted DXF path.

### Generating demo CAD inputs

For testing the standardized intake (and as a reference for designers
preparing real files), the repo ships a generator that writes five
clean synthetic DXFs plus matplotlib PNG previews:

```bash
python3 generate_demo_cad_inputs.py
```

This writes:

- DXFs → `examples/cad_inputs/demo/`
  - `demo_rectangle_floor.dxf` — 6 m × 4 m rectangle
  - `demo_l_shape_floor.dxf` — 8 m × 4 m L-shape with a notch
  - `demo_floor_with_column.dxf` — 7 m × 4.5 m floor with a centre column
  - `demo_irregular_apartment_floor.dxf` — 12 m × 8 m L-shape with two columns and a utility shaft
  - `demo_long_corridor.dxf` — 18 m × 2 m corridor
- PNG previews → `outputs/demo_cad_previews/` (one per DXF)

After generation the script runs `inspect_dxf` on every file and exits
non-zero if any inspection fails. All committed DXFs already pass — they
can be opened directly in Rhino/AutoCAD to verify the layer convention
visually, or fed straight into the intake:

```bash
python3 cad_to_input.py \
    --cad examples/cad_inputs/demo/demo_floor_with_column.dxf \
    --out examples/generated/input_demo_floor_with_column.json \
    --project-id cad_demo_floor_with_column_001 \
    --include-test-slabs
python3 run_engine.py    -i examples/generated/input_demo_floor_with_column.json -o /tmp/_demo.json
python3 export_package.py -i examples/generated/input_demo_floor_with_column.json \
                          -l /tmp/_demo.json -o outputs/layout_packages/demo_floor_with_column
```

The PNGs in `outputs/demo_cad_previews/` are deliberately not
version-controlled (the directory is `.gitignore`d); they're a
convenience artifact. The DXFs are the authoritative test inputs.

## DXF validation suite

Before building a DWG→DXF converter, the whole standardized-DXF
pipeline is validated end-to-end: `DXF → intake → engine input JSON
(+ auto-sized test slabs) → placement engine → DXF/report package →
summary`.

### Auto-sized test slab inventory

The engine needs slabs. For validation the tool can generate a
**synthetic** inventory (not the real company slab database) sized to
the project:

```text
estimated_slab_count = ceil((project_usable_area / slab_area) * buffer_factor)
                       # buffer_factor default 1.25, floored at 1
```

`cad_to_input.py` exposes this via flags:

```bash
python3 cad_to_input.py \
    --cad examples/cad_inputs/demo/demo_irregular_apartment_floor.dxf \
    --out examples/generated/input_apartment.json \
    --project-id cad_apartment_001 \
    --include-test-slabs \
    --test-slab-count auto          # or an explicit integer
    # --test-slab-width / --test-slab-height / --test-slab-thickness
    # --slab-buffer-factor 1.25
```

### Running the suite

```bash
python3 run_dxf_validation_suite.py \
    --cad-dir examples/cad_inputs/demo \
    --out-dir outputs/dxf_validation_runs \
    --strategies balanced lowest_waste
```

For every `*.dxf` in `--cad-dir` the suite writes, under
`outputs/dxf_validation_runs/<case>/`:

```
cad_inspection.md          what the intake saw
input_generated.json       the engine input (with test slabs)
balanced/      layout.json  layout.dxf  layout_report.md  preview.png
lowest_waste/  layout.json  layout.dxf  layout_report.md  preview.png
```

and a top-level `validation_summary.md`.

### Reading `validation_summary.md`

- A **Results** table with one row per (DXF × strategy): coverage %,
  layout/inventory status, slabs generated/used, waste %, pass/fail.
- **Cross-strategy notes** that call out cases where `balanced` fails
  but `lowest_waste` passes (expected — the row-based generator wastes
  whole slabs on thin/notch rows) versus cases where *both* fail
  (a real issue to investigate).
- A **Per-case detail** section with every metric.

A case PASSES when the input validates, the engine runs, the output
files are written, `layout_status == "complete"`,
`inventory_status == "sufficient"`, and `coverage_percentage >= 99.9`.

All validation outputs land under `outputs/` and are **not**
version-controlled.

## CAD / Rhino / AutoCAD hand-off

Designers continue working in Rhino/AutoCAD. For each layout the
engine can produce a hand-off **package** — a folder containing a
clean editable DXF, a verbose Markdown report, the layout JSON, and an
optional preview PNG, one set per requested strategy.

```bash
python3 export_package.py \
    --input examples/input_lowest_waste_corridor_offcut.json \
    --layout outputs/validation_runs/layout_lowest_waste_corridor_offcut.json \
    --out outputs/layout_packages/lowest_waste_corridor
```

If `--layout` is omitted the engine is run on `--input` directly. Use
`--strategy lowest_waste` to export only one strategy from a multi-option
layout, and `--no-preview` to skip the matplotlib PNG.

**What the package contains** (per layout option):

| File | Purpose |
|---|---|
| `layout_<strategy>.dxf` | Clean editable geometry (project boundary, holes, slab pieces, offcut pieces, seams, piece labels). Open in Rhino or AutoCAD. |
| `layout_<strategy>_report.md` | Verbose Markdown report — full metrics table, per-piece table with bounding boxes and centroids, seam table, designer review notes (with addresses and suggested actions), per-piece risk flags, draft-status disclaimers. |
| `layout_<strategy>.json` | Trimmed engine output for this option only — what produced the DXF and report sitting next to it. |
| `layout_<strategy>_preview.png` | Optional matplotlib preview (skipped with `--no-preview`). |

**Design intent.** The DXF stays visually clean — no aggressive red
warning circles, no risk overlays. Subtle reference points appear on
`REVIEW_REFERENCE_POINTS` for piece-level review markers; everything
else lives in the Markdown report. The designer uses the DXF to edit
geometry and the report to address warnings.

**Not in this milestone.** DWG export, final factory cut DXF, PDF
report, web UI, Blender add-on. The DXF is intended as an editable
review draft, not a final factory cutting file.

The `--plot` / `-p` flag is optional; without it no PNG is rendered and
matplotlib is not imported.

## V1 slab inventory ingestion (ERP Excel bridge)

The placement engine consumes slabs as structured records. Until the
real slab database lands, the design team works from the ERP's messy
Excel export plus a separate folder of slab photos. A small bridge
under `placement_engine/slab_intake/` cleans these into a per-slab
CSV/JSON the engine can use.

This is a **V1 temporary adapter**, not a permanent data model — it is
expected to be replaced when the real slab database is integrated (see
[ARCHITECTURE.md](ARCHITECTURE.md) §Slab database future work).

### What it expects

| Input | Notes |
|---|---|
| `--excel path/to/export.xlsx` | The ERP export. Persian column headers are supported via `placement_engine/slab_intake/column_map.py`. |
| `--images path/to/folder/` | Folder of slab photos. Scanned **recursively**. Images are matched by **سریال کالا / serial_number** (e.g. `1731792-4731.jpg`). Supported extensions: `.jpg .jpeg .png .webp .bmp .tif .tiff`. |
| `--sheet name` *(optional)* | Sheet to read. If omitted and the workbook has multiple sheets, the sheet with the most non-empty rows is chosen. |
| `--output dir/` *(optional)* | Output directory. Default: `outputs/slab_ingestion/`. |

### How to run it

```bash
python3 scripts/prepare_slab_data.py \
    --excel  path/to/export.xlsx \
    --images path/to/images_folder \
    --output outputs/slab_ingestion
```

### What it produces

| File | Purpose |
|---|---|
| `clean_slabs.csv` | Flat per-slab CSV (UTF-8 with BOM so Excel renders Persian columns correctly). One row per slab, warnings joined with `;`. |
| `clean_slabs.json` | Same data plus mapping/warning metadata, structured. Easier to consume from Python or the engine. |
| `ingestion_report.txt` | Human-readable summary: rows ingested, mapped/unmapped columns, image match rate, warning counts, per-row warning list. |

### Dimension parsing (from `سریال کالا` / serial_number)

In V1 the **dimension-encoded** field is `سریال کالا` (`serial_number`),
**not** `کد کالا`. `item_code` is preserved as metadata only and is
never used for dimension parsing.

- `serial_number` is treated as a **string** (leading zeros preserved, original digit script kept).
- Persian / Arabic-Indic digits are translated to ASCII before parsing.
- Non-digit characters (`/`, `-`, letters, ...) are skipped — only digits drive the parse.
- The first **3 digits** are read as `height_cm`, the next **3** as `width_cm`.
- Any extra digits (thickness, post-slash codes, batch suffix, ...) are **ignored** for V1.
- Both values are converted to millimetres (`height_mm = height_cm * 10`, etc.).
- The Excel `مساحت (M2)` column is read into `area_m2` for cross-checking.

Examples:

| `serial_number` | `height_cm` | `width_cm` | `height_mm` | `width_mm` |
|---|---|---|---|---|
| `1731792-4731/AV2040643-05` | 173 | 179 | 1730 | 1790 |
| `1202002` | 120 | 200 | 1200 | 2000 |
| `۱۵۰۳۰۰۲` | 150 | 300 | 1500 | 3000 |

### Image matching

- Images are **never embedded in Excel**. They live in a separate folder.
- The folder is indexed recursively by filename **stem** (filename without extension).
- Matching tries the primary slab-number method first; if that fails, the serial-fallback method runs. The method that succeeded is recorded in `image_match_method`.

**Primary — `slab_number_suffix`** (Excel `شماره` ⇄ trailing numeric suffix of the filename):

  - `شماره` (column `slab_number`) is parsed as an integer; leading zeros are tolerated (`"05"` → `5`).
  - Each image stem's trailing digit run is parsed the same way (`5538-6545-27.jpeg` → `27`; `1781722-4731-04.jpg` → `4`).
  - Equal integers match.
  - When several images share a suffix, the matcher prefers one whose stem shares a ≥4-digit run with the serial — otherwise the alphabetically-first stem wins (stable across runs).

**Fallback — `serial_fallback`** (only when slab_number matching produced nothing):

  - Stems derived from `serial_number`, in order:
    1. portion before the first `/` (e.g. `1731792-4731`)
    2. that portion with `/AV<digits>` segment stripped (e.g. `1781722-4731/AV2040643-04` → `1781722-4731-04`)
    3. that portion with `-` / `_` removed
    4. digits-only of that portion
    5. first **7** digits of the full serial (h + w + thickness)
    6. first **6** digits (h + w)
    7. slash-replaced variants (`_` / `-`) of the full serial

- `item_code` is **never** used for image matching (V1).
- No image → the row is kept, `image_match_method` is `not_found`, and `image_not_found` is added to its `warnings`.

### Slab identity

- `slab_id` — per-slab identifier. Equal to `serial_number`. Falls back to `item_code` only when no serial exists.
- `serial_number` — the ERP slab serial (`سریال کالا`), preserved as-written.
- `slab_number` — the per-rack slab index (`شماره`), preserved as-written. Primary image-matching key.
- `item_code` — the ERP product code (`کد کالا`), metadata only.
- `image_id` — the stem actually used to find the photo (whichever method succeeded). For unmatched rows, set to a stable serial-derived label for diagnostics.

### Warning codes

Warnings never block the export — every row always appears in the output. They surface in the report and in the `warnings` column.

| Warning | Meaning |
|---|---|
| `missing_serial_number` | The row had no `سریال کالا` — cannot parse dimensions or run the serial-fallback image match. |
| `invalid_serial_format` | `serial_number` had fewer than 6 digits in its leading chunk. |
| `could_not_parse_dimensions` | Dimensions could not be extracted (paired with `missing_serial_number` or `invalid_serial_format`). |
| `missing_item_code` | The row had no `کد کالا`. Informational — `item_code` is metadata only. |
| `missing_area_m2` | `مساحت (M2)` was blank or non-numeric. |
| `image_not_found` | Neither the slab_number suffix nor the serial fallback found an image. |
| `duplicate_slab_id` | Another row shares this `slab_id` (= `serial_number`) — both rows are flagged. This is the real per-slab integrity check. |
| `suspicious_area_mismatch` | `(height_mm × width_mm) / 1 000 000` differs from the Excel `area_m2` by more than 5%. The row is kept; designers should review. |

Note on `item_code`: multiple slabs in the same product batch legitimately share the ERP `item_code` (product/material code). Shared `item_code` is **not** a warning in V1.

### Editing the column map

The Persian → internal mapping lives in one place:
`placement_engine/slab_intake/column_map.py`. To support a new ERP
header spelling, add another `"persian header": "internal_name"` entry
and re-run the script.

## Repository layout

```
marble-placement-engine/
├── README.md                    ← you are here
├── ARCHITECTURE.md              ← module map + data flow
├── SCHEMA.md                    ← input/output field reference
├── LIMITATIONS.md               ← what we don't solve yet
├── requirements.txt
├── run_engine.py                ← CLI entry point
│
├── scripts/
│   └── prepare_slab_data.py     ← V1 ERP Excel + images → clean CSV/JSON
│
├── examples/
│   ├── input_floor_simple.json     ← rectangular floor
│   └── input_floor_with_hole.json  ← L-shaped floor with column cutout
│
├── outputs/                      ← CLI writes JSON + PNG here
│
├── placement_engine/
│   ├── __init__.py               ← re-exports ENGINE_VERSION
│   ├── config.py                 ← version + tolerance constants
│   ├── models.py                 ← Pydantic input + output schemas
│   ├── engine.py                 ← orchestrator (load → run → return)
│   │
│   ├── geometry/
│   │   ├── polygons.py           ← JSON ↔ Shapely conversion + helpers
│   │   ├── clipping.py           ← slab-rectangle ∩ project polygon
│   │   └── validation.py         ← project polygon assembly + overlap checks
│   │
│   ├── strategies/
│   │   ├── base.py               ← abstract PlacementStrategy + Context
│   │   └── row_based.py          ← RowBasedStrategy + BalancedStrategy
│   │
│   ├── scoring/
│   │   └── waste.py              ← installed_area, waste, piece counts
│   │
│   ├── exporters/
│   │   └── json_exporter.py      ← write EngineOutput to disk
│   │
│   ├── visualization/
│   │   └── debug_plot.py         ← matplotlib PNG (lazy-imported)
│   │
│   ├── utils/
│   │   └── ids.py                ← deterministic ID generator
│   │
│   └── slab_intake/                 ← V1 ERP Excel bridge (temporary)
│       ├── column_map.py            ← Persian → internal column names
│       └── pipeline.py              ← Excel + images → clean CSV/JSON
│
└── tests/                        ← pytest suite (30 tests)
    ├── conftest.py
    ├── test_input_models.py
    ├── test_geometry_validation.py
    ├── test_clipping.py
    ├── test_waste.py
    ├── test_engine_output.py
    ├── test_engine_with_hole.py
    └── test_debug_plot.py
```

## What this MVP guarantees

1. **Schema validation up front.** Bad input fails with a Pydantic
   `ValidationError` before any geometry runs.
2. **Geometric validity.** Every emitted piece lies inside the project
   area, no two pieces overlap (within a 1 mm² floating-point tolerance),
   and no piece covers a project hole.
3. **Hole-free output polygons.** Even when a slab spans a project hole,
   the engine splits the result into single-ring sub-polygons before
   serialising — the JSON schema does not represent polygons-with-holes.
4. **Real seam detection.** The engine identifies every shared boundary
   segment between adjacent pieces, populates `layout_options[i].seams`,
   and reports `metrics.seam_count` and `metrics.total_seam_length`.
   Corner-only contact, gaps, and across-the-hole adjacencies all
   correctly produce no seam.
5. **Honest coverage reporting.** `metrics.coverage_percentage`,
   `uncovered_area`, `layout_status`, and `inventory_status` make it
   impossible for a low-waste-but-poorly-covered layout to look
   successful. When the project isn't fully covered, the engine emits
   `incomplete_coverage` (and, if the inventory is exhausted,
   `insufficient_inventory`) review markers.
6. **Determinism.** Same input + same code → identical output JSON.
7. **Clear separation of concerns.** Geometry / strategy / scoring /
   serialisation each live in their own module so a strategy change can't
   break clipping, and a scoring change can't break the schema.

### Coverage versus waste — they are different metrics

`coverage_percentage` answers "how much of the floor is clad?" — it
divides `installed_area` by the project's usable area.

`waste_percentage` answers "of the slabs we actually consumed, how
much was offcut?" — it divides `waste_area` by `total_slab_area_used`.

These two can disagree dramatically. A layout that uses one slab
perfectly to cover 12 % of a large floor reports `waste_percentage = 0`
*and* `coverage_percentage = 12`, with `layout_status = "partial"` and
`inventory_status = "insufficient"`. Don't read low waste as success
without checking coverage.

### When to use `balanced` vs `lowest_waste`

| | `balanced` | `lowest_waste` |
|---|---|---|
| One piece per slab placement | yes | no — slabs can be split into a main piece + several offcut pieces |
| Coverage on tight inventory | leaves gaps wherever a slab is edge-clipped | reuses leftover slab material to fill gaps; splits one physical slab into multiple installed pieces |
| Slab waste % on the bundled corridor (18 m × 2 m, 6 slabs) | 6.25 % | 0 % (S006's leftover fully reused) |
| Coverage % on the same corridor | 90 % | 96 % (the math limit; total slab area < project area) |
| Seams | one between each adjacent main slab | many more — every offcut strip touches its neighbours |
| Visual / vein matching | not yet implemented in either strategy | secondary; explicitly de-prioritised |

The flagship business case: when slab inventory is tight and material
efficiency matters more than aesthetics, `lowest_waste` consumes
fewer full slabs and reduces uncovered area, at the cost of more
visible seams. Both strategies report `coverage_percentage`,
`layout_status`, and `inventory_status` so the trade-off is explicit.

For everything the MVP **does not** do, see
[LIMITATIONS.md](LIMITATIONS.md).
