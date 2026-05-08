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
                  or
              LowestWasteStrategy.generate(ctx)               ← strategies/lowest_waste.py

              Both call run_row_based_placement(ctx) for phase 1:
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
              • return (pieces, markers, PlacementRecord[…])

              For lowest_waste, phase 2 runs after phase 1:
              • _build_offcuts(records): slab-local complement of
                each placement's used bbox → OffcutRectangle list
              • _uncovered_components(project, pieces): largest first
              • _fill_uncovered(...): greedy first-fit; cut a corner-
                anchored sub-rectangle from the largest fitting
                offcut, place it (clipped against the project),
                update offcut + uncovered inventory; repeat
              • _renumber_by_slab(...): rewrite piece_id as
                "{slab_id}_{N}", set piece_index_from_slab and
                piece_role for every piece
                              │
                              ▼
              _validate_pieces(project, result.pieces, slabs) ← geometry/validation.py
              • each piece ⊂ project (within tolerance)
              • no two pieces overlap in project space
              • every slab_polygon ⊂ its source slab rectangle
              • no two pieces from the same slab overlap in
                slab-local coordinates
                              │
                              ▼
              detect_seams(pieces, seam_tolerance)            ← scoring/seams.py
              • pairwise boundary intersection
              • LineString / MultiLineString → one Seam each
              • Point / MultiPoint (corner-only) → ignored
              • below tolerance → ignored
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
              • Project-coverage view:
                  project_usable_area, installed_area, uncovered_area,
                  coverage_percentage, layout_status
              • Inventory comparison:
                  slabs_used vs len(slabs) → inventory_status
              • Slab-usage view:
                  total_slab_area_used, waste_area, waste_percentage
              • piece_count, slabs_used
              metrics.small_piece_count = count of pieces
                with a `small_piece` flag
              metrics.seam_count = len(seams)
              metrics.total_seam_length = Σ seam.length
                              │
                              ▼
              Coverage-warning markers
              • layout_status != "complete"  → incomplete_coverage
              • inventory_status = "insufficient" → insufficient_inventory
              (layout-level markers; ReviewMarker.location is null)
                              │
                              ▼
              Merge strategy + risk + coverage markers, renumber R001…Rn
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
| [`validation.py`](placement_engine/geometry/validation.py) | **core** | `GeometryValidationError` · `build_project_polygon(layout)` · `assert_pieces_non_overlapping(pieces)` · `assert_pieces_inside(pieces, project)` · `assert_pieces_within_slab_bounds(pieces, slabs)` · `assert_no_slab_local_overlaps(pieces)` | Project polygon assembly (boundary + holes, with containment check), project-space invariants, and (for lowest_waste) **material-validity** invariants — every `slab_polygon` lies inside its source slab rectangle, and pieces cut from the same slab don't overlap in slab-local coordinates. The engine runs all four assertions on every strategy's output; passing them is what stops a strategy from "inventing" material. |

### `placement_engine/strategies/`

