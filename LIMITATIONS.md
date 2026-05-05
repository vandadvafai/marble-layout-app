# Known Limitations

What the MVP does **not** solve yet, why each one matters, and what would
need to land to fix it. This list is exhaustive as of engine version
**0.1.0** — if behaviour surprises you and isn't here, treat it as a bug
and open an issue rather than working around it.

---

## 1. Sliver pieces around holes

**What happens.** When a slab placement spans a project hole, the clipper
splits the result into four bands relative to the hole's bounding box
(left, right, below, above). The "below" and "above" bands are often very
narrow strips — sometimes only a few hundred millimetres tall — that a
human designer would never cut.

**Visible in.** [`outputs/plot_with_hole.png`](outputs/plot_with_hole.png) — pieces P003 and P004 around the column cutout.

**Why it's not fixed yet.** Splitting along the hole's bbox is the
simplest hole-free decomposition. Doing better requires a real polygon
partitioning algorithm (constrained Delaunay, ear clipping with hole
support, or ad-hoc shape-aware splitting that prefers a single L-shaped
cut).

**Mitigation today.** Raise `rules.min_piece_width` /
`min_piece_height` / `min_piece_area` to discard slivers. The pieces will
disappear from the output (the hole "eats" the slab), which is at least
honest — but it inflates waste.

**Fix path.** Replace `_split_holes` in
[`geometry/clipping.py`](placement_engine/geometry/clipping.py) with a
partitioner that produces fewer, more designer-friendly sub-polygons,
then add a `risk_flags: ["small_piece"]` annotation for any sliver that
still gets through.

---

## 2. Naive row wrapping on irregular shapes (PARTIALLY FIXED in 0.1.1)

**What used to happen.** The row-based generator works in the project's
**bounding box**, not the project polygon itself. On a non-rectangular
shape (L-shape, T-shape, anything with a notch), slabs would be placed
into bbox cells that lay entirely outside the actual project boundary,
get clipped to nothing, and still be consumed from inventory in lock
step with the cursor. An upper notch on an L-floor could silently burn
a slab.

**What happens now (engine 0.1.1+).** The placement loop now keeps the
**slab pointer** independent of the **cursor**:

- The cursor advances every iteration (so the loop terminates).
- The slab pointer only advances when a placement produces ≥ 1 valid
  clipped piece.
- When a placement attempt produces zero valid pieces, the engine emits
  an `empty_slab_placement_skipped` review marker carrying the
  `slab_id`, the centre of the candidate slab rectangle, and a low
  severity. The slab is retried at the next cursor position.

Verified by [`tests/test_skip_empty_placement.py`](tests/test_skip_empty_placement.py).
On the bundled hole example, slab S005 — previously lost — now
contributes a 960 k mm² piece in the L's upper-right corner, and a
single `R001` marker records the failed first attempt at `(1600, 4500)`.

**What is *still* not solved.**

- The generator still iterates over the project's **bounding box**, not
  the polygon interior. Notches still cause skipped placements (the
  marker tells you so) — they just don't burn inventory any more.
- A polygon-aware scanline tiler would skip notches entirely and would
  cover irregular shapes more efficiently. That work is still pending.
