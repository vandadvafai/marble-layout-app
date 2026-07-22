// Shapes returned by the FastAPI demo endpoint. Kept narrow on
// purpose — these mirror placement_engine/api/serializers.py and
// nothing else. When the editing milestone lands we'll widen them
// or split into request/response namespaces.

export type Point = [number, number];

export interface Piece {
  piece_id: string;
  zone_id: string;
  /** Nominal grid coordinates — i.e. "where the full working slab
   *  would sit if the floor were infinite". For edge clips, hole
   *  splits, and absorbed-sliver merges these DO NOT describe the
   *  cut piece; they're traceability fields ("which row/column did
   *  this strip come from?"). Display + match + DXF export must
   *  prefer the polygon-derived cut dims (see ``lib/pieceGeom``). */
  nominal_x_mm: number;
  nominal_y_mm: number;
  nominal_width_mm: number;
  nominal_height_mm: number;
  /** Real cut dimensions — polygon bbox + signed area. Backend
   *  populates these for every piece. Optional in the type to keep
   *  cached payloads from before 0.1.54 readable; the helper in
   *  ``lib/pieceGeom`` falls back to a polygon-derived computation
   *  when these aren't present. */
  bounding_width_mm?: number;
  bounding_height_mm?: number;
  actual_area_m2?: number;
  polygon: Point[];
  is_full_tile: boolean;
  is_edge_piece: boolean;
  intersects_hole: boolean;
  notes: string[];
}

export interface LayoutTarget {
  target_id: string;
  name: string;
  /** [x0, y0, x1, y1] */
  bbox: [number, number, number, number];
  boundary: Point[];
  holes: Point[][];
}

export interface LayoutGrid {
  tile_width_mm: number;
  tile_height_mm: number;
  origin: Point | null;
  anchor_mode: string | null;
}

export interface Layout {
  target: LayoutTarget;
  grid: LayoutGrid;
  pieces: Piece[];
  piece_count: number;
}

export interface Doorway {
  doorway_id: string;
  segment: [Point, Point];
  width_mm: number;
  is_main_entrance: boolean;
}

export interface Column {
  column_id: string;
  polygon: Point[];
}

export interface Space {
  space_id: string;
  name: string;
  polygon: Point[];
  visibility: string;
}

export interface GuideLine {
  guide_line_id: string;
  segment: [Point, Point];
  priority: number;
  name: string;
}

export interface Plan {
  target_id: string;
  spaces: Space[];
  doorways: Doorway[];
  columns: Column[];
  guide_lines: GuideLine[];
}

export interface DemoLayoutResponse {
  demo_id: string;
  label: string;
  layout: Layout;
  plan?: Plan;
}

export interface DemoIndexEntry {
  demo_id: string;
  label: string;
}

export interface DemoIndexResponse {
  demos: DemoIndexEntry[];
}

// ---------------------------------------------------------------------------
// Editing — derived in the frontend, never round-tripped to the backend.
// ---------------------------------------------------------------------------

export type Orientation = "vertical" | "horizontal";

export interface Seam {
  /** Stable across renders: zone + orientation + integer position. */
  seam_id: string;
  /** ``"boundary"`` for cross-zone seams (see ``BOUNDARY_ZONE_ID`` in
   *  ``lib/seams.ts``), otherwise the shared zone of the touching pieces. */
  zone_id: string;
  /** True when the seam straddles two different zones (i.e. it's the
   *  boundary the zone-splitting algorithm produced). The drag
   *  mechanics are the same; the canvas just renders it slightly
   *  differently so the designer can tell it apart from an interior
   *  seam. */
  is_boundary: boolean;
  orientation: Orientation;
  /** x for vertical seams, y for horizontal seams. */
  position: number;
  /** Perpendicular extent — [y0, y1] for vertical, [x0, x1] for horizontal. */
  range: [number, number];
  /** Pieces on the left (vertical) or bottom (horizontal) of the seam. */
  piece_left_ids: string[];
  /** Pieces on the right (vertical) or top (horizontal) of the seam. */
  piece_right_ids: string[];
  /** Clamp the drag so the seam can't move past these positions. */
  min_position: number;
  max_position: number;
}

// ---------------------------------------------------------------------------
// Validation — POST /api/demo-layouts/{demo_id}/validate response.
// ---------------------------------------------------------------------------

export type RuleStatus =
  | "pass"
  | "violation"
  | "reward"
  | "info"
  | "not_applicable";

