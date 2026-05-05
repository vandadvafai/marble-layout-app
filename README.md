# Marble Placement Engine — MVP

A rule-based Python engine that takes a 2D project layout (a floor outline,
optional cutouts) plus a list of available marble slabs, and produces a
first-draft slab placement that a designer can later refine in Blender.

This is the **foundation milestone**. Only one strategy (`balanced`,
row-based) is implemented. The engine is deterministic, validates its own
output, and emits a stable JSON schema that Blender, AI, and front-end
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
4. **Determinism.** Same input + same code → identical output JSON.
5. **Clear separation of concerns.** Geometry / strategy / scoring /
   serialisation each live in their own module so a strategy change can't
   break clipping, and a scoring change can't break the schema.

For everything the MVP **does not** do, see
[LIMITATIONS.md](LIMITATIONS.md).