| File | Category | Functions / classes | Role |
|------|----------|---------------------|------|
| [`base.py`](placement_engine/strategies/base.py) | **core** | `StrategyContext` dataclass · `StrategyResult` dataclass · `PlacementStrategy` abstract base | Strategy interface. Every strategy receives the parsed input and the assembled project polygon, and returns a `StrategyResult` (placed pieces + any review markers raised during generation). Scoring and post-validation are kept out of the strategy. |
| [`row_based.py`](placement_engine/strategies/row_based.py) | **core** | `PlacementRecord` · `_passes_min_size(...)` · `_piece_from_clip(...)` · `run_row_based_placement(ctx)` · `RowBasedStrategy(generate)` · `BalancedStrategy` | The shared row-based geometric loop. `run_row_based_placement` is called by both `balanced` and `lowest_waste`; it returns the placed pieces, any skip markers, and one `PlacementRecord` per successful slab placement (slab, project origin, placed size, pieces). `BalancedStrategy` is a thin wrapper that returns the loop's pieces directly. The cursor and slab pointer advance independently: a placement that yields zero valid pieces emits an `empty_slab_placement_skipped` ReviewMarker and the slab is retried at the next cursor position. |
| [`lowest_waste.py`](placement_engine/strategies/lowest_waste.py) | **core** | `OffcutRectangle` · `_build_offcuts(records)` · `_uncovered_components(...)` · `_best_offcut(...)` · `_shrink_offcut(...)` · `_make_offcut_piece(...)` · `_fill_uncovered(...)` · `_renumber_by_slab(...)` · `LowestWasteStrategy` | Two-phase placement. Phase 1 delegates to `run_row_based_placement` and tags every piece `piece_role="main"`. Phase 2 builds an `OffcutRectangle` inventory from the slab-local complement of each `PlacementRecord`, scans uncovered project components largest-first, and greedily places corner-anchored sub-rectangles of the best fitting offcut until the project is covered or no offcut remains. Pieces are renumbered as `{slab_id}_{N}` and tagged `piece_role="main"` or `piece_role="offcut"` accordingly. Restricted to axis-aligned rectangular offcuts and bbox-rectangular gaps; non-rectangular gap remainders stay uncovered. |

### `placement_engine/scoring/`

| File | Category | Functions | Role |
|------|----------|-----------|------|
| [`waste.py`](placement_engine/scoring/waste.py) | **core** | `_piece_area(piece)` · `project_area(project)` · `_layout_status(...)` · `_inventory_status(...)` · `compute_basic_metrics(project, pieces, slabs)` | Two complementary metric views: **project-coverage** (`project_usable_area`, `installed_area`, `uncovered_area`, `coverage_percentage`, `layout_status`, `inventory_status`) and **slab-usage** (`total_slab_area_used`, `waste_area`, `waste_percentage`). The status fields make it impossible for a low-waste-but-poorly-covered layout to look successful. Cutting-complexity / production-difficulty are still hardcoded placeholders — see [LIMITATIONS.md](LIMITATIONS.md). |
| [`seams.py`](placement_engine/scoring/seams.py) | **core** | `_extract_linestrings(geom)` · `_line_to_coords(line)` · `detect_seams(pieces, tolerance)` · `total_seam_length(seams)` | Seam detector. For every pair of placed pieces, intersects their `boundary` LineStrings; flattens the result through `LineString` / `MultiLineString` / `GeometryCollection`; drops `Point`/`MultiPoint` corner contacts and segments below `Rules.seam_tolerance`. Each surviving line becomes one `Seam` with deterministic `SM###` IDs. |
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
| [`tests/test_seams.py`](tests/test_seams.py) | Seam detector + engine wiring. Direct unit tests for the vertical-edge / horizontal-edge / corner-only / gap / across-the-hole / sub-tolerance / MultiLineString / Point cases; end-to-end checks that the simple example produces exactly 4 seams totalling 9600 mm and that seam metrics always match the `seams` list. |
| [`tests/test_coverage_metrics.py`](tests/test_coverage_metrics.py) | Project-coverage view. Bundled examples report `complete`/`sufficient`; insufficient-inventory and corridor fixtures report `partial`/`insufficient` with `incomplete_coverage` and `insufficient_inventory` review markers; flagship business case (zero slab waste with low coverage stays `partial`); schema includes both new and legacy metric fields; empty-pieces case yields `failed`/`unknown`. |
| [`tests/test_lowest_waste.py`](tests/test_lowest_waste.py) | `lowest_waste` strategy. End-to-end checks that the corridor improves from 90 % (balanced) to 96 % (lowest_waste) with 0 % slab waste; that S006 contributes both a main piece and multiple offcut pieces; that same-slab pieces share `slab_id`/`source_slab_id`; that piece IDs follow `{slab_id}_{N}` with contiguous indices; that no slab_polygon escapes the source slab and no two same-slab pieces overlap in slab-local coords; that lowest_waste reaches 100 % on a fixture where math allows; that insufficient inventory remains honestly reported; that balanced output is unchanged. |
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
