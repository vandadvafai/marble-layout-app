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

## 4. Offcut reuse — basic rectangular only (PARTIALLY in 0.1.5)

**What happens now (engine 0.1.5+).** The new `lowest_waste` strategy
runs a second pass after the main row-based placement. For each slab
that was edge-clipped, the slab-local complement of its used bounding
box becomes one or more `OffcutRectangle`s. The strategy then walks
uncovered project components largest-first and greedily cuts
corner-anchored sub-rectangles from the best fitting offcut to fill
each gap. Pieces cut from the same physical slab share `slab_id` /
`source_slab_id` and are emitted as separate installed pieces (with
`piece_id = "{slab_id}_{N}"`); seams between them are detected just
like any other seam. See [`scoring/seams.py`](placement_engine/scoring/seams.py)
and [`strategies/lowest_waste.py`](placement_engine/strategies/lowest_waste.py).

**`balanced` is unchanged.** It still treats each slab as a single
placement and emits one piece per placement.

**`waste_percentage` interpretation.** For `lowest_waste`, a slab is
"consumed" the moment **any** piece is cut from it; if the strategy
manages to use all of that slab's material across main + offcut
pieces, the slab contributes `0` waste. (The corridor fixture shows
this clearly: `lowest_waste` reports `waste_percentage = 0 %` because
S006 is fully consumed across one main piece and nine offcut strips.)

**What is *still* not solved.**

- **Only axis-aligned rectangular offcuts.** Non-rectangular leftover
  shapes (which can occur when clipping a slab against an irregular
  project edge) are bbox-approximated; any non-rectangular slack is
  treated as waste rather than reused.
- **Bbox-rectangular gap fills only.** When the uncovered region is a
  non-rectangular polygon, only the bounding-box portion can be
  filled; an L-shaped uncovered tail stays uncovered.
- **Greedy first-fit, no back-tracking.** A genuinely optimal packer
  would explore alternative cut patterns; for v1 the strategy commits
  to the largest-fitting offcut.
- **`reusable_offcut_area` is still always `0`.** The metric is
  reserved for a future world where offcuts that the strategy chose
  *not* to use are tracked as available stock; today every offcut
  either gets reused immediately or is rolled into `waste_area`.
- **`rules.allow_piece_reuse_from_offcuts` is still accepted but not
  enforced.** Today `lowest_waste` always reuses; setting this to
  `false` does nothing. A future tweak should respect the flag.

**Fix path forward.**

1. **Polygon-aware offcut tracking** — replace `OffcutRectangle` with
   a Shapely-polygon-based representation; subtract used pieces from
   the slab polygon directly. Lifts the rectangular-only restriction
   on both offcut shape and gap shape.
2. **Real `reusable_offcut_area` accounting** — once polygon offcuts
   exist, leftover material that didn't get used in the current
   project becomes a per-slab residual the metric can populate.
3. **Smarter packing** — back-tracking, alternative cut patterns,
   rotation. Naturally pairs with #6 (rotation support).
4. **Honour `rules.allow_piece_reuse_from_offcuts = false`** —
   trivial gate; useful for designers who want a "no-mosaic"
   guarantee.

---

## 5. Only `balanced` and `lowest_waste` ship today

**What happens.** `options_requested` accepts `balanced`,
`lowest_waste`, `best_visual`, `pattern_match`, `natural_random`. The
registry in [`engine.py`](placement_engine/engine.py) currently
contains `balanced` and `lowest_waste`; the other three are silently
skipped — the engine raises only if **no** requested strategy could
produce an option.

**Mitigation today.** Treat `best_visual`, `pattern_match`, and
`natural_random` as planned-but-unimplemented; the engine doesn't
error on them so callers can pre-write JSON for the eventual full set.

**Fix path.** Add the three remaining strategy classes and register
them in `STRATEGY_REGISTRY`. Once `cutting_complexity_score` (#9) is
defined, comparative scoring across strategies becomes meaningful.

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

## 7. Seam detection (IMPLEMENTED in 0.1.3)

**What happens now.**
[`scoring/seams.py`](placement_engine/scoring/seams.py) runs after
geometry validation. For every pair of placed pieces it intersects
their `boundary` LineStrings and inspects the result:
- `LineString` → one `Seam`
- `MultiLineString` → one `Seam` per disjoint segment
- `Point` / `MultiPoint` (corner-only contact) → ignored
- segment length below `Rules.seam_tolerance` → ignored
- pieces separated by a hole or a gap → no intersection, no seam

The output JSON now contains:
- `layout_options[i].seams[]` with `seam_id`, `piece_ids`, `line`,
  `length`, `visibility`
- `metrics.seam_count` = `len(seams)`
- `metrics.total_seam_length` = Σ `seam.length`

Verified by [`tests/test_seams.py`](tests/test_seams.py): the simple
2 × 2 example produces exactly 4 seams totalling 9 600 mm; the L-shape
with the column cutout produces 11 seams; corner-only and across-hole
cases produce none.

**What is *still* not solved.**

- **Seam visibility is always `"medium"`.** `Layout.zones` exist in the
  schema but the seam detector does not yet check whether a seam
  crosses a high-visibility zone.
- **No aesthetic seam ranking** (preferring fewer, longer seams over
  many short ones; preferring symmetry; preferring axis-aligned over
  diagonal). Needed before a `pattern_match` strategy is meaningful.
- **No seam-minimisation strategy.** The current row-based generator
  doesn't know about seams; it just lays slabs. A future
  `lowest_waste` / `pattern_match` strategy would optimise for seam
  count or layout symmetry.
- **No Blender seam visualisation yet.** The JSON contract is now
  complete enough to drive one — that work belongs to the future
  Blender add-on.
- **The `cutting_complexity_score` and
  `estimated_production_difficulty` metrics still don't consume seam
  data** — they remain hardcoded to `1` / `"low"`. Wiring this in is
  the natural next milestone (see #9).

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

## 9. Cut counting, complexity score, production difficulty are placeholders

**What happens.** `cut_count_estimate` always returns `0`,
`cutting_complexity_score` always returns `1`, and
`estimated_production_difficulty` always returns `"low"`. The MVP
populates these with default values so the schema validates and so
downstream consumers can reserve the keys.

**Why they're kept as placeholders.** Real cut counting and a
meaningful complexity score depend on production-line specifics that
the design / production team has not yet defined. Any formula we ship
today would be a guess that downstream code might quietly start to
trust.

`metrics.piece_count`, `slabs_used`, `small_piece_count`,
`seam_count`, `total_seam_length`, `coverage_percentage`,
`layout_status`, and `inventory_status` are now all populated honestly,
so the inputs needed for a real complexity formula are in place when
the team is ready.

**Fix path (when the team is ready).** Add `scoring/complexity.py`,
define the cut-counting heuristic and the complexity weights with the
production team, wire through `engine.py`, and add regression tests
pinning the scores on the shipped examples. Naturally pairs with the
next strategy milestone so each strategy can be compared by score.

**Until then:** treat these three fields as advisory only. Do not
treat them as final production guidance.

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