export interface RuleResult {
  rule_id: string;
  status: RuleStatus;
  count: number;
  message: string;
  affected_ids: string[];
  score_delta: number;
}

export interface PieceEvaluation {
  piece_id: string;
  zone_id: string;
  space_id: string | null;
  visibility: string;
  bbox_width_mm: number;
  bbox_height_mm: number;
  is_below_min: boolean;
  is_small: boolean;
  is_absorbed_holder: boolean;
  crosses_doorway: boolean;
}

export interface SeamEvaluation {
  seam_id: string;
  piece_a_id: string;
  piece_b_id: string;
  length_mm: number;
  crosses_doorways: string[];
  crosses_main_entrance: boolean;
  near_columns: string[];
}

export interface ValidationResult {
  target_id: string;
  is_valid: boolean;
  design_score: number;
  hard_violation_count: number;
  soft_violation_count: number;
  reward_count: number;
  score_breakdown: Record<string, number>;
  rules: RuleResult[];
  pieces: PieceEvaluation[];
  seams: SeamEvaluation[];
}

// ---------------------------------------------------------------------------
// Annotation tools — designer-driven plan edits.
// ---------------------------------------------------------------------------

/**
 * Editor interaction modes (0.1.41 — explicit modes milestone).
 *
 * The old single ``"select"`` mode tried to handle BOTH piece
 * selection and seam dragging by hit-test priority. That was
 * ambiguous: a click near a seam might select the underlying piece,
 * a click on a piece might fall through to a seam drag, etc. The
 * new model separates intent up front:
 *
 *   * ``"select_piece"`` — only pieces respond. Seams are inert
 *     (no hover, no drag, no selection). Doorways / columns / guide
 *     lines remain selectable.
 *   * ``"edit_seam"``    — only seams respond. Pieces are inert
 *     (no hover, no selection, no highlight). Doorways / columns /
 *     guide lines remain selectable (they're plan annotations, not
 *     pieces, so the ambiguity doesn't apply).
 *   * The four ``add_*`` / ``doorway`` / ``column`` / ``guide_line``
 *     modes are creation modes — drag in empty space to draw the
 *     new object.
 *
 * Mode is the SINGLE source of truth for "what does a click do?".
 * The canvas no longer infers intent.
 */
export type EditorMode =
  | "select_piece"
  | "edit_seam"
  | "doorway"
  | "column"
  | "guide_line"
  | "add_seam";

/** Modes in which interactive selection is the primary action (as
 *  opposed to drawing new objects). Kept as a type-level union so
 *  the canvas can narrow ``mode`` without listing literals at every
 *  call site. */
export type SelectionMode = "select_piece" | "edit_seam";

/** What the designer currently has selected on the canvas. */
export type Selection =
  | { kind: "doorway"; id: string }
  | { kind: "column"; id: string }
  | { kind: "guide_line"; id: string }
  | { kind: "seam"; id: string }
  | { kind: "piece"; id: string };

/** Transient state while the designer is dragging out a new object
 *  in one of the creation modes. ``start`` and ``current`` are
 *  engine-mm coordinates. */
export interface DrawingState {
  mode: Exclude<EditorMode, SelectionMode>;
  start: Point;
  current: Point;
}

// Risk badge surface shared between lib/pieceRisk and PiecesPanel.
// Re-exported here as the canonical "frontend types live in one
// place" convention.
export type { PieceRisk, PieceWithRisk, RiskBadge, RiskLevel } from "./pieceRisk";

// ---------------------------------------------------------------------------
// Inventory matching — POST /api/demo-layouts/{id}/match-inventory response.
// ---------------------------------------------------------------------------

export type MatchStatus =
  | "exact_fit"
  | "matched"
  | "multiple_options"
  | "no_match";

export interface SlabCandidate {
  slab_id: string;
  serial_number: string | null;
  /** Source-spreadsheet ERP code. Null for inventories that don't
   *  carry one. Shown as the secondary id under the slab heading. */
  item_code?: string | null;
  /** Material name and finish if the inventory recorded them. Both
   *  are nullable for V1 — most clean_slabs.json exports leave them
   *  blank. Surfaced as a small badge when present. */
  material_name?: string | null;
  finish?: string | null;
  /** ORIGINAL slab dimensions from the inventory (Excel). Do NOT
   *  swap on rotation — the slab itself isn't smaller, the cutter
   *  just orients it differently. */
  width_mm: number;
  height_mm: number;
  /** Final cut dimensions — what's cut OUT of the slab to make the
   *  piece. Today equal to the piece's nominal w × h since the
   *  matcher already verified the slab covers it. Surfaced as its
   *  own pair (0.1.49) so the Properties panel can show "piece
   *  size", "original slab size", and "final cut size" without
   *  ambiguity. */
  cut_width_mm: number;
  cut_height_mm: number;
  waste_mm2: number;
  waste_fraction: number;
  rotation_needed: boolean;
  image_path: string | null;
}

