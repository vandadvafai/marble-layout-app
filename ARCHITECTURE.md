# Architecture

This document is the engineering map of the placement engine. It explains
**what each file does**, **how data flows through the system**, and **why
the boundaries are drawn where they are**.

For the JSON contract see [SCHEMA.md](SCHEMA.md).
For known gaps see [LIMITATIONS.md](LIMITATIONS.md).

---

## High-level data flow

```
                       run_engine.py  (CLI)
                              │
                              ▼
              ┌──────────────────────────────────┐
              │  engine.load_input_from_file()    │
              │  • read JSON file                 │
              │  • Pydantic validates ProjectInput│
              └──────────────────────────────────┘
                              │
                              ▼
              ┌──────────────────────────────────┐
              │  engine.run(project_input)        │
              └──────────────────────────────────┘
                              │
                              ▼
              build_project_polygon(layout)
              • boundary → Shapely Polygon
              • each hole → Shapely Polygon
              • verify hole ⊂ boundary
              • compose Polygon(boundary, holes=…)
                              │
                              ▼
        for each strategy in options_requested:
                              │
                              ▼
              StrategyContext(project_input, project_polygon)
                              │
                              ▼
              BalancedStrategy.generate(ctx) → StrategyResult ← strategies/row_based.py
              • walk slabs left-to-right, top-to-bottom
              • for each slab, build axis-aligned rectangle
              • clip rectangle to project polygon            ← geometry/clipping.py
              • drop sub-polygons under min size
              • translate clip into slab-local coords
              • build PlacedPiece with project_polygon,
                slab_polygon, texture_transform
              • if zero valid pieces emerge: emit an
                `empty_slab_placement_skipped` ReviewMarker,
                advance the cursor, KEEP the slab in inventory
                              │
                              ▼
              _validate_pieces(project, result.pieces)       ← geometry/validation.py
              • each piece ⊂ project (within tolerance)
              • no two pieces overlap (within tolerance)
                              │
                              ▼
              annotate_pieces_with_risks(pieces, thresholds) ← scoring/risk.py
              • per piece: small / narrow / short /
                thin_aspect_ratio / irregular_piece flags
              build_risk_review_markers(pieces)
              • one `piece_risk` marker per flagged piece
              renumber strategy + risk markers as R001…Rn
                              │
                              ▼
              compute_basic_metrics(project, pieces, slabs)  ← scoring/waste.py
              • installed_area, waste_area, waste_percentage
              • piece_count, slabs_used
              metrics.small_piece_count = count of pieces
                with a `small_piece` flag
                              │
                              ▼
              wrap into LayoutOption (option_id, score, metrics, …)
                              │
                              ▼
              EngineOutput(project_id, engine_version, layout_options)
                              │
                              ▼
              write_output(output, path)                     ← exporters/json_exporter.py
              • model_dump(mode="json") → indent=2 → disk
                              │
                              ▼
        if --plot:
              render_layout(project_input, output, png_path) ← visualization/debug_plot.py
              • matplotlib (lazy-imported, Agg backend)
              • boundary outline, hatched holes, coloured pieces
```

Every arrow above is a single function call. The engine is intentionally
flat: there is no event bus, no DI container, no plugin loader. Strategies
register themselves through one dictionary in [engine.py](placement_engine/engine.py).

---

## File-by-file walk-through

Categories used below:
- **core** — load-bearing engine logic; changes here change behaviour
- **schema** — the contract; changes here are breaking
- **helper** — small utilities supporting core code
- **placeholder** — empty `__init__.py` markers and stubs
- **test** — pytest suite

### Top level

| File | Category | Role |
|------|----------|------|
| [README.md](README.md) | docs | Entry point + quick start. |
| [ARCHITECTURE.md](ARCHITECTURE.md) | docs | This document. |
| [SCHEMA.md](SCHEMA.md) | docs | Field-by-field schema reference. |
| [LIMITATIONS.md](LIMITATIONS.md) | docs | What the MVP does not yet solve. |
| [requirements.txt](requirements.txt) | config | Pinned-floor dependencies (shapely, pydantic, numpy, typer, pytest, matplotlib). |
| [run_engine.py](run_engine.py) | core (CLI) | argparse front end. Calls `engine.load_input_from_file`, `engine.run`, `write_output`, and lazily `render_layout` if `--plot` was passed. No business logic. |

