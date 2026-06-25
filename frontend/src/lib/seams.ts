// Derive editable seams from the current piece set.
//
// V1 pieces are axis-aligned tile rectangles. A seam is a shared
// edge between two pieces:
//
//   * vertical seam   — pieces share a column boundary at x = X
//                       (one piece has its right edge at X, the
//                        other has its left edge at X)
//   * horizontal seam — pieces share a row boundary at y = Y
//
// Up to 0.1.39 we grouped by ``zone_id`` first, which meant seams
// straddling two zones (e.g. the boundary created by the
// zone-splitting algorithm) were INVISIBLE to the editor — the
// designer couldn't drag them. The editor is now a fully
// interactive design tool, so we derive seams from the full piece
// set; same-zone and cross-zone seams are both editable. We tag
// each seam with the zone(s) of the pieces it touches:
//
//   * single-zone seam → zone_id = that zone's id, ``is_boundary``=false
//   * multi-zone seam  → zone_id = "boundary", ``is_boundary``=true
//
// The visual layer uses ``is_boundary`` to render boundary seams
// in a slightly different colour so designers can tell them apart;
// drag mechanics are identical.
//
// The function also computes per-seam drag limits so the canvas's
// pointer math can clamp without re-walking. The limits keep the
// seam at least MIN_GAP_MM away from the adjacent seam (or zone
// wall), preventing pieces from collapsing to negative width.
//
// Two compression conventions worth noting:
//   * We index pieces by their nominal rectangle (nominal_x/y_mm +
//     nominal_w/h_mm), not their actual_cut_polygon, because the
//     editor edits the nominal grid and lets the backend reconstruct
//     polygons during validation.
//   * Seam positions are rounded to the nearest 1 mm before they
//     become part of the seam_id so two pieces touching at
//     x=1590.0000000003 and x=1589.9999999998 still bucket into the
//     same seam (float noise from earlier polygon clipping).

import type { Orientation, Piece, Seam } from "./types";

/** Minimum allowable gap between a seam and the next seam/wall.
 *
 * Up to 0.1.41 this was 100 mm (10 cm) — an implementation artifact
 * that prevented designers from creating thin pieces even when the
 * geometry would have been perfectly valid. The engine itself only
 * needs positive width/height. We now clamp to 1 mm, which combined
 * with the 50 mm snap (``SNAP_MM`` in lib/editing.ts) means seams
 * naturally land on 50 mm multiples for drag, while the numeric
 * input on the seam form can dial down further if a designer needs
 * a thin slice. */
export const MIN_GAP_MM = 1;
/** Rounding precision for seam-position bucketing (mm). */
const POSITION_BUCKET_MM = 1;

interface Bucket {
  /** Pieces with their right (vertical) or top (horizontal) edge on this seam. */
  before: Piece[];
  /** Pieces with their left (vertical) or bottom (horizontal) edge on this seam. */
  after: Piece[];
}

/** Sentinel zone_id used on cross-zone seams. The drag handler in
 *  ``editing.applySeamMove`` only consults the piece_left_ids /
 *  piece_right_ids lists, so the zone_id is purely informational
 *  (canvas hint + UI label). */
export const BOUNDARY_ZONE_ID = "boundary";

export function deriveSeams(pieces: Piece[]): Seam[] {
  // No more grouping by zone — the seam-build routine itself decides
  // whether each shared edge is interior to one zone or a boundary
  // between two zones. This is what unlocks editing on zone-split
  // seams that the old per-zone walk hid from the editor.
  return [
    ...buildSeams(pieces, "vertical"),
    ...buildSeams(pieces, "horizontal"),
  ];
}