export interface PieceMatchResult {
  piece_id: string;
  required_width_mm: number;
  required_height_mm: number;
  required_area_m2: number;
  status: MatchStatus;
  candidates: SlabCandidate[];
}

export interface InventoryMatchSummary {
  exact_fit: number;
  multiple_options: number;
  matched: number;
  no_match: number;
  total_pieces: number;
}

/** Dimensional statistics computed from the resolved inventory's
 *  valid slabs. Returned inside ``InventoryInfo.stats`` so the
 *  Step-3 panel can show median / mean sizes without a second call.
 *  All values are mm. ``is_inconsistent`` is true when min/max span
 *  more than 100% of the median in either axis — a flag for the UI
 *  to warn that median-based layout may not represent the batch
 *  well. */
export interface InventoryStats {
  slab_count: number;
  median_width_mm: number;
  median_height_mm: number;
  mean_width_mm: number;
  mean_height_mm: number;
  min_width_mm: number;
  max_width_mm: number;
  min_height_mm: number;
  max_height_mm: number;
  mode_width_mm: number | null;
  mode_height_mm: number | null;
  mode_width_count: number | null;
  mode_height_count: number | null;
  is_inconsistent: boolean;
}

/** Header block describing which inventory file the backend resolved
 *  to. Returned both standalone from /api/inventory/info and embedded
 *  inside the matcher response so a single round-trip is enough to
 *  populate the panel header + the per-piece candidates. */
export interface InventoryInfo {
  /** ``"empty"`` | ``"uploaded"`` | ``"env_override"`` — see
   *  ``placement_engine/api/inventory_source.py`` for the source of
   *  truth. Kept as a string (not a strict union) so a future label
   *  doesn't require a frontend bump. On a fresh install the value
   *  is ``"empty"`` until the designer completes Step 3. */
  source_label: string;
  /** Human-readable explanation of the label, e.g. "uploaded by
   *  designer (Step 3)". Shown as the chip tooltip + the panel
   *  subtitle. */
  source_description: string;
  /** Project-relative path to the resolved ``clean_slabs.json``.
   *  ``null`` when no inventory has been uploaded — the app never
   *  surfaces raw filesystem paths outside Step 3's Developer
   *  Details block. */
  source_path: string | null;
  valid_count: number;
  skipped_count: number;
  total_records: number;
  /** Dimension statistics computed from the valid slabs. null when
   *  the inventory has no usable rows. */
  stats: InventoryStats | null;
}

/** Response shape from POST /api/demo-layouts/{demo_id}/regenerate.
 *  Carries the new layout + plan plus the chosen tile size (so the
 *  UI can show "working slab: 1790 × 1730 mm · from inventory"). */
export interface RegenerateLayoutResponse {
  demo_id: string;
  label: string;
  layout: DemoLayoutResponse["layout"];
  plan?: DemoLayoutResponse["plan"];
  tile_choice: {
    tile_width_mm: number;
    tile_height_mm: number;
    basis: "explicit_override" | "inventory_median";
  };
  inventory_source_label: string;
}

// ---------------------------------------------------------------------------
// Workflow (0.1.39 — 4-step wizard milestone).
//
// The editor was rebuilt around a 4-step production flow:
//   1. Upload Plan — pick a DXF (sample picker fallback for V1)
//   2. Plan Validation — the existing canvas + seam + plan editor
//   3. Upload Slabs — pick an Excel + photos (placeholder for V1)
//   4. Assign + Export — finalize, pick slabs, render export buttons
//
// ``currentStep`` lives in App state and is persisted per-demo via
// localStorage so a refresh keeps the designer where they were.
// The stepper header dispatches changes; helpers below gate which
// steps are reachable based on the prerequisites being satisfied.
// ---------------------------------------------------------------------------

export type WorkflowStep = 1 | 2 | 3 | 4;

/** Frozen pieces snapshot the designer takes at the end of Step 2.
 *  Drives Step 4's canvas + assignment surface — once finalized,
 *  pieces don't change unless the designer goes back and
 *  re-finalizes. */
export interface FinalizationState {
  pieces: Piece[];
  finalizedAt: string;
}