### `placement_engine/`

| File | Category | Functions / classes | Role |
|------|----------|---------------------|------|
| [`__init__.py`](placement_engine/__init__.py) | helper | re-exports `ENGINE_VERSION` | Package marker. |
| [`config.py`](placement_engine/config.py) | helper | `ENGINE_VERSION`, `AREA_EPSILON_MM2`, `LENGTH_EPSILON_MM`, `DEFAULT_OVERLAP_TOLERANCE_MM2` | Single source of truth for the engine version string and the floating-point tolerances Shapely results are compared against. |
| [`models.py`](placement_engine/models.py) | **schema** | `Point`, `PolygonCoords`, `RiskFlagType` (type aliases) · Inputs: `SourceFile`, `Zone`, `Layout`, `ImageMetadata`, `Defect`, `Slab`, `DesignRequirements`, `RiskThresholds`, `Rules`, `ProjectInput` · Outputs: `TextureTransform`, `RiskFlag`, `PlacedPiece`, `Seam`, `ReviewMarker`, `LayoutMetrics`, `Explanation`, `LayoutOption`, `EngineOutput` · `StrategyName` literal | The full JSON contract. Every field reachable from the CLI passes through here. Optional fields (zones, defects, vein direction, image metadata, source file, etc.) are accepted today even though MVP code does not act on them — this keeps future features non-breaking. |
| [`engine.py`](placement_engine/engine.py) | **core** | `STRATEGY_REGISTRY` dict · `load_input_from_file(path)` · `_validate_pieces(project, pieces)` · `_option_for_strategy(name, …)` · `run(project_input)` | Orchestrator. Owns the loop over `options_requested`, builds the project polygon once, dispatches to the registered strategy class, runs validators, calls scoring, and assembles the `EngineOutput`. Knows nothing about specific strategies beyond the registry. |

### `placement_engine/geometry/`

| File | Category | Functions | Role |
|------|----------|-----------|------|
| [`polygons.py`](placement_engine/geometry/polygons.py) | **core** | `coords_to_polygon(boundary, holes=None)` · `polygon_to_coords(geom)` · `rectangle(x, y, w, h)` · `bbox_dimensions(geom)` | The only place JSON ↔ Shapely conversion happens. `polygon_to_coords` rejects `MultiPolygon` so callers must split first. |
| [`clipping.py`](placement_engine/geometry/clipping.py) | **core** | `clip_to_project(slab_rect, project)` · `_flatten_polygons(geom)` · `_split_holes(poly)` | Intersects a slab rectangle with the project polygon and returns a list of **hole-free** sub-polygons. `_flatten_polygons` unwraps `MultiPolygon` / `GeometryCollection` results; `_split_holes` slices polygons that contain interior rings (caused by spanning a project hole) into four bands around the hole's bounding box. |
| [`validation.py`](placement_engine/geometry/validation.py) | **core** | `GeometryValidationError` · `build_project_polygon(layout)` · `assert_pieces_non_overlapping(pieces)` · `assert_pieces_inside(pieces, project)` | Project polygon assembly (boundary + holes, with containment check) and piece-level invariants. O(n²) overlap check is fine at MVP piece counts. |

### `placement_engine/strategies/`

