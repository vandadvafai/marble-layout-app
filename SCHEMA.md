# JSON Schema Reference

Plain-English description of every meaningful field in the engine's input
and output. Field names match the JSON exactly. All distances are
**millimetres**; all coordinates are 2D `[x, y]` pairs.

The full source of truth is [`placement_engine/models.py`](placement_engine/models.py).

---

## Conventions

- A **polygon** is a list of `[x, y]` points. The ring is implicitly
  closed (do not repeat the first point at the end).
- A polygon must have **at least 3 points**.
- The coordinate system is the project's own: positive X to the right,
  positive Y up, origin wherever the floor plan was originally anchored.
- Optional fields can be omitted from the JSON entirely; the engine
  applies sensible defaults.

---

## Input — the top level

```jsonc
{
  "project_id":          "marble_floor_simple_001",
  "project_type":        "floor",
  "units":               "mm",
  "layout":              { ... },
  "slabs":               [ ... ],
  "design_requirements": { ... },
  "rules":               { ... },
  "options_requested":   ["balanced"],
  "random_seed":         42
}
```

| Field | Required | Default | Meaning |
|-------|----------|---------|---------|
| `project_id` | yes | — | Free-form identifier copied through to the output. Use whatever scheme the design team prefers; the engine never parses it. |
| `project_type` | no | `"floor"` | Free-form label for what's being clad (floor, wall, countertop, fireplace…). The MVP treats every project as a flat 2D surface; this field exists for the future. |
| `units` | no | `"mm"` | Currently the only accepted value. Every distance in the schema is in this unit. |
| `layout` | yes | — | The project geometry (boundary, holes, optional zones). See below. |
| `slabs` | yes | — | The available slab inventory. Must contain at least one slab; `slab_id` values must be unique. |
| `design_requirements` | no | sensible defaults | Designer intent. Most fields are advisory in MVP. |
| `rules` | no | sensible defaults | Hard constraints (rotations, minimum piece sizes, tolerances). |
| `options_requested` | no | `["balanced"]` | Which strategies to run. Today every value collapses to the balanced row-based generator; the schema reserves the other names. |
| `random_seed` | no | `42` | Reserved for the future natural-random strategy. |

The top-level object accepts extra fields (`extra="allow"`), so callers can
attach metadata the engine will simply round-trip.

---

## Input — `layout`

Describes the surface to be tiled.

```jsonc
"layout": {
  "source_file": { ... },             // optional
  "boundary":    [[0,0],[6000,0],[6000,4000], ...],
  "holes":       [ [[1200,900],[1800,900], ...] ],
  "zones":       [ ... ]              // optional
}
```

| Field | Meaning |
|-------|---------|
| `source_file` | Optional pointer to whatever file the layout was originally extracted from (manual JSON, DXF, SVG, PDF, image, CAD export). The engine does not open it; it exists so future importers can attribute their work. |
| `boundary` | The outer outline of the surface. A single polygon, ≥ 3 points. Self-intersecting polygons are rejected. |
| `holes` | List of polygons to subtract from the boundary (columns, drains, openings). Each hole must lie **fully inside** the boundary or input is rejected. Holes may not currently overlap each other. |
| `zones` | Optional sub-regions of the project (e.g. "entrance", "high-visibility area"). MVP stores them but does not yet use them. |

### `source_file`

| Field | Meaning |
|-------|---------|
| `type` | One of `manual_json`, `dxf`, `svg`, `pdf`, `image`, `cad_export`. |
| `path` | Optional path/URI to the source file. |
| `notes` | Free-form designer note. |

### `zones[i]`

| Field | Meaning |
|-------|---------|
| `zone_id` | Caller-defined identifier. |
| `name` | Optional human label. |
| `polygon` | Polygon describing the zone. |
| `visibility` | `low`, `medium`, or `high`. Will steer scoring once seam/risk modules land. |
| `notes` | Free-form. |

---

## Input — `slabs[i]`