/** piece_id → slab_id (or null = explicitly unassigned). Stored as
 *  a plain object so JSON round-trips through localStorage trivially. */
export type Assignments = Record<string, string | null>;

/** Per-piece status the panel surfaces in Step 4.
 *
 *  ``too_small`` (0.1.53 — manual swap milestone): the designer
 *  assigned a slab to the piece, but the slab is not large enough
 *  to cover the piece's nominal w × h (even after rotation when
 *  rotation is allowed). Typically the result of a Manual-swap
 *  drag that moved a smaller slab onto a larger piece. The chip
 *  flags the conflict and the export bar refuses to fire until
 *  every piece is back to a valid state. */
export type AssignmentStatus =
  | "unassigned"
  | "assigned"
  | "no_match"
  | "too_small"
  | "duplicate";

// ---------------------------------------------------------------------------
// Step-3 upload (0.1.43 milestone).
//
// /api/inventory/upload returns this shape after processing the
// Excel + photo upload through the slab-intake pipeline. The
// summary is what the Step-3 panel renders directly; the
// session_id is informational since only one upload is active.
// ---------------------------------------------------------------------------

export interface InventoryPreviewRow {
  slab_id: string | null;
  serial_number: string | null;
  item_code: string | null;
  width_cm: number | null;
  height_cm: number | null;
  width_mm: number | null;
  height_mm: number | null;
  area_m2: number | null;
  image_found: boolean;
  image_filename: string | null;
  warnings: string[];
}

export interface InventoryUploadSummary {
  total_rows: number;
  valid_slabs: number;
  invalid_slabs: number;
  linked_photos: number;
  unmatched_photos: string[];
  slabs_without_photos: string[];
  mapped_columns: Record<string, string>;
  unmapped_columns: string[];
  warning_counts: Record<string, number>;
  preview: InventoryPreviewRow[];
  /** Per-status calibration counts (M4). Optional so payloads cached
   *  from before the calibration milestone still parse. */
  calibration?: CalibrationCounts;
}

export interface InventoryUploadResponse {
  session_id: string;
  uploaded_at: string;
  excel_filename: string;
  image_count: number;
  summary: InventoryUploadSummary;
}

// ---------------------------------------------------------------------------
// Slab calibration (M4 — Calibration UI & Workflow).
//
// Mirrors ``placement_engine.calibration.models.CalibrationRecord.to_dict()``
// field-for-field. The backend is the single source of truth for the
// usable-dimension math (20 mm/side, applied exactly once) — this type
// only carries the numbers through, never re-derives them.
// ---------------------------------------------------------------------------

export type SourceType =
  | "green_boundary" | "scanned_crop" | "raw_photo" | "no_photo";

export type CalibrationStatus =
  | "approved" | "needs_review" | "rejected" | "missing_photo";

export interface CropRectangle {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface CalibrationRecord {
  slab_id: string;
  source_type: SourceType;
  excel_width_mm: number;
  excel_height_mm: number;
  usable_width_mm: number;
  usable_height_mm: number;
  calibration_status: CalibrationStatus;
  factory_policy_version: string;
  original_image_path: string | null;
  calibrated_image_path: string | null;
  /** 4-length [x, y] pixel corners in original-image space, ordered
   *  top-left, top-right, bottom-right, bottom-left. */
  detected_corners: Point[] | null;
  confirmed_corners: Point[] | null;
  crop_coordinates: CropRectangle | null;
  calibration_confidence: number | null;
  aspect_delta: number | null;
  approved_at: string | null;
  approved_by: string | null;
  warnings: string[];
  notes: string | null;
}

export interface CalibrationCounts {
  approved: number;
  needs_review: number;
  missing_photo: number;
  rejected: number;
}

export interface CalibrationRecordsResponse {
  active: boolean;
  session_id?: string;
  factory_policy_version?: string;
  records: CalibrationRecord[];
  counts: CalibrationCounts;
}

export interface InventoryCurrentResponse {
  active: boolean;
  session_id?: string;
  uploaded_at?: string;
  excel_filename?: string;
  image_count?: number;
  summary?: InventoryUploadSummary;
}

export interface InventoryMatchResponse {
  demo_id: string;
  /** The new inventory header block. Older clients can still read
   *  the legacy ``inventory_count`` / ``inventory_path`` fields below. */
  inventory: InventoryInfo;
  inventory_path: string;
  inventory_count: number;
  allow_rotation: boolean;
  pieces: PieceMatchResult[];
  summary: InventoryMatchSummary;
}