| File | Category | Functions / classes | Role |
|------|----------|---------------------|------|
| [`base.py`](placement_engine/strategies/base.py) | **core** | `StrategyContext` dataclass · `StrategyResult` dataclass · `PlacementStrategy` abstract base | Strategy interface. Every strategy receives the parsed input and the assembled project polygon, and returns a `StrategyResult` (placed pieces + any review markers raised during generation). Scoring and post-validation are kept out of the strategy. |
| [`row_based.py`](placement_engine/strategies/row_based.py) | **core** | `_passes_min_size(piece, rules)` · `_piece_from_clip(project_clip, slab, placed_origin, piece_id)` · `RowBasedStrategy(generate)` · `BalancedStrategy` | The only generator implemented today. Lays slabs left-to-right in horizontal rows, clips each placement against the project polygon, and converts each surviving sub-polygon into a `PlacedPiece`. The cursor and the slab pointer advance independently: a placement that yields zero valid pieces emits an `empty_slab_placement_skipped` ReviewMarker and the slab is retried at the next cursor position. `BalancedStrategy` is a thin alias of `RowBasedStrategy`. |

### `placement_engine/scoring/`

| File | Category | Functions | Role |
|------|----------|-----------|------|
| [`waste.py`](placement_engine/scoring/waste.py) | **core** | `_piece_area(piece)` · `compute_basic_metrics(project, pieces, slabs)` · `project_area(project)` | Area-based metrics: installed area (sum of piece areas), total slab area used (sum of full areas of every slab a piece references), waste area, waste percentage, piece count, slabs used. Seam/complexity fields are still placeholders — see [LIMITATIONS.md](LIMITATIONS.md). |
| [`risk.py`](placement_engine/scoring/risk.py) | **core** | `evaluate_piece(piece, thresholds)` · `annotate_pieces_with_risks(pieces, thresholds)` · `build_risk_review_markers(pieces)` | Soft warning evaluator. Inspects each placed piece against `Rules.risk_thresholds` and attaches `small_piece`, `narrow_piece`, `short_piece`, `thin_aspect_ratio`, and `irregular_piece` flags. Builds one `piece_risk` `ReviewMarker` per flagged piece, located at the piece centroid. Operates after geometry validation, so flagged pieces are still geometrically valid. |

### `placement_engine/exporters/`

| File | Category | Functions | Role |
|------|----------|-----------|------|
| [`json_exporter.py`](placement_engine/exporters/json_exporter.py) | helper | `write_output(output, path)` | Serialises an `EngineOutput` via `model_dump(mode="json")` and writes it pretty-printed to disk. Creates parent directories. |

### `placement_engine/visualization/`

| File | Category | Functions | Role |
|------|----------|-----------|------|
| [`debug_plot.py`](placement_engine/visualization/debug_plot.py) | helper | `_slab_colour(slab_id, slab_order)` · `_polygon_patch(coords, **kw)` · `render_layout(project_input, output, target, option_index=0)` | Headless matplotlib (`Agg` backend) PNG renderer. Boundary as a thick black outline, holes hatched grey, pieces filled with a per-slab colour and labelled with `piece_id` / `slab_id`. Imported lazily from the CLI so matplotlib only loads when `--plot` is used. |

### `placement_engine/utils/`

| File | Category | Functions | Role |
|------|----------|-----------|------|
| [`ids.py`](placement_engine/utils/ids.py) | helper | `IdSequence(prefix, width=3, start=1)` with `.next()` | Deterministic sequential IDs (`P001`, `P002`, …). Used for piece IDs and option IDs so output JSON diffs cleanly across runs. |

### `placement_engine/{exporters,geometry,scoring,strategies,utils,visualization}/__init__.py`

All **placeholder** — empty package markers.

### Examples

| File | Role |
|------|------|
| [`examples/input_floor_simple.json`](examples/input_floor_simple.json) | 6000 × 3600 mm rectangle, four identical 3200 × 1800 slabs. Round-trips with 6.25 % waste. |
| [`examples/input_floor_with_hole.json`](examples/input_floor_with_hole.json) | L-shaped boundary (6000 × 4000 with notch) plus a 600 × 500 mm column cutout. Six slabs in inventory. Exercises hole splitting and irregular shapes. |

### Tests