```jsonc
{
  "slab_id":        "S001",
  "width":          3200,
  "height":         1800,
  "thickness":      20,
  "image_path":     "images/slab_S001.png",
  "image_metadata": { ... },        // optional
  "vein_direction": "horizontal",   // optional
  "design_notes":   "...",          // optional
  "defects":        [ ... ]         // optional
}
```

| Field | Meaning |
|-------|---------|
| `slab_id` | Unique identifier for this physical slab. Every emitted piece references it. |
| `width` / `height` | Slab dimensions in mm (must be > 0). The MVP treats slab orientation as `width × height` with rotation 0. |
| `thickness` | Stored only; not used for layout. |
| `image_path` | Optional path to the slab's photo. Stored on each piece's `texture_transform` so Blender can map the image. The engine does **not** open the file. |
| `image_metadata` | Optional `original_filename`, `dpi`, `notes`. Reserved for future image analysis. |
| `vein_direction` | One of `horizontal`, `vertical`, `diagonal`, `none`. Reserved for the future `best_visual` strategy. |
| `design_notes` | Free-form. |
| `defects` | List of polygons (in slab-local coordinates) marking defective regions. Reserved for future risk flagging. Each defect has `defect_id`, `type`, `polygon`, `severity`, optional `notes`. |

---

## Input — `design_requirements`

Designer intent. Free-form: extra fields are accepted and round-tripped.

| Field | Default | Meaning |
|-------|---------|---------|
| `general_notes` | — | Free-form. |
| `preferred_visual_style` | — | Free-form (e.g. `"natural"`, `"book-matched"`). |
| `preferred_vein_direction` | — | Free-form (e.g. `"align_with_longest_project_axis"`). |
| `priority` | `"balanced"` | One of `balanced`, `lowest_waste`, `best_visual`, `pattern_match`. Used by the engine to mark the matching `LayoutOption` as `recommended: true`. |
| `avoid_high_visibility_seams` | `false` | Reserved for future seam scoring. |
| `avoid_defects` | `true` | Reserved for future risk scoring. |

---

## Input — `rules`

Hard constraints the engine enforces.

| Field | Default | Meaning |
|-------|---------|---------|
| `allowed_rotations` | `[0, 90]` | Permitted slab rotations in degrees. **MVP currently uses only 0**; non-zero values are accepted but ignored. Only 0/90/180/270 are valid. |
| `min_piece_width` | `0` | **Hard-drop filter.** Bounding-box width (mm) below this discards the piece entirely from the layout. Set to 0 to disable. |
| `min_piece_height` | `0` | Hard-drop filter, height. |
| `min_piece_area` | `0` | Hard-drop filter, area (mm²). |
| `seam_tolerance` | `2` | Reserved for future seam detection. |
| `allow_partial_slab_use` | `true` | Reserved. MVP behaviour is "yes, partial use is allowed." |
| `allow_piece_reuse_from_offcuts` | `false` | Reserved for future offcut tracking. |
| `max_waste_percentage_target` | `25` | Advisory target. Not enforced in MVP — the engine will still emit a layout above the target. |
| `risk_thresholds` | see below | **Soft warning thresholds.** Independent of `min_piece_*`. Pieces below these stay in the layout but receive `risk_flags` and `piece_risk` review markers so a designer can review them. |

### `rules.risk_thresholds`

These are independent of the hard-drop filter above. A piece passes the
hard filter first; then risk thresholds decide whether to attach a
warning. Set `min_piece_*` to `0` to see warnings for *every* uncomfortable
piece without dropping any of them.

| Field | Default | Meaning |
|-------|---------|---------|
| `min_piece_width` | `150` | Bounding-box width below this triggers a `narrow_piece` flag. |
| `min_piece_height` | `150` | Bounding-box height below this triggers a `short_piece` flag. |
| `min_piece_area` | `50000` | Area (mm²) below this triggers a `small_piece` flag and increments `metrics.small_piece_count`. |
| `max_aspect_ratio` | `8.0` | When `max(w/h, h/w)` exceeds this, the piece is flagged `thin_aspect_ratio`. |
| `max_vertex_count` | `6` | Pieces with more exterior vertices than this are flagged `irregular_piece`. A clean rectangle has 4; an L-shape has 6. |

