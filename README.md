# Avandad — Layout Helper (V1.0.0)

A stone slab placement and factory planning app for designers and
factory teams. Upload a plan, edit the layout, upload your slab
inventory (Excel + photos), assign slabs to pieces, and export a
client PNG and a factory-ready DXF package — all inside a single
four-step wizard.

Backend: Python + FastAPI. Frontend: React + Vite. DXF: ezdxf.

---

## What Avandad does

1. **Reads a floor plan** (a DXF outline or one of the bundled sample
   plans) and generates a tile-based cut layout.
2. **Lets the designer edit** the layout: drag seams, add/remove
   seams, place doorways, columns and guide lines, and validate the
   result against a set of production rules.
3. **Ingests a slab inventory** from an Excel file plus a folder of
   slab photos. The backend matches each photo to a row using a
   filename-suffix convention, then runs every photo through
   **calibration** — perspective-corrects the slab in the photo and
   computes its usable cutting area — producing a clean, validated
   inventory ready for assignment. See [Slab Calibration](#slab-calibration)
   below.
4. **Assigns slabs** to pieces — either automatically (best-fit) or
   by drag-and-drop, with live validation of every assignment.
5. **Exports**:
   * a **client image** (PNG) showing the finished floor with the
     assigned slab photos, and
   * a **factory package** (ZIP) containing an overview DXF plus one
     DXF per physical slab, all in millimetres with real cut
     geometry, ready to open in AutoCAD.

## The 4-step workflow

1. **Upload Plan** — Pick a DXF plan (or one of the sample plans).
   Once loaded, a *Project summary* card shows the current working
   slab size and lets you regenerate the layout against the active
   inventory's median dimensions.
2. **Edit & Validate** — Drag seams (50 mm snap), add doorways,
   columns and guide lines, remove or add seams. The right panel
   surfaces the validation status, a live piece list sorted by risk,
   and a compact slab-inventory preview. Click *Finalize layout &
   continue* to lock the cut plan and move on.
3. **Upload Slabs** — Pick an Excel inventory file (`.xlsx` /
   `.xls`) and the slab photos, then click *Upload & parse*. The
   *Inventory summary* card reports slabs detected, invalid rows,
   photos matched and unmatched. Below it, the **Calibration** card
   groups every slab into Approved / Needs Review / Missing Photo /
   Rejected and lets you approve, reject, replace a photo, or open
   the manual-review modal to adjust corners — see
   [Slab Calibration](#slab-calibration).
4. **Assign & Export** — Assign a slab to every piece:
   * *Auto-assign best slabs* fills every empty piece with its
     lowest-waste candidate,
   * or drag a slab from the sidebar onto a piece,
   * or drag one piece's assigned slab onto another piece to swap,
   * or drag it back to the sidebar to unassign.

   Once every piece is validly assigned, the fixed bottom-right bar
   unlocks **Export Client Image** (PNG) and **Export Factory
   Package** (ZIP).

## Slab Calibration

Every slab photo uploaded in Step 3 is run through calibration
before it's usable — this is what turns an arbitrary photo into a
straightened, correctly-sized image the matcher, fit checker, and
DXF writer can all trust. The rules below are the confirmed V1.0
factory policy (`placement_engine/calibration/policy.py`).

### Source types

| Source type | What it means | Automatic behaviour |
| --- | --- | --- |
| **Existing green boundary** | A bright green rectangle marks the slab in the photo (the factory's own annotation convention). | Approved automatically. |
| **Already scanned / cropped** | The photo is already a clean, cropped shot of just the slab against a plain background. | Approved automatically. |
| **Raw photograph** | An uncropped photo; the system detects the four corners itself. | Approved automatically when detection confidence is high; otherwise flagged **Needs Review**. |
| **No photo** | The Excel row has no linked image. | Blocked as **Missing Photo** until a photo is added. |

### The four calibration statuses

Every slab lands in exactly one of:

* **✅ Approved** — usable by the matcher, fit checker, and DXF
  writer. This is the only status that reaches Step 4.
* **🟡 Needs Review** — the detector didn't reach the auto-approve
  threshold (low confidence, an aspect-ratio mismatch between the
  photo and the Excel dimensions, an irregular/broken slab edge, or
  corner detection failing outright). Not a rejection — open the
  slab, check or drag the four corners, and approve or reject it
  yourself.
* **🔴 Missing Photo** — no image was linked to this Excel row.
  Blocks Step 4 until a photo is added (via *Add photo* on the row,
  or *Replace image* inside the review modal).
* **⚫ Rejected** — the operator (or an extreme aspect-ratio
  mismatch) marked this slab unusable. Excluded from Layout Helper,
  but does **not** block Step 4 — a rejected slab is a resolved
  slab, it's just not one of the usable ones.

**Step 4 gating rule**: Step 4 unlocks only once every slab in the
project is either Approved or Rejected — any remaining Needs Review
or Missing Photo record blocks it. At least one Approved slab must
exist (a project where every slab was rejected has nothing to
assign).

### Physical vs. usable dimensions

The Excel width/height is always the slab's **physical**,
real-world size — that number never changes and is kept purely for
traceability (it's what appears on factory DXF labels). Calibration
deducts **20 mm from every side** (40 mm off each dimension) exactly
once to produce the **usable** size — the area that's actually safe
to cut pieces from. Layout Helper, the fit checker, and the DXF
writer all plan against the *usable* rectangle; nothing downstream
re-applies the deduction.

When more than one piece is cut from the same slab, the factory
plan leaves exactly **5 mm** of spacing between neighbouring cut
contours — the blade clearance is already built into that number,
it isn't added on top.

### Manual review

Opening a Needs Review (or any) slab shows the original photo next
to the calibrated preview. You can:

* **drag the four corner handles** — the preview regenerates from
  the backend shortly after you release a corner (this also
  approves the slab, since adjusting the corners is you vouching
  for them);
* **rotate the view 90°** for photos that were shot sideways;
* **reset corners** back to what the detector originally found;
* **zoom** in for precise placement;
* **approve** the detected corners as-is, **reject** the slab, or
  **replace the image** with a new photo (which re-runs the same
  classifier from scratch — a slab with no photo at all can also
  get its first photo this way).

### Project-scoped persistence & restart recovery

Each upload creates a project directory under
`AVANDAD_DATA_DIR/projects/<uuid>/` holding the original photos, the
calibrated images, the calibration records, and the standardized
inventory. This directory is **durable**, not a temp folder — it
survives a backend restart and a browser refresh:

* On restart, the backend automatically re-opens the most recently
  modified project directory and restores every calibration record
  exactly as it was left, approved slabs included.
* On a browser refresh, the frontend re-fetches the current
  calibration state from the backend rather than caching it locally
  — so what you see always matches what's on disk.
* *Start new project* / *Remove upload* is what actually deletes a
  project directory; a restart or refresh never does.

A project created by an older version of Avandad (before this
calibration module existed) is upgraded automatically the first
time it's read: any pre-existing green-boundary detection is
promoted straight to Approved, so operators don't have to
re-calibrate slabs that were already validated.

## Local setup

### Requirements

* Python **3.11+**
* Node.js **20.5+** (the frontend uses Vite 5)
* macOS, Linux or WSL2

### Backend

```bash
cd marble-placement-engine
python3 -m pip install -r requirements.txt
python3 scripts/run_api_server.py           # FastAPI on :8000
```

### Frontend

```bash
cd marble-placement-engine/frontend
npm install
npm run dev                                 # Vite on :5173
```

Open <http://localhost:5173>. The frontend dev server proxies
`/api/*` to the backend so both must be running.

### Optional configuration

See `.env.example` at the repo root. Nothing is required for local
testing — the app boots against the bundled sample plans and the
sample inventory that ships in `examples/demo/`.

Two optional env vars change the defaults:

| Variable | Effect |
| --- | --- |
| `AVANDAD_DATA_DIR` | Where project directories (uploads, calibration records, processed images) live, under `<AVANDAD_DATA_DIR>/projects/<uuid>/`. Default: `<repo>/data/`. |
| `AVANDAD_INVENTORY_PATH` | Pin the inventory the API reads for real project matching. Setting this bypasses the Step-3 upload flow. Legacy alias `STONELAYOUT_INVENTORY_PATH` still works. |
| `ODA_FILE_CONVERTER_PATH` | Absolute path to the ODA converter executable. Only needed if you plan to import DWG files (DXF works without it). |

## First Run on a New Computer

Nothing in `outputs/` is required to boot the app — all generated
files (including `clean_slabs.json`) are produced automatically
from the user's own uploads at runtime. A clean clone of the repo
will run without any manual bootstrap:

```bash
# 1. Clone
git clone https://github.com/vandadvafai/marble-layout-app.git
cd marble-layout-app/marble-placement-engine

# 2. Install
python3 -m pip install -r requirements.txt
cd frontend && npm install && cd ..

# 3. Start the backend + frontend in two terminals
python3 scripts/run_api_server.py            # :8000
(cd frontend && npm run dev)                 # :5173

# 4. Open http://localhost:5173 and walk the 4 steps
```

On the first visit:

* **Step 1** offers three bundled sample plans (L-shape, apartment,
  rectangle). Pick any to seed the editor.
* **Step 2** lets you edit the layout even though no inventory is
  loaded yet.
* **Step 3** is the first place the app touches real user data. Pick
  your Excel file + slab photos and click *Upload & parse*. The
  backend calibrates every photo (see
  [Slab Calibration](#slab-calibration)) and writes a project
  directory — the standardized inventory, calibration records, and
  processed images — under `AVANDAD_DATA_DIR/projects/<uuid>/`
  (default `<repo>/data/`) — nothing is written back into the repo.
* **Step 4** unlocks once every slab is resolved (Approved or
  Rejected) with at least one Approved.
* Exports land in your browser's default download folder.

### Example Excel structure

Avandad recognises both Persian ERP headers and common English
variants (case-insensitive, extra whitespace tolerated). An English
Excel that Just Works:

| Serial Number | Width (cm) | Height (cm) | Item Code |
| --- | --- | --- | --- |
| VILLA-BEIGE-01 | 160 | 220 | AB-1250 |
| VILLA-BEIGE-02 | 160 | 245 | AB-1250 |

Column requirements:

* An **identity column**: `Serial`, `Serial Number`, `Slab ID` /
  the Persian equivalents `سریال کالا` / `کد کالا`.
* Either **explicit dimension columns** (`Width` + `Height` /
  `Length`, in cm) **or** a **serial that encodes dimensions** (the
  Persian ERP convention: first 3 digits = height cm, next 3 =
  width cm).

Anything else (`Area`, `Slab Number`, `Item Code`, ...) is
optional. The Step-3 upload response lists which columns were
recognised (`mapped_columns`) and which were ignored
(`unmapped_columns`) so it's easy to tell whether the file was
understood.

### Where uploads and exports are stored

* **Uploads** — each `POST /api/inventory/upload` creates a fresh
  project directory under `AVANDAD_DATA_DIR/projects/<uuid>/`
  holding the original Excel + photos, the calibrated images, the
  calibration records (`calibrations.json`), the standardized
  inventory (`clean_slabs.json`), and metadata. Unlike a temp
  folder, this directory is **durable** — it's the source of truth
  the backend re-opens after a restart (see
  [Project-scoped persistence](#slab-calibration)). *Remove upload*
  / *Start new project* is what actually deletes it.
* **Calibration** — the standardized inventory
  (`clean_slabs.json`) contains only Approved slabs; the matcher,
  fit checker, and DXF writer read that file, never the raw upload.
* **Exports** — the client PNG and the factory DXF ZIP are streamed
  back to the browser and land wherever the browser saves
  downloads (usually `~/Downloads`).

## Supported file types

| Where | Formats | Notes |
| --- | --- | --- |
| Plan upload (Step 1) | `.dxf` | DXF import is fully wired for the sample plans. DWG needs the ODA File Converter (`ODA_FILE_CONVERTER_PATH` env var). |
| Slab inventory (Step 3) | `.xlsx`, `.xls` | One slab per row. Width, height and a slab-id column are required; extras (serial, item code, material, finish) surface in the UI when present. |
| Slab photos (Step 3) | `.jpg`, `.jpeg`, `.png` | Matched to inventory rows by filename suffix (last segment of the slab id or serial). |
| Client image export | `.png` | Full-floor render at layout bounds with a white margin and project title. |
| Factory package export | `.zip` | Contains one overview DXF plus one DXF per physical slab. Millimetres, R2013, AutoCAD-compatible. |

## Client image export

* Renders the **entire floor at its true bounds** with a white
  margin and a project title band — independent of any pan/zoom the
  designer applied in the canvas.
* Every assigned slab photo is preloaded and decoded before the
  export starts; any load/decode failure aborts the export and
  names the failing slab so it can be re-uploaded.
* The output is a single high-resolution PNG.
* **Filename**: `<Project>_ClientLayout_YYYY-MM-DD.png`

## Factory DXF package export

* A **ZIP** containing:
  * one overview DXF laying every assigned slab out in a grid,
  * one DXF per physical slab (each shows the true slab boundary
    with the assigned cut piece placed inside),
* Layers follow the standard convention: `SLAB_BOUNDARY`,
  `SLAB_USABLE_AREA`, `CUT_PIECES`, `DIMENSIONS`, `LABELS`.
* Every cut contour is closed, in millimetres, with real polygon
  geometry preserved for edge clips and absorbed slivers.
* Labels include slab id, piece id, cut w × h, rotation and waste.
* **Filenames**:
  * `<Project>_FactoryCutPlan_Overview_YYYY-MM-DD.dxf`
  * `<Project>_Slab_<SlabID>_CutPlan_YYYY-MM-DD.dxf`
  * `<Project>_FactoryPackage_YYYY-MM-DD.zip`

## Manual slab assignment & drag-and-drop

* **From inventory to piece**: drag a slab candidate row from the
  right sidebar onto a piece polygon on the canvas.
* **Piece to piece**: drag one piece onto another to **swap** their
  assigned slabs.
* **Piece to inventory**: drag a piece back to the *"Drop a piece
  here to unassign"* zone above the inventory list.
* **Swap mode toggle**: also available for touch/tablet users.
* Live validation runs after every drop — invalid assignments
  (piece too big for the slab) turn red and block export.

## Current validation behaviour

Validation runs in three layers:

1. **Assignment validity** — every finalised piece must have a
   slab; the same slab must not be assigned to two pieces (unless
   the designer opts in to *allow same slab on multiple pieces*).
2. **Fit check** — the assigned slab must physically fit the
   piece's real cut dimensions (polygon bbox). Pieces whose slab
   is too small are marked red and block export.
3. **Manufacturing fit** — **off by default in V1** because the
   slab dimensions Layout Helper imports are already the *usable*
   size — calibration already deducted the 20 mm/side edge trim
   once (see [Slab Calibration](#slab-calibration)), so the default
   profile's `edge_trim_mm` is `0` to avoid deducting it twice. An
   *Advanced factory settings* toggle exposes three profiles for
   shops that want to opt in to an additional manufacturing
   allowance:
   * *Strict* — kerf + edge trim + tolerance,
   * *Standard* — kerf + tolerance,
   * *Exact* — geometry only (V1 default).
   Plus an *exact-edge action* (allow / warn / block) for pieces
   flush with the slab boundary.

## Known V1 limitations

* Plan upload accepts `.dxf` and `.dwg` (via ODA) only; PDF and
  image plans are not supported.
* Cut-sheet optimisation is out of scope — the factory export
  reflects the designer's final manual assignments, not a
  globally-optimised nesting.
* Vein matching / book matching is not implemented for V1.
* Multi-project workspaces aren't supported; localStorage saves
  one wizard session per demo id.
* Slabs without photos are still assignable; the client image will
  render them with a neutral marble tint.
* Some root-level scripts (`generate_demo_cad_inputs.py`,
  `run_dxf_validation_suite.py`, `streamlit_app.py`) are legacy
  engine-era utilities kept for QA; they aren't part of the V1
  wizard.

## Basic troubleshooting

| Symptom | Fix |
| --- | --- |
| Frontend loads but every request fails with "Backend unavailable" | Backend isn't running or is on a different port than `:8000`. Confirm `python3 scripts/run_api_server.py` printed `Uvicorn running on http://127.0.0.1:8000`. |
| *"No inventory uploaded yet"* on Step 4 | Expected on a fresh install. Complete Step 3 first — the app never uses a bundled `clean_slabs.json` for real projects. |
| `clean_slabs.json` not found error | You do NOT need this file to boot the app. It's generated automatically under `AVANDAD_DATA_DIR/projects/<uuid>/` the moment you upload an Excel + photos in Step 3 — see [Project-scoped persistence](#slab-calibration). |
| *Step 4 blocked* banner | At least one slab is still **Needs Review** or **Missing Photo** — open the Calibration card on Step 3 and resolve every slab (approved or rejected) before continuing. |
| A slab is **missing a photo** | Its Excel row had no image linked at upload time. Use *Add photo* on that row (Step 3's Calibration card) — no need to re-upload the whole project. |
| A slab is stuck in **Needs Review** | Open it (*Review*) and check why: low detector confidence, an aspect-ratio mismatch against the Excel dimensions, an irregular/broken edge, or failed corner detection — the modal's warning banner names the reason. Adjust the corners and release, or click *Approve* / *Reject* directly. |
| **Corner detection failed** on a raw photo | The photo had no clear slab outline (poor contrast, heavy glare, or an unusual background). Open the review modal and place the four corners manually, or use *Replace image* with a cleaner photo. |
| Photo **proportions don't match** the Excel width/height | The calibrated photo's aspect ratio disagrees with the Excel dimensions by more than the auto-approve tolerance. Confirm the Excel row has the correct width/height for that slab, or manually adjust the corners if the photo itself is trustworthy. |
| Project doesn't come back after a **restart** | The backend re-opens the most recently modified directory under `AVANDAD_DATA_DIR/projects/`. Confirm `AVANDAD_DATA_DIR` is set the same way it was before the restart, and that the directory wasn't deleted by *Start new project*. |
| Upload fails with *"Excel file is missing required columns"* | The message lists exactly which columns weren't recognised. Add the requested identity / dimension columns and re-upload. Common English aliases (`Serial`, `Width`, `Height`, `Length`) are already accepted. |
| Photos aren't linking to slab rows | Slab photos are matched by the trailing suffix of the filename (last hyphen-separated segment) against the `Slab Number` / serial suffix. Rename the photos so the suffix matches. |
| Client image export says a slab failed to load | Re-upload the missing slab photo in Step 3. The error banner names the slab id. |
| Factory DXF export blocked | Open the *Blockers* pill in the bottom-right action bar; every reason is listed with a suggested fix. |
| Custom kerf/trim rejects a visually fitting slab | Toggle *Advanced factory settings* off (V1 default) or switch profile to *Exact*. |
| **No generated files** after cloning on a new machine | Expected — nothing under `data/`/`outputs/` is required to boot. Every artefact (calibration records, `clean_slabs.json`, processed images) is created the first time you complete Step 3 on that machine; there is nothing to copy over from another computer. |
| `npm test` fails on Node < 20.5 | Vitest 2 requires Node 20.5+; upgrade Node or use `nvm use 20`. |

## V1 Testing Guide (designers)

Please spend ~30 minutes running the app end-to-end and report
back. The four things we most need to hear about are:

### What to test

1. **Sample plan flow** — Pick a sample plan on Step 1, finalize
   the layout in Step 2, run through Step 3 with the bundled
   inventory (or a real Excel + photo bundle if you have one),
   auto-assign in Step 4, and export both the client PNG and the
   factory ZIP.
2. **Manual swap flow** — On Step 4, drag slabs between pieces,
   from the inventory onto pieces, and back to the inventory. Try
   dropping a small slab on a large piece; confirm the piece turns
   red and export stays blocked.
3. **Repeat exports** — Export the client image three times in a
   row. Every image should contain every slab photo. Report any
   blank tile.
4. **Advanced factory settings** — Toggle it on in Step 4, try
   each of the three profiles (Strict / Standard / Exact) plus
   each exact-edge action (Allow / Warn / Block). Confirm the
   preflight verdicts change accordingly.

### Expected workflow

* Header shows the Avandad mark, the app name **Avandad —
  Layout Helper**, and the version chip **v1.0.0**.
* The 4 steps unlock in order; Step 4 stays locked until every slab
  in Step 3's Calibration card is resolved (Approved or Rejected)
  with at least one Approved.
* The bottom-right *Export* bar shows two buttons; a *Blockers*
  pill appears when export can't run and lists why.

### Feedback to report

* Any layout that shows the wrong piece size or area in the
  right-panel *Piece Details* card.
* Any exported PNG that contains a blank slab.
* Any exported DXF where the cut piece doesn't sit inside its
  slab boundary in AutoCAD.
* Filenames that contain unsafe characters (`/`, spaces,
  Unicode).
* Any UI surface that still shows a raw filesystem path
  (`outputs/…`) outside the *Developer details* disclosure on
  Step 3.
* Anything that says *"Stone Layout"* — the app is now
  **Avandad — Layout Helper**.

### Where exports land

Downloads follow your browser's default rule (usually
`~/Downloads` on macOS / `Downloads/` on Windows). Filenames:

* `<Project>_ClientLayout_YYYY-MM-DD.png`
* `<Project>_FactoryPackage_YYYY-MM-DD.zip`

Unzipping the factory package gives you the overview DXF plus one
DXF per physical slab. All four should open in AutoCAD 2013+.

## Repo layout

```
marble-placement-engine/
├── frontend/                    React app (Vite + TypeScript)
│   ├── src/
│   ├── vitest.config.ts
│   └── package.json
├── placement_engine/            FastAPI backend + engine
│   ├── api/                     HTTP routes, DXF export, fit check
│   ├── calibration/             slab calibration: models, policy,
│   │                            corner detection, storage
│   ├── layout/                  tile layout generator
│   ├── architectural/           rule layer
│   ├── inventory/               slab loader + BLF packing
│   ├── cad_intake/              DXF / DWG readers
│   ├── target_area/             floor-plan geometry
│   └── ...
├── tests/                       pytest suite for the backend
├── scripts/
│   └── run_api_server.py        launches uvicorn on :8000
├── outputs/                     generated artefacts (gitignored)
├── examples/                    sample DXFs + engine inputs
└── requirements.txt
```

## Developer references

- [ARCHITECTURE.md](ARCHITECTURE.md) — module map, data flow, engine
  decisions from the pre-app era.
- [SCHEMA.md](SCHEMA.md) — engine JSON inputs and outputs in plain
  English.
- [LIMITATIONS.md](LIMITATIONS.md) — what the underlying engine does
  not solve yet. Some entries pre-date the app and now describe
  known V1 gaps.

## Running the tests

```bash
# Backend
cd marble-placement-engine
python3 -m pytest tests/ -q

# Frontend (unit — jsdom via Vitest)
cd marble-placement-engine/frontend
npm test

# Frontend (typecheck + production build)
npm run build
```

All three pass on the current baseline (723 backend, 26 vitest).
2 backend tests are environment-gated skips, not failures: one
needs `ODA_FILE_CONVERTER_PATH` + a real `.dwg` fixture, the other
skips when the bundled test photos happen not to match any slab.
