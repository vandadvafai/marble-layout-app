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

The `--plot` / `-p` flag is optional; without it no PNG is rendered and
matplotlib is not imported.

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
│   └── utils/
│       └── ids.py                ← deterministic ID generator
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
