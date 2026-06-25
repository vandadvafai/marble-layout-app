# Stonelayout designer — UI shell (foundation milestone)

Read-only canvas that renders one demo layout from the Python
engine. No editing, dragging, or export — those land in subsequent
milestones.

## How frontend talks to backend

```
┌──────────────────────────────┐                 ┌──────────────────────┐
│ Vite dev server  (:5173)     │   GET /api/*    │ FastAPI  (:8000)     │
│                              │ ──────────────▶ │ uvicorn worker       │
│ React app                    │ ◀────────────── │ placement_engine.api │
│  └─ LayoutCanvas (SVG)       │   demo JSON     │  └─ existing engine  │
└──────────────────────────────┘                 └──────────────────────┘
```

The Vite dev server proxies `/api/*` to `http://localhost:8000`
(see `vite.config.ts`), so the frontend uses **relative** URLs in
both dev and production builds. The FastAPI process also enables
CORS for `localhost:5173` and `localhost:3000` as a fallback for
when the proxy is bypassed (curl, separate hosting, etc.).

The backend generates layouts **on demand** from existing
fixtures (`examples/cad_inputs/demo/*.dxf` +
`examples/architectural/*.json` + `outputs/slab_ingestion_test/clean_slabs.json`)
using the same `generate_tile_layout_from_inventory` call that has
shipped since 0.1.24 — no parallel JS implementation, no pre-baked
JSON snapshots.

## Run locally

Two terminals. **Both processes must be running.**

**Terminal 1 — backend** (from the repo root):

```bash
python scripts/run_api_server.py        # http://localhost:8000
```

Equivalent: `uvicorn placement_engine.api.main:app --reload --port 8000`.

Sanity-check the backend before starting the frontend (use the
explicit IPv4 address — `localhost` may resolve to IPv6 ::1, which
won't match uvicorn's default IPv4-only bind):

```bash
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:8000/api/demo-layouts
```

### Troubleshooting: `ECONNREFUSED ::1:8000`

If the Vite proxy logs `connect ECONNREFUSED ::1:8000` even though
the backend looks up, you've hit the IPv4/IPv6 loopback mismatch:
Node 18+ resolves `localhost` to `::1` (IPv6) first, while uvicorn's
default `127.0.0.1` bind is IPv4 only. The Vite config in this repo
already targets `http://127.0.0.1:8000` explicitly to avoid this.
Don't replace it with `localhost` — every Node-side proxy hop in
this codebase must use the explicit IPv4 address.

**Terminal 2 — frontend** (from `frontend/`):

```bash
cd frontend
npm install                              # one-time
npm run dev                              # http://localhost:5173
```

Open `http://localhost:5173` in a browser. The page loads the
`l_shape` demo by default; the picker in the top bar switches
between `l_shape`, `apartment`, and `rectangle`.

## What's rendered

| Layer                  | Source field                              | Style |
|------------------------|-------------------------------------------|-------|
| Floor boundary         | `layout.target.boundary`                  | Black, 8 mm |
| Interior holes         | `layout.target.holes`                     | Light grey fill |
| Pieces                 | `layout.pieces[].polygon`                 | Off-white fill, grey outline |
| Absorbed-sliver holder | `pieces[].notes` includes `absorbed_sliver:*` | Amber fill |
| Spaces                 | `plan.spaces[].polygon`                   | Faint yellow shading |
| Columns                | `plan.columns[].polygon`                  | Steel blue fill |
| Doorway                | `plan.doorways[].segment`                 | Orange band |
| Main entrance          | `plan.doorways[].is_main_entrance=true`   | Red band, thicker |
| Guide line             | `plan.guide_lines[].segment`              | Grey dashed |

Engine coordinates are millimetres with Y growing upward (CAD
convention). The canvas flips Y via an SVG group transform so we
can keep raw mm everywhere in the React tree.

## Interactions

- **Wheel** — zoom around the cursor.
- **Click a seam** — select it (turns blue).
- **Drag a seam** — moves the seam in 50 mm snap increments. Affected
  pieces resize live; on release the backend re-validates.
- **Drag empty canvas** — pan.
- **Reset view** — restore the original framing.
- **Reset edits** (validation panel) — revert to the pristine layout
  fetched from the backend.
- **Validate now** (validation panel) — force re-validation without
  editing.

## Validation panel

After every seam drag the frontend POSTs the edited pieces to
`/api/demo-layouts/{demo_id}/validate`. The backend runs the same
`rules.py` evaluator the engine has used since 0.1.25, and returns:

- a top-level `is_valid` flag (true iff zero hard violations),
- a per-rule list with statuses for R1, R2, R3, R4, R5, R6, R7, R8, R9,
- per-piece classifications (`is_below_min`, `is_absorbed_holder`, …),
- per-seam classifications (`crosses_doorways`, `near_columns`).

The canvas reads these results and re-tints pieces:

- **R1 violator** (sub-100 mm piece) → red fill
- **R2 violator** (seam crossing a doorway) → red seam highlight
- **Absorbed-sliver holder** → amber fill
- **R7 reward piece** (single slab spanning a doorway) — unchanged
  styling for now; tracked in `affected_ids` for the next milestone

## What's intentionally NOT in this milestone

- DXF export
- Multi-piece selection / move
- Adding or removing pieces / seams (only **moving** existing seams)
- Validation while dragging (we validate only on pointer-up to
  keep the rule engine off the 60 fps path)
- Inventory / cut-list panels
- Authentication / multi-project / persistence
- The automatic candidate selector (retired in 0.1.30; preserved
  on the `checkpoint-before-ui-pivot` git branch)

## Layout

```
frontend/
  index.html                  Vite entry HTML
  package.json
  tsconfig.json               TypeScript strict
  tsconfig.node.json
  vite.config.ts              React plugin + /api proxy
  src/
    main.tsx                  React bootstrap
    App.tsx                   App shell + demo picker + edit/validate flow
    styles.css                Layout CSS + validation panel styles
    lib/
      types.ts                Mirrors backend serializers + editor types
      api.ts                  fetch() + POST validate wrappers
      seams.ts                Derive editable seams from pieces
      editing.ts              Drag math: snap, clamp, apply seam move
    components/
      LayoutCanvas.tsx        SVG renderer + pan/zoom + seam editor
      DemoPicker.tsx          <select> for demo IDs
      ValidationPanel.tsx     Sidebar showing rule-by-rule outcomes
```

The shape mirrors the backend (`placement_engine/api/`) so edits
stay symmetric. `lib/seams.ts` and `lib/editing.ts` are intentionally
pure functions — no React, no DOM — so the next milestone (eg.
adding/removing seams) can reuse them without touching the canvas.
