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
   filename-suffix convention and produces a clean, validated
   inventory ready for assignment.
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
   photos matched and unmatched.
4. **Assign & Export** — Assign a slab to every piece:
   * *Auto-assign best slabs* fills every empty piece with its
     lowest-waste candidate,
   * or drag a slab from the sidebar onto a piece,
   * or drag one piece's assigned slab onto another piece to swap,
   * or drag it back to the sidebar to unassign.

   Once every piece is validly assigned, the fixed bottom-right bar
   unlocks **Export Client Image** (PNG) and **Export Factory
   Package** (ZIP).

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
testing — the app boots against the bundled sample plans and demo
inventory.

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
3. **Manufacturing fit** — **off by default in V1** because
   Layout Helper imports slab dimensions that are already
   preprocessed by the factory (safe-crop). An *Advanced factory
   settings* toggle exposes three profiles for shops that want to
   opt in:
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
| Frontend loads but every request 500s | Backend isn't running or `.env` overrides an invalid path. Confirm `python3 scripts/run_api_server.py` printed `Uvicorn running on…`. |
| *Step 4 blocked* banner | Step 3's *Upload & parse* hasn't produced at least one valid slab. Re-check the Excel dimension columns. |
| Client image export says a slab failed to load | Re-upload the missing slab photo in Step 3. The error banner names the slab id. |
| Factory DXF export blocked | Open the *Blockers* pill in the bottom-right action bar; every reason is listed with a suggested fix. |
| Custom kerf/trim rejects a visually fitting slab | Toggle *Advanced factory settings* off (V1 default) or switch profile to *Exact*. |
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
* The 4 steps unlock in order; Step 4 stays locked until Step 3
  produces at least one valid slab.
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

All three pass on the V1 baseline (680 backend, 13 vitest).