- If a slab is geometrically too large to fit anywhere in the project,
  the loop will skip it across every cursor position until y exceeds
  the bbox, producing many low-severity markers. This is correct (the
  slab really doesn't fit) but noisy.
- Inventory leftovers — slabs the loop terminated before reaching — are
  not flagged. The only signal that a slab wasn't used is its absence
  from `placed_pieces`.

**Fix path for the remaining gap.** Replace the bbox-driven cursor with
a scanline that walks the project polygon's actual interior, or add a
"slab too large for any remaining region" check that fast-paths
unplaceable slabs out of inventory.

---

## 3. Limited visual / vein matching

**What happens.** The schema accepts `slab.vein_direction`,
`design_requirements.preferred_vein_direction`, and
`zones[i].visibility`, but **no strategy uses them**. The engine cannot
prefer slabs whose veins line up with the longest project axis, cannot
prioritise visually consistent slabs in high-visibility zones, and cannot
reorder placement to keep matched grain together.

**Why it's not fixed yet.** Visual scoring needs (a) at minimum, the
vein-direction metadata to actually steer slab ordering, and ideally (b)
real image analysis (colour distance, edge matching, pattern continuity).
The MVP defers both — point (a) is a half-day of work, point (b) is a
project on its own.

**Mitigation today.** Order the `slabs` array manually so the visually
best slabs come first; the row-based generator places them in
inventory order, so they end up in the bottom-left rows that often
matter most.

**Fix path.** Implement a `BestVisualStrategy` that re-orders slabs by
vein-direction match and zone visibility before delegating to
`RowBasedStrategy.generate`. This is the cleanest first use of the
strategy registry.

---

## 4. No true offcut optimisation

**What happens.** A slab is treated as fully consumed the moment any
piece is cut from it. `compute_basic_metrics` adds the slab's full area
to `total_slab_area_used` regardless of how much of it the piece
actually used. `reusable_offcut_area` is always `0`, and
`rules.allow_piece_reuse_from_offcuts` is accepted but ignored.

**Why this matters.** Reported `waste_percentage` is a worst-case
estimate. In a real production line, an installer would re-use the
unused part of a partially-cut slab elsewhere. The engine has no concept
of that yet.

**Why it's not fixed yet.** Offcut tracking is a packing problem in its
own right (which leftover gets re-used where, given size and orientation
constraints). It belongs to a "lowest-waste" optimisation milestone, not
the MVP.

**Fix path.** Two pieces:
1. After clipping, record the unused portion of each placed slab as an
   "offcut" polygon.
2. Replace the linear walk over `slabs` with a queue that prefers offcuts
   large enough for the next placement before reaching for a fresh slab.
This work pairs naturally with rotation support and a proper
`LowestWasteStrategy`.

---

## 5. Only one strategy is implemented

**What happens.** `options_requested` accepts `balanced`, `lowest_waste`,
`best_visual`, `pattern_match`, `natural_random`, but every value
currently falls through to the same `BalancedStrategy` (the registry
in [`engine.py`](placement_engine/engine.py) only contains
`balanced` → `BalancedStrategy`). Unknown strategy names are silently
skipped — the engine raises only if **no** option could be generated.

**Mitigation today.** Treat the requested-strategies field as a
suggestion, not a guarantee. Today only `balanced` produces output.

**Fix path.** Add the four other strategy classes, each (initially) a
thin wrapper around `RowBasedStrategy` with different slab ordering and
random-seed handling, and register them in `STRATEGY_REGISTRY`.

---

## 6. Rotation 90/180/270 not yet wired in

**What happens.** `rules.allowed_rotations` accepts `[0, 90]` (and also
180/270), but the row-based strategy hard-codes rotation 0. The
`PlacedPiece.rotation` field is always emitted as `0.0`, and
`texture_transform.rotation` likewise.

**Why it's not fixed yet.** Rotation needs three coordinated changes:
the placement loop tries multiple rotations per slab, the slab-local
coordinate transform handles the rotated frame, and the texture
transform records the rotation so Blender can rotate the cropped image.
None are difficult individually, but they have to ship together to be
useful.

**Fix path.** Single PR that updates `_piece_from_clip` to take a
rotation argument and rotate `slab_polygon` + `texture_transform`
accordingly, and updates `RowBasedStrategy.generate` to try each
allowed rotation and pick the best fit.

---

## 7. No seam detection or seam scoring

**What happens.** `metrics.seam_count` and `metrics.total_seam_length`
are always `0`. `layout_options[i].seams` is always `[]`. The schema
slot exists, the data does not.

**Why this matters.** Seam direction, length, and visibility are
central to a designer's eye. Without this, the engine can't tell a
clean two-row layout apart from one with twelve scattered seams.

**Fix path.** Add `scoring/seams.py` that computes shared edges between
adjacent `PlacedPiece` polygons (use Shapely's `intersection` and filter
to LineString results). Populate `seams[]` and the seam metric fields.
Wire the seam count into a future `cutting_complexity_score`.

---

## 8. Risk flags & review markers (PARTIALLY FIXED in 0.1.2)

**What used to happen.** `placed_pieces[i].risk_flags` was always `[]`
and `layout_options[i].review_markers` was always `[]`.

**What happens now (engine 0.1.2+).** A new
[`scoring/risk.py`](placement_engine/scoring/risk.py) module evaluates
every placed piece against `Rules.risk_thresholds` after the strategy
runs and the geometry is validated. Pieces breaching a threshold get
structured `RiskFlag` entries (`type` + `severity` + `message`); the
engine then synthesises one `piece_risk` `ReviewMarker` per flagged
piece, located at the piece centroid and referencing it via
`related_piece_ids`. Marker IDs from the strategy and the risk module
are merged into a single contiguous `R001…Rn` sequence per option.

The schema upgrade also turned `placed_pieces[i].risk_flags` from
`list[str]` into `list[RiskFlag]` — see [SCHEMA.md](SCHEMA.md). This is
a real schema change but the field had always been `[]` in shipped
output, so no downstream consumer broke.

**Categories implemented:**
- `small_piece` — area below `risk_thresholds.min_piece_area`
- `narrow_piece` — bbox width below `risk_thresholds.min_piece_width`
- `short_piece` — bbox height below `risk_thresholds.min_piece_height`
- `thin_aspect_ratio` — `max(w/h, h/w)` above `max_aspect_ratio`
- `irregular_piece` — vertex count above `max_vertex_count`

**What is *still* not solved.**

- `near_defect` — needs slab `defects` to be considered when a piece's
  `slab_polygon` overlaps or comes close to a defect region. The schema
  already accepts `Slab.defects`; the evaluator just doesn't read them
  yet.
- `high_visibility_seam` — needs seam detection (limitation #7) and
  `Layout.zones` lookup.
- `visual_transition_warning` — needs `vein_direction` comparison
  between neighbouring pieces, which in turn needs seam detection so
  "neighbour" is well-defined.

**Fix path.** All three are additive: extend `evaluate_piece` (or add
a sibling function) to take the slab inventory and zone list, and emit
the corresponding flag types. None requires changing the existing
flags or the schema.

---

## 9. Cutting-complexity and production-difficulty scores are stubs

**What happens.** `cutting_complexity_score` always returns `1` and
`estimated_production_difficulty` always returns `"low"`. The MVP
populates these with default values so the schema validates.
`metrics.small_piece_count` is now populated correctly (from the risk
module — see #8); the other counters (`seam_count`, `cut_count_estimate`)
are still always `0`.

**Why it's not fully fixed.** A meaningful complexity score depends on
seam detection (#7) which is still pending.

**Fix path.** Implement after #7. A reasonable first formula:
`score = clamp(1, 5, round((piece_count + small_piece_count +
0.1 * seam_count) / k))`. Risk-flag counts can already feed in.

---

## 10. No Blender integration

**What happens.** The output JSON is designed to be consumed by a
Blender add-on, but the add-on does not exist. The texture transform,
slab polygons, and project polygons are all in the JSON; nothing on the
Blender side reads them.

**Mitigation today.** Use the matplotlib debug plot (`--plot`) to
eyeball the layout.

**Fix path.** Separate project (Blender add-on). The placement engine
should not grow Blender-specific code; it should keep emitting the JSON
contract.

---

## 11. No image analysis

**What happens.** `slab.image_path` is stored on each `PlacedPiece` via
`texture_transform`, but the engine never opens the file. Image
metadata is preserved as-is. There is no vein detection, no colour
distance, no defect detection.

**Why it's not fixed yet.** Image analysis is a separate research
project. The MVP is intentionally rule-based; AI/vision features come
in a later milestone, behind the same JSON contract.

**Fix path.** Add an offline pre-processor that augments the input
JSON: reads each slab image, infers `vein_direction`, populates
`defects` automatically, and writes the enriched JSON. The placement
engine then runs unchanged.

---

## 12. No web app, no database, no API server

The MVP is a local CLI by design. There is no FastAPI layer, no
upload workflow, no persistence beyond the input/output JSON files. All
of these are future work and intentionally out of scope.

---

## 13. Floating-point tolerances are global, not per-project

`AREA_EPSILON_MM2`, `LENGTH_EPSILON_MM`, and
`DEFAULT_OVERLAP_TOLERANCE_MM2` live in
[`config.py`](placement_engine/config.py) as module constants. A
project working at micrometre precision or at metre precision would
both use the same defaults. Not a problem at typical floor scales, but
worth flagging.

**Fix path.** Move the tolerances onto the `Rules` model so they can be
overridden per-project.

---

## 14. No per-project random seed plumbing

`ProjectInput.random_seed` is accepted (default 42) but no strategy
currently consumes it. The MVP is deterministic by construction;
randomness only matters once `natural_random` lands.

**Fix path.** Pass the seed through `StrategyContext` and have
`NaturalRandomStrategy` build a seeded `random.Random` from it.
