// Add a new seam by splitting the affected pieces.
//
// V1 model: a new seam always spans the full perpendicular extent
// of its target zone, so the user only chooses orientation +
// position (snapped to 50 mm) + which zone. Pieces in that zone
// that cross the seam line are split into two; pieces in other
// zones and pieces that don't cross the line are untouched.
//
// Why one-zone-only: in the L-shape, zone z0 is the lower bar
// and zone z1 is the upper-right column. The two zones don't
// share grid coordinates, so a single global "x = 1590" seam
// wouldn't make sense across both. Picking the zone from the
// user's pointer-down anchor keeps the model intuitive.
//
// Min-size policy: we only split when BOTH resulting pieces would
// be at least ``MIN_SPLIT_REMAINDER_MM`` wide/tall. Pieces too
// thin to split safely are left intact (the user sees no change
// for that piece and can try a different position). This avoids
// silently spawning sub-100 mm pieces just because the drag
// landed close to an existing seam.

import type { Orientation, Piece, Point } from "./types";

/** Snap increment matching the seam-drag editor. */
export const SNAP_MM = 50;

/** Minimum residual size of each piece after a split (mm). */
export const MIN_SPLIT_REMAINDER_MM = 100;

export function snapTo50(value: number): number {
  return Math.round(value / SNAP_MM) * SNAP_MM;
}

/** Determine seam orientation from a drag delta. Returns "horizontal"
 *  when the drag travelled more in the x axis than the y axis,
 *  otherwise "vertical". (A horizontal seam runs along x and is
 *  positioned by its y coordinate.) */
export function inferOrientation(start: Point, end: Point): Orientation {
  const dx = Math.abs(end[0] - start[0]);
  const dy = Math.abs(end[1] - start[1]);
  return dx > dy ? "horizontal" : "vertical";
}

/** Compute the seam's position along the perpendicular axis from a
 *  drag, snapped to ``SNAP_MM``. For a horizontal seam this is a y
 *  value; for a vertical seam it's an x value. We use the midpoint
 *  of the two endpoints so the seam doesn't snap to one end of
 *  the drag — visually more predictable. */
export function seamPositionFromDrag(
  orientation: Orientation, start: Point, end: Point,
): number {
  const axis = orientation === "horizontal" ? 1 : 0;
  const mid = (start[axis] + end[axis]) / 2.0;
  return snapTo50(mid);
}

/** Locate the piece at the given engine-mm point (axis-aligned bbox
 *  hit-test). Used to pick the target zone when the user starts a
 *  seam-add drag inside a piece. Returns ``null`` when the point
 *  is outside every piece. */
export function findPieceAt(pieces: Piece[], point: Point): Piece | null {
  const [x, y] = point;
  for (const p of pieces) {
    if (
      x >= p.nominal_x_mm
      && x <= p.nominal_x_mm + p.nominal_width_mm
      && y >= p.nominal_y_mm
      && y <= p.nominal_y_mm + p.nominal_height_mm
    ) {
      return p;
    }
  }
  return null;
}

/** Union bbox of every piece in a zone (engine-mm). */
export function zoneBbox(
  pieces: Piece[], zone_id: string,
): [number, number, number, number] | null {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  let any = false;
  for (const p of pieces) {
    if (p.zone_id !== zone_id) continue;
    any = true;
    if (p.nominal_x_mm < x0) x0 = p.nominal_x_mm;
    if (p.nominal_y_mm < y0) y0 = p.nominal_y_mm;
    const right = p.nominal_x_mm + p.nominal_width_mm;
    const top = p.nominal_y_mm + p.nominal_height_mm;
    if (right > x1) x1 = right;
    if (top > y1) y1 = top;
  }
  return any ? [x0, y0, x1, y1] : null;
}

/** Result of a seam-add operation. ``unchanged`` flags the case
 *  where the requested seam didn't cross any splittable piece —
 *  the caller can show feedback ("no pieces split"). */
export interface AddSeamResult {
  pieces: Piece[];
  splitCount: number;
  unchanged: boolean;
}

/** Split every piece in the target zone that crosses the requested
 *  seam line. Pieces that don't cross the line are returned
 *  unchanged (referential equality preserved, so React skips them).
 *  Pieces too thin to split safely (residual < MIN_SPLIT_REMAINDER_MM)
 *  are left intact.
 *
 *  New piece IDs are derived from the original by appending an
 *  axis tag ("_L"/"_R" for vertical, "_B"/"_T" for horizontal) so
 *  the canvas can still address the new pieces individually.
 */
export function addSeamAndSplit(
  pieces: Piece[],
  orientation: Orientation,
  position: number,
  zone_id: string,
): AddSeamResult {
  let splitCount = 0;
  const next = pieces.flatMap((p) => {
    if (p.zone_id !== zone_id) return [p];
    if (orientation === "vertical") {
      const xL = p.nominal_x_mm;
      const xR = xL + p.nominal_width_mm;
      const leftWidth = position - xL;
      const rightWidth = xR - position;
      if (
        leftWidth >= MIN_SPLIT_REMAINDER_MM
        && rightWidth >= MIN_SPLIT_REMAINDER_MM
      ) {
        splitCount++;
        const left: Piece = {
          ...p,
          piece_id: `${p.piece_id}_L`,
          nominal_width_mm: leftWidth,
          polygon: rectPolygon(xL, p.nominal_y_mm, leftWidth, p.nominal_height_mm),
        };
        const right: Piece = {
          ...p,
          piece_id: `${p.piece_id}_R`,
          nominal_x_mm: position,
          nominal_width_mm: rightWidth,
          polygon: rectPolygon(position, p.nominal_y_mm, rightWidth, p.nominal_height_mm),
        };
        return [left, right];
      }
      return [p];
    }
    // horizontal
    const yB = p.nominal_y_mm;
    const yT = yB + p.nominal_height_mm;
    const bottomHeight = position - yB;
    const topHeight = yT - position;
    if (
      bottomHeight >= MIN_SPLIT_REMAINDER_MM
      && topHeight >= MIN_SPLIT_REMAINDER_MM
    ) {
      splitCount++;
      const bottom: Piece = {
        ...p,
        piece_id: `${p.piece_id}_B`,
        nominal_height_mm: bottomHeight,
        polygon: rectPolygon(p.nominal_x_mm, yB, p.nominal_width_mm, bottomHeight),
      };
      const top: Piece = {
        ...p,
        piece_id: `${p.piece_id}_T`,
        nominal_y_mm: position,
        nominal_height_mm: topHeight,
        polygon: rectPolygon(p.nominal_x_mm, position, p.nominal_width_mm, topHeight),
      };
      return [bottom, top];
    }
    return [p];
  });
  return {
    pieces: splitCount > 0 ? next : pieces,
    splitCount,
    unchanged: splitCount === 0,
  };
}

function rectPolygon(x: number, y: number, w: number, h: number): Point[] {
  return [
    [x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y],
  ];
}