| File | What it covers |
|------|----------------|
| [`tests/conftest.py`](tests/conftest.py) | Adds the project root to `sys.path` so `import placement_engine` works in any pytest invocation. |
| [`tests/test_input_models.py`](tests/test_input_models.py) | Pydantic schema: minimal valid input, negative slab dimension rejected, duplicate slab IDs rejected, boundary with too few vertices rejected, unsupported rotation rejected, empty slab list rejected. |
| [`tests/test_geometry_validation.py`](tests/test_geometry_validation.py) | `build_project_polygon`: simple rectangle area, hole subtracts area, hole outside boundary rejected, self-intersecting boundary rejected. |
| [`tests/test_clipping.py`](tests/test_clipping.py) | `clip_to_project`: slab inside project, slab overhangs boundary, slab entirely outside, slab spans hole (split correctness). |
| [`tests/test_waste.py`](tests/test_waste.py) | Metrics: full coverage → 0 % waste, partial coverage → expected percentage, slab counted once across multiple pieces. |
| [`tests/test_engine_output.py`](tests/test_engine_output.py) | End-to-end on the simple example: engine returns output, every piece has required fields, every piece references a known slab, pieces are disjoint, waste metrics are internally consistent, JSON round-trips, output is deterministic across runs. |
| [`tests/test_engine_with_hole.py`](tests/test_engine_with_hole.py) | End-to-end on the hole example: engine runs, no piece covers the hole, every emitted polygon is single-ring, pieces are disjoint. |
| [`tests/test_skip_empty_placement.py`](tests/test_skip_empty_placement.py) | Slab-not-silently-consumed behaviour. Synthetic L fixture forces a row-1 skip; the shipped hole example is re-checked to ensure S005 now appears. |
| [`tests/test_risk_flags.py`](tests/test_risk_flags.py) | Risk evaluator + engine wiring. Direct evaluator tests for each flag type; end-to-end tests that risk flags and `piece_risk` review markers appear in the output JSON; sanity test that default thresholds don't fire on the shipped examples. |
| [`tests/test_debug_plot.py`](tests/test_debug_plot.py) | Parametrised over both example inputs: `render_layout` writes a real PNG (magic bytes + size). |

---

## Key design decisions

- **Pydantic over dicts.** Every input and output crosses `model_validate` /
  `model_dump`, so a typo in a JSON key fails immediately with a clear
  message instead of silently producing wrong geometry.
- **Shapely for all 2D geometry.** Avoids hand-rolling polygon clipping.
  Tolerances live in `config.py` so we have one knob to tune when
  floating-point slivers cause trouble.
- **Strategies are pure generators.** They return `list[PlacedPiece]` and
  do no validation, scoring, or serialisation. The engine validates and
  scores afterward. This is what allows the registry-based dispatch in
  `engine.py` to stay small.
- **Hole-free emitted polygons.** The output schema cannot represent a
  polygon with an interior ring, so the clipper splits hole-bearing
  results at the geometry layer rather than punting to consumers.
- **Determinism is a contract, not an accident.** No `uuid`, no clock-based
  IDs, no unseeded RNG. The example tests assert byte-identical re-runs.
- **Lazy matplotlib import.** Importing `matplotlib` costs ~0.5 s and pulls
  in numpy backends. The CLI defers it until `--plot` is actually present.

## Extension points

- **New strategy:** subclass `PlacementStrategy`, implement `generate`,
  add an entry to `STRATEGY_REGISTRY` in [`engine.py`](placement_engine/engine.py).
  No other file needs to change.
- **New metric module:** add a function in `scoring/`, call it from
  `_option_for_strategy` in [`engine.py`](placement_engine/engine.py),
  populate the relevant `LayoutMetrics` field. Schema fields are already
  reserved.
- **Different importer (DXF/SVG/PDF):** add a converter that produces a
  `Layout` and feed it to `engine.run`. The `SourceFile.type` literal
  already lists those values.
- **Blender add-on:** read the output JSON. Each `PlacedPiece` carries a
  `project_polygon` (where to put the mesh) and `slab_polygon` +
  `texture_transform` (how to crop the slab image as the texture).