---

## Output — the top level

```jsonc
{
  "project_id":     "marble_floor_simple_001",
  "engine_version": "0.1.0",
  "units":          "mm",
  "generated_at":   "2026-05-04T14:50:00Z",
  "layout_options": [ { ... } ]
}
```

| Field | Meaning |
|-------|---------|
| `project_id` | Echoed from input. |
| `engine_version` | The engine version that produced this file. Lives in [`config.py`](placement_engine/config.py). |
| `units` | Always `"mm"`. |
| `generated_at` | ISO-8601 UTC timestamp. |
| `layout_options` | One entry per requested strategy. MVP currently always returns one. |

---

## Output — `layout_options[i]`

```jsonc
{
  "option_id":      "OPT_001",
  "option_name":    "Balanced layout",
  "strategy":       "balanced",
  "recommended":    true,
  "score":          0.0,
  "metrics":        { ... },
  "placed_pieces":  [ ... ],
  "seams":          [],
  "review_markers": [],
  "explanation":    { ... }
}
```

| Field | Meaning |
|-------|---------|
| `option_id` | Sequential ID (`OPT_001`, `OPT_002`, …). Stable across runs. |
| `option_name` | Human label. |
| `strategy` | The strategy that produced this option. |
| `recommended` | `true` if this option matches `design_requirements.priority`. |
| `score` | Composite score. **MVP returns 0**; populated when richer scoring lands. |
| `metrics` | See below. |
| `placed_pieces` | The slab placement (see below). |
| `seams` | List of detected seam segments between adjacent pieces (see below). One entry per disjoint shared edge. |
| `review_markers` | Designer-attention notes (skipped placements, risk-flagged pieces, etc.). |
| `explanation` | Plain-English `summary` and `tradeoffs` list. |

### `metrics`

| Field | MVP populated? | Meaning |
|-------|----------------|---------|
**Project-coverage view** — how much of the floor was clad:

