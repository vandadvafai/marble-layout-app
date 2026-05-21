# Architecture

This document is the engineering map of the placement engine. It explains
**what each file does**, **how data flows through the system**, and **why
the boundaries are drawn where they are**.

For the JSON contract see [SCHEMA.md](SCHEMA.md).
For known gaps see [LIMITATIONS.md](LIMITATIONS.md).

---

## High-level data flow

```
        (Optional) standardized DWG prepared by the designer
                              │
                              ▼
              ┌──────────────────────────────────┐
              │  cad_conversion.convert_cad_to_dxf │
              │  • .dxf  → passthrough (unchanged) │
              │  • .dwg  → ODA File Converter      │
              │            → temporary DXF         │
              │  • other → UnsupportedCADFormat    │
              └──────────────────────────────────┘
                              │  (always a DXF from here on)
                              ▼
        (Optional) standardized DXF prepared by the designer
                              │
                              ▼
              ┌──────────────────────────────────┐
              │  cad_to_input.py  /  inspect_cad.py│
              │  /  make_package.py                │
              │  → placement_engine.cad_intake     │
              │  • dxf_reader.read_dxf             │
              │  • entities_on_layer(              │
              │      AI_PROJECT_BOUNDARY,          │
              │      AI_HOLES_CUTOUTS)             │
              │  • geometry_extractor.extract_     │
              │      closed_polylines              │
              │  • input_builder.build_project_    │
              │      input_dict (+ default rules,  │
              │      default design_requirements,  │
              │      optional test slab inventory) │
              │  • writes engine-input JSON        │
              └──────────────────────────────────┘
                              │
                              ▼
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
        if `export_package.py` was used or write_package() called:
              write_package(project_input, output, target_dir)  ← exporters/package.py
              • per layout_option:
                  layout_<strategy>.json (trimmed to one option)
                  layout_<strategy>.dxf   ← exporters/dxf_exporter.py
                  layout_<strategy>_report.md ← exporters/markdown_report.py
                  layout_<strategy>_preview.png (optional)
              • DXF stays clean (geometry + labels only)
              • Markdown report carries warnings, addresses,
                suggested actions, draft-status disclaimers
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
| [export_package.py](export_package.py) | core (CLI) | CAD hand-off CLI. Reads a project input JSON and (optionally) an existing layout output JSON, then calls `write_package` to produce the per-option DXF + Markdown report + JSON + preview bundle. Supports `--strategy` to filter to one option and `--no-preview` to skip the PNG. |
| [cad_to_input.py](cad_to_input.py) | core (CLI) | Standardized-CAD intake CLI. Reads a `.dxf` *or* `.dwg` whose surface lives on `AI_PROJECT_BOUNDARY` (+ optional holes on `AI_HOLES_CUTOUTS`), converts a DWG to DXF if needed, validates the geometry, and writes an engine-input JSON via `build_project_input_dict`. Supports `--include-test-slabs`, `--test-slab-*`, `--strategy …`, `--oda-path`, `--conversion-backend`. |
| [inspect_cad.py](inspect_cad.py) | core (CLI) | CAD inspection CLI. Accepts `.dxf` or `.dwg` (DWG converted first); reports layers, entity counts, boundary area / bbox, hole areas, and conversion provenance as Markdown. Exits non-zero when the inspection found errors so it composes with shell pipelines. |
| [make_package.py](make_package.py) | core (CLI) | One-shot orchestrator: standardized `.dwg`/`.dxf` → conversion → engine input JSON → placement engine → CAD hand-off package. Thin glue over `build_project_input_dict`, `engine.run`, and `write_package`; adds no engine behaviour. |

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
| [`dxf_exporter.py`](placement_engine/exporters/dxf_exporter.py) | helper | `_ensure_layers(doc)` · `_label_text_height(project_input)` · `_piece_centroid(piece)` · `write_dxf(project_input, layout_option, target)` | Writes a clean editable DXF for one layout option using `ezdxf`. Layers: `PROJECT_BOUNDARY`, `HOLES_CUTOUTS`, `SLAB_PIECES`, `OFFCUT_PIECES`, `SEAMS`, `PIECE_LABELS`, `REVIEW_REFERENCE_POINTS`. Pieces become closed `LWPOLYLINE` entities; labels become `TEXT` entities at piece centroids; seams become `LINE` (2-vertex) or `LWPOLYLINE` (multi-vertex). Layout-level review markers (`location=None`) are intentionally **not** rendered — they live in the report. Text height is auto-scaled from the project bbox. Why DXF: Rhino and AutoCAD both ingest DXF cleanly without extra tooling, which is the existing designer workflow. |
| [`markdown_report.py`](placement_engine/exporters/markdown_report.py) | helper | `_suggested_marker_action(...)` · `_suggested_risk_action(...)` · per-section builders · `write_report(project_input, output, option, target)` | Writes the verbose Markdown companion to the DXF. Sections: title, summary, metrics table, pieces table (bbox + centroid), seams table (endpoints), designer review notes (severity + location + related pieces + message + suggested action), per-piece risk flags, notes & limitations (draft-status disclaimers). Markers and flags carry **addresses** so the designer can locate them in Rhino/AutoCAD. |
| [`package.py`](placement_engine/exporters/package.py) | helper | `_slug(name)` · `_trim_output_to_one_option(...)` · `write_package(project_input, output, target_dir, options=…, render_preview=…)` | Orchestrates the per-option hand-off bundle: writes `layout_<strategy>.{json,dxf,md}` plus an optional preview PNG into `target_dir`. The trimmed JSON ensures the file next to each DXF/report contains only that option, so designers can't confuse strategies. |

### `placement_engine/cad_conversion/`

| File | Category | Functions / classes | Role |
|------|----------|---------------------|------|
| [`errors.py`](placement_engine/cad_conversion/errors.py) | **core** | `CADConversionError` · `UnsupportedCADFormatError` · `ODANotFoundError` · `ConversionFailedError` | Exception family for the conversion layer. Messages are written to be actionable for designers (what to do in Rhino/AutoCAD) and developers. |
| [`oda_converter.py`](placement_engine/cad_conversion/oda_converter.py) | **core** | `find_oda_executable(explicit_path)` · `build_oda_command(...)` · `convert_with_oda(dwg, output_dir, oda_path)` · `ODA_MISSING_MESSAGE` | ODA File Converter backend. Locates the executable (explicit path → env var → common locations → PATH), builds the folder-based ODA command, stages the DWG in a temp folder, runs the converter via `subprocess`, and verifies the output DXF appeared (ODA's exit codes are unreliable, so file presence is the success signal). |
| [`converter.py`](placement_engine/cad_conversion/converter.py) | **core** | `ConversionResult` dataclass · `convert_cad_to_dxf(input, output_dir, backend, oda_path)` · `SUPPORTED_EXTENSIONS` | Front door. `.dxf` → passthrough (no disk writes); `.dwg` → ODA backend; anything else → `UnsupportedCADFormatError`. `backend` is `auto` / `oda` / `none` (`none` blocks DWG conversion — handy in tests). Returns a `ConversionResult` recording original path, DXF path, whether conversion happened, and the backend. |

### `placement_engine/cad_intake/`

| File | Category | Functions / classes | Role |
|------|----------|---------------------|------|
| [`dxf_reader.py`](placement_engine/cad_intake/dxf_reader.py) | **core** | `CADIntakeError` · layer-name constants (`LAYER_PROJECT_BOUNDARY`, `LAYER_HOLES_CUTOUTS`, `LAYER_IGNORE`) · `read_dxf(path)` · `entities_on_layer(doc, layer)` · `layer_summary(doc)` · `known_layers()` | Thin wrapper around `ezdxf` that opens a DXF, looks up entities by layer, and surfaces a `CADIntakeError` with a designer-actionable message when the file is missing or malformed. The rest of the intake never imports ezdxf directly. |
| [`geometry_extractor.py`](placement_engine/cad_intake/geometry_extractor.py) | **core** | `extract_closed_polylines(entities, layer_name)` · internal `_is_closed`, `_lwpolyline_to_coords`, `_polyline_to_coords`, `_strip_trailing_duplicate` | Converts `LWPOLYLINE` / `POLYLINE` entities into JSON-style `PolygonCoords`. Raises `CADIntakeError` with conversion hints on the first unsupported entity (`LINE`, `ARC`, `SPLINE`, `HATCH`, `INSERT`, `TEXT`, …) or unclosed polyline encountered. |
| [`input_builder.py`](placement_engine/cad_intake/input_builder.py) | **core** | `build_project_input_dict(cad_path, …)` · `build_project_input(cad_path, …)` · internal `_validate_boundary`, `_validate_holes`, `_extract_boundary_and_holes` | Pulls the boundary and holes from the DXF, validates them with Shapely (single boundary, holes inside boundary, holes don't overlap), and assembles either a raw dict (geometry-only draft) or a fully-validated `ProjectInput` (with the default 6 × 3 200 × 1 800 test inventory attached). |
| [`inspection.py`](placement_engine/cad_intake/inspection.py) | helper | `InspectionReport` dataclass · `inspect_dxf(path)` · `format_report_markdown(report)` | Layers found, entity counts, boundary area + bbox, hole count + areas, warnings, errors. `inspect_dxf` never raises for content reasons — every problem is captured in the report's `errors` list. |

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
| [`tests/test_dxf_exporter.py`](tests/test_dxf_exporter.py) | DXF exporter contract: file written; all 7 expected layers exist; one closed `LWPOLYLINE` per piece on the right layer; one seam entity per detected seam; a TEXT label exists for every `piece_id`; layout-level review markers (`location=None`) do **not** appear in the DXF. |
| [`tests/test_markdown_report.py`](tests/test_markdown_report.py) | Markdown report contract: file written; project id, layout/inventory status, coverage and waste percentages all surface in the body; every section header is present; every piece has a row in the pieces table; layout-level markers carry the "no specific point" address; draft-status disclaimers present. |
| [`tests/test_package_exporter.py`](tests/test_package_exporter.py) | Package orchestrator: one file set per option with the expected naming; preview PNG written when requested; per-option JSON contains only that option (so designers can't confuse strategies); `--strategy` filtering works; raises when no options chosen. |
| [`tests/test_cad_intake.py`](tests/test_cad_intake.py) | Standardized DXF → engine input pipeline. Happy paths (basic rectangle, rectangle with hole, default rules/design requirements, strategy flag), error paths (missing layer, multiple boundaries, hole outside boundary, unclosed polyline, unsupported entity, self-intersecting boundary, missing file), inspection report (areas + bbox + holes + errors-without-raising), end-to-end test that standardized DXF flows through `engine.run` and `write_package` to produce a full hand-off bundle. |
| [`tests/test_cad_conversion.py`](tests/test_cad_conversion.py) | DWG → DXF conversion wrapper. DXF passthrough; unsupported-extension and missing-file errors; DWG-without-converter actionable error; ODA command construction and `convert_with_oda` (subprocess mocked — no real ODA needed); `find_oda_executable` lookup precedence; DXF still works through intake + `inspect_cad_file`. One real end-to-end ODA test, skipped unless `ODA_FILE_CONVERTER_PATH` is set. |
| [`tests/test_debug_plot.py`](tests/test_debug_plot.py) | Parametrised over both example inputs: `render_layout` writes a real PNG (magic bytes + size). |

---

## DWG input — conversion wrapper, not a parser

```
   standardized DWG
        │
        ▼
   placement_engine/cad_conversion/
     convert_cad_to_dxf(input, output_dir, backend, oda_path)
        • .dxf → ConversionResult(was_converted=False, backend="passthrough")
        • .dwg → oda_converter.convert_with_oda(...)
                   → ODA File Converter subprocess → temporary DXF
                   → ConversionResult(was_converted=True, backend="oda")
        │
        ▼
   existing standardized-DXF intake (cad_intake/)  ← unchanged
        │
        ▼
   engine input JSON  →  placement engine  →  CAD package exporter