function buildSeams(
  allPieces: Piece[], orientation: Orientation,
): Seam[] {
  // Bucket pieces by the perpendicular axis position. For a
  // vertical seam we look at each piece's left and right x
  // coordinates; for a horizontal seam we use top/bottom y.
  const buckets = new Map<number, Bucket>();
  for (const p of allPieces) {
    const { before, after } =
      orientation === "vertical"
        ? {
            before: p.nominal_x_mm + p.nominal_width_mm,  // right edge
            after: p.nominal_x_mm,                         // left edge
          }
        : {
            before: p.nominal_y_mm + p.nominal_height_mm, // top edge
            after: p.nominal_y_mm,                         // bottom edge
          };
    addToBucket(buckets, before, "before", p);
    addToBucket(buckets, after, "after", p);
  }

  // The zone walls = the lowest and highest bucket keys with only
  // one side populated. Interior positions (both sides populated)
  // are the seams we expose to the editor.
  const sortedPositions = [...buckets.keys()].sort((a, b) => a - b);
  const zoneMin = sortedPositions[0];
  const zoneMax = sortedPositions[sortedPositions.length - 1];

  const seams: Seam[] = [];
  for (let i = 0; i < sortedPositions.length; i++) {
    const pos = sortedPositions[i];
    const bucket = buckets.get(pos)!;
    if (bucket.before.length === 0 || bucket.after.length === 0) continue;

    // Drag limits — adjacent interior seam OR zone wall.
    const prev = previousInteriorOrWall(buckets, sortedPositions, i, "down");
    const next = previousInteriorOrWall(buckets, sortedPositions, i, "up");
    const min_position = (prev ?? zoneMin) + MIN_GAP_MM;
    const max_position = (next ?? zoneMax) - MIN_GAP_MM;

    // Perpendicular extent: union of touching pieces' projections
    // along the other axis. For vertical seams that's [y_lo..y_hi].
    const allTouching = [...bucket.before, ...bucket.after];
    const range: [number, number] = perpendicularRange(allTouching, orientation);

    // Classify the seam: same-zone vs cross-zone. Tag boundary seams
    // with the sentinel zone so the canvas can render them in a
    // distinct colour and the seam_id stays globally unique.
    const zoneIds = new Set(allTouching.map((p) => p.zone_id));
    const isBoundary = zoneIds.size > 1;
    const seamZoneId = isBoundary ? BOUNDARY_ZONE_ID : [...zoneIds][0];

    seams.push({
      seam_id: `${seamZoneId}:${orientation}:${pos}`,
      zone_id: seamZoneId,
      is_boundary: isBoundary,
      orientation,
      position: pos,
      range,
      piece_left_ids: bucket.before.map((p) => p.piece_id),
      piece_right_ids: bucket.after.map((p) => p.piece_id),
      min_position,
      max_position,
    });
  }
  return seams;
}

function addToBucket(
  buckets: Map<number, Bucket>,
  position: number, side: "before" | "after", piece: Piece,
): void {
  const key = bucketKey(position);
  let entry = buckets.get(key);
  if (!entry) {
    entry = { before: [], after: [] };
    buckets.set(key, entry);
  }
  entry[side].push(piece);
}

function bucketKey(position: number): number {
  return Math.round(position / POSITION_BUCKET_MM) * POSITION_BUCKET_MM;
}

function previousInteriorOrWall(
  buckets: Map<number, Bucket>,
  sortedPositions: number[],
  i: number,
  direction: "down" | "up",
): number | null {
  // Walk neighbours until we find another interior seam (both
  // sides populated) or the zone wall (a position the caller treats
  // as the boundary). Returns the position, or null if we hit the
  // very edge of the sorted list.
  const step = direction === "down" ? -1 : 1;
  for (let j = i + step; j >= 0 && j < sortedPositions.length; j += step) {
    const pos = sortedPositions[j];
    const b = buckets.get(pos)!;
    const interior = b.before.length > 0 && b.after.length > 0;
    const wall = b.before.length === 0 || b.after.length === 0;
    if (interior || wall) return pos;
  }
  return null;
}

function perpendicularRange(
  pieces: Piece[], orientation: Orientation,
): [number, number] {
  let lo = Infinity;
  let hi = -Infinity;
  for (const p of pieces) {
    if (orientation === "vertical") {
      lo = Math.min(lo, p.nominal_y_mm);
      hi = Math.max(hi, p.nominal_y_mm + p.nominal_height_mm);
    } else {
      lo = Math.min(lo, p.nominal_x_mm);
      hi = Math.max(hi, p.nominal_x_mm + p.nominal_width_mm);
    }
  }
  return [lo, hi];
}