| Field | MVP populated? | Meaning |
|-------|----------------|---------|
| `project_usable_area` | yes | Project boundary area minus holes (mm²). The total surface that should ideally be covered. |
| `installed_area` | yes | Sum of placed piece areas (mm²). The canonical "what was actually clad" field — there is no separate `covered_area`. |
| `uncovered_area` | yes | `max(project_usable_area − installed_area, 0)`. |
| `coverage_percentage` | yes | `installed_area / project_usable_area × 100`. Tells you how much of the project is finished. |
| `layout_status` | yes | `"complete"` if `uncovered_area` is within floating-point tolerance of zero, `"partial"` if some area is uncovered, `"failed"` if no pieces were placed at all. |
| `inventory_status` | yes | `"sufficient"` whenever the layout is complete; `"insufficient"` when every input slab contributed and the layout is still partial; `"unknown"` when coverage is incomplete but slabs remain in inventory (the engine had material but the strategy couldn't place it). |

**Slab-usage view** — how efficiently the consumed slabs were used:

| Field | MVP populated? | Meaning |
|-------|----------------|---------|
| `total_slab_area_used` | yes | Sum of full areas of every slab a piece references. Offcut reuse is off, so a slab is fully consumed by any single piece taken from it. |
| `waste_area` | yes | `total_slab_area_used − installed_area`. |
| `waste_percentage` | yes | `waste_area / total_slab_area_used × 100`. **Slab efficiency**, not project completion — a layout can have `waste_percentage = 0` and `coverage_percentage = 12`. |
| `reusable_offcut_area` | no (always 0) | Reserved for offcut tracking. |
| `non_reusable_waste_area` | yes (= `waste_area`) | Reserved; equals `waste_area` until offcut tracking lands. |

**Counters and seam metrics:**

| Field | MVP populated? | Meaning |
|-------|----------------|---------|
| `piece_count` | yes | Number of emitted pieces. |
| `slabs_used` | yes | Distinct `slab_id` values across all pieces. |
| `seam_count` | yes | Number of `Seam` entries in `layout_options[i].seams`. |
| `total_seam_length` | yes | Sum of `Seam.length` across all detected seams (mm). |
| `small_piece_count` | yes | Number of pieces carrying a `small_piece` risk flag. |

**Placeholder metrics — kept simple by design until the production team defines the rules:**

| Field | MVP populated? | Meaning |
|-------|----------------|---------|
| `cut_count_estimate` | no (always 0) | Real cut counting will be defined later with the production team. Do not treat the value as authoritative. |
| `cutting_complexity_score` | no (always 1) | Real scoring criteria will be defined later. |
| `estimated_production_difficulty` | no (always `"low"`) | Should not be treated as final production guidance yet. |

---

## Output — `placed_pieces[i]`

This is the heart of the output — what Blender will draw.

```jsonc
{
  "piece_id":          "P001",
  "slab_id":           "S001",
  "project_polygon":   [[0,0],[3200,0],[3200,1800],[0,1800]],
  "slab_polygon":      [[0,0],[3200,0],[3200,1800],[0,1800]],
  "rotation":          0,
  "texture_transform": { ... },
  "is_full_slab":      true,
  "risk_flags":        []
}
```

| Field | Meaning |
|-------|---------|
| `piece_id` | Unique within an option. The `balanced` strategy uses sequential `P###` ids (`P001`, `P002`, …). The `lowest_waste` strategy uses `{slab_id}_{N}` ids (`S001_1`, `S001_2`, …) so multiple pieces from one physical slab are obvious at a glance. |
| `slab_id` | The slab this piece is attributed to. Always one of the input `slab_id`s. |
| `source_slab_id` | The original physical slab the marble was cut from. Today this always equals `slab_id`. Reserved for a future "reused offcut from elsewhere" extension; present in every emitted piece since 0.1.5. |
| `piece_index_from_slab` | 1-based index of this piece within its source slab. When a single slab contributes multiple pieces (main + offcuts, or hole-split sub-pieces) the indices are contiguous (`1`, `2`, `3`, …). |
| `piece_role` | `"main"` for the strategy's primary placement, `"offcut"` for pieces cut from leftover slab material in a second pass. `balanced` always emits `"main"`; `lowest_waste` emits both. |
| `project_polygon` | **Where the piece sits on the project surface.** Coordinates are in project space. Always a single closed ring (no interior holes). Blender uses this to position the mesh. |
| `slab_polygon` | **Where the piece was cut from on the original slab.** Coordinates are in slab-local space, with `(0, 0)` at the slab's bottom-left and `(slab.width, slab.height)` at the top-right. The engine validates that every `slab_polygon` lies inside the source slab rectangle and that pieces cut from the same slab don't overlap in slab-local coordinates. |
| `rotation` | Degrees (0/90/180/270). MVP always emits `0`. |
| `texture_transform` | UV-mapping hint for Blender. See below. |
| `is_full_slab` | `true` if the piece exactly equals the source slab (no clipping happened). Useful for production planners — full slabs need no cuts. Always `false` for offcut pieces. |
| `risk_flags` | List of soft warnings attached to this piece. Each entry is `{type, severity, message}`. See below. Empty list when nothing trips a threshold. |

#### Multiple pieces from the same physical slab

The `lowest_waste` strategy may cut a single physical slab into a main
piece **and** one or more offcut pieces installed in different parts
of the project. All of these pieces share the same `slab_id` and
`source_slab_id`; their `piece_id` differs (`S001_1`, `S001_2`, …)
and their `piece_index_from_slab` increments. Their `slab_polygon`
rectangles are disjoint in slab-local coordinates (validated by
`assert_no_slab_local_overlaps`).

If two such pieces happen to touch in the project layout, the seam
between them **is** detected and emitted in
`layout_options[i].seams` — the engine never suppresses a seam just
because both endpoints come from the same physical slab. The designer
needs to see every cut.

### `risk_flags[i]`

| Field | Meaning |
|-------|---------|
| `type` | One of `small_piece`, `narrow_piece`, `short_piece`, `thin_aspect_ratio`, `irregular_piece`. |
| `severity` | `low`, `medium`, or `high`. Today the evaluator emits `medium` for size flags and `low` for aspect-ratio / irregular-shape flags. |
| `message` | Human-readable explanation that includes the actual measurement and the threshold it breached. |

For every piece carrying ≥ 1 flag, the engine also emits a
`piece_risk`-typed entry in `layout_options[i].review_markers` whose
`location` is the piece centroid and `related_piece_ids` references the
piece. The marker's severity is the worst severity among the piece's
flags.

---

## Output — `seams[i]`

A seam is the shared boundary segment between two adjacent placed
pieces. Pieces that meet only at a corner (a single point) or are
separated by a hole / gap produce no seam.

When two pieces share **multiple disjoint** boundary segments
(uncommon but possible with non-convex pieces), each segment becomes
its own `Seam` entry — easier for Blender to render later.

```jsonc
{
  "seam_id":    "SM001",
  "piece_ids":  ["P001", "P002"],
  "line":       [[3200, 0], [3200, 1800]],
  "length":     1800,
  "visibility": "medium"
}
```

| Field | Meaning |
|-------|---------|
| `seam_id` | Sequential ID (`SM001`, `SM002`, …). Stable across runs. |
| `piece_ids` | The two pieces whose shared edge this seam is. Order is `[lower-index piece, higher-index piece]` in the placed-piece list, which is deterministic. |
| `line` | Polyline of `[x, y]` points in project-space mm. For a clean rectangular seam this is exactly two points (the segment endpoints). For a curved or multi-vertex shared edge, all vertices are preserved. |
| `length` | Length of the polyline in mm. |
| `visibility` | `low`, `medium`, or `high`. Currently always `"medium"`. Once `Layout.zones` are wired into seam scoring, seams crossing high-visibility zones will report `"high"`. |

### `texture_transform`

The slab image needs to be cropped and placed onto the piece. The
transform tells Blender exactly how:

| Field | Meaning |
|-------|---------|
| `image_path` | Echo of the slab's `image_path`. |
| `uv_origin` | Bottom-left corner of the crop in slab-local mm — the same as `min(slab_polygon)`. |
| `uv_width` | Width of the crop in mm — equal to the slab-local bbox width. |
| `uv_height` | Height of the crop in mm. |
| `rotation` | Degrees. MVP always 0. |
| `scale` | `[sx, sy]`. MVP always `[1, 1]`. |

---

## Output — reserved fields not yet populated

These exist in the schema today so future modules don't need a breaking
schema change. Until then they are returned as empty/zero/placeholder
values:

- `layout_options[i].score` (always `0`)
- `metrics.reusable_offcut_area`, `cut_count_estimate`,
  `cutting_complexity_score`, `estimated_production_difficulty`
  (see "Placeholder metrics" above)

`seams` and `review_markers` are now populated. Markers can have one
of these `type` values:

| Marker type | Source | `location` | Severity |
|-------------|--------|------------|----------|
| `empty_slab_placement_skipped` | strategy: cursor landed where the slab couldn't intersect the project | centre of the candidate slab rect | low |
| `piece_risk` | risk evaluator: piece tripped one or more risk thresholds | piece centroid | piece's worst flag severity |
| `incomplete_coverage` | engine: `layout_status != "complete"` | `null` (layout-level) | high |
| `insufficient_inventory` | engine: every input slab consumed and project still uncovered | `null` (layout-level) | high |

`ReviewMarker.location` is therefore `[x, y]` *or* `null`; layout-level
markers have no specific point in project space.

See [LIMITATIONS.md](LIMITATIONS.md) for what feeds into these once
implemented.