```

The key principle: **DWG support is a conversion wrapper, not native
DWG parsing.** The engine never interprets DWG bytes. An external tool
(ODA File Converter) produces a DXF, and everything downstream — the
intake parser, the engine, the exporters — runs exactly as it does for
a hand-exported DXF. The `source_file` block in the generated JSON
records the original DWG path and the intermediate DXF path so the
provenance is traceable.

## Standardized-DXF validation workflow

Before a DWG→DXF converter is built, the whole standardized-DXF
pipeline is validated end-to-end by
[`run_dxf_validation_suite.py`](run_dxf_validation_suite.py):

```
   examples/cad_inputs/**/*.dxf
            │
            ▼   for each DXF:
   inspect_dxf  →  cad_inspection.md
            │
            ▼
   build_project_input_dict(test_slab_spec=SlabInventorySpec("auto"))
       • project usable area  →  estimate_slab_count (ceil, ×1.25 buffer)
       • generate_test_slabs(n)         ← placement_engine/utils/test_inventory.py
       • write input_generated.json
            │
            ▼
   ProjectInput.model_validate  →  engine.run
            │
            ▼   per layout option (strategy):
   write_dxf · write_report · render_layout  →  <case>/<strategy>/layout.*
            │
            ▼
   validation_summary.md   (one row per DXF × strategy, pass/fail)
```

Why this stage exists: it confirms that with a *clean* DXF and a
*sufficient* slab inventory the pipeline produces complete layouts —
and it surfaces, honestly, that the row-based `balanced` strategy
cannot reach 100 % on irregular shapes even with surplus material
(whole slabs are wasted on thin/notch rows), whereas `lowest_waste`
can via offcut reuse. That finding is what justifies prioritising
`lowest_waste` and treating `balanced`'s shortfall as a known
structural limitation rather than a bug. DWG conversion is only worth
building once this DXF stage is solid.

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
