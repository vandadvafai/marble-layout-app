// Pure functions that mutate a piece set in response to seam-drag
// events. Kept pure (no React state, no DOM) so the canvas, the
// validation layer, and any future automated tooling all read the
// same source of truth.
//
// Convention: seam positions are absolute coordinates in engine mm.
// A "drag delta" is the displacement of the seam from its current
// position; the helper applies it, snaps it, clamps it to the
// seam's [min_position, max_position] bounds, and returns the
// updated piece array.

import { MIN_GAP_MM } from "./seams";
import type { Piece, Seam } from "./types";

/** Snap drags to this increment so designer adjustments stay tidy. */
export const SNAP_MM = 50;

/** Snap a raw mm position to the nearest SNAP_MM increment. */
export function snap(positionMm: number): number {
  return Math.round(positionMm / SNAP_MM) * SNAP_MM;
}

/** Clamp a position to the seam's drag bounds. */
export function clampToSeamBounds(seam: Seam, positionMm: number): number {
  return Math.max(
    seam.min_position,
    Math.min(seam.max_position, positionMm),
  );
}

/**
 * Compute the new seam position from a raw drag target. Snaps to
 * SNAP_MM, then clamps to the seam's [min_position, max_position]
 * range. The clamping happens after snapping so the user can land
 * exactly on the boundary instead of sliding to the next snap point.
 */
export function resolveTargetPosition(
  seam: Seam, rawTargetMm: number,
): number {
  return clampToSeamBounds(seam, snap(rawTargetMm));
}

/**
 * Move a seam to ``newPosition`` and return a new piece array with
 * the affected pieces' rectangles AND polygons adjusted to match.
 *
 * Vertical seam at x=X moving to x=X':
 *   * pieces on the left  (right edge was at X) → width += (X' - X)
 *   * pieces on the right (left edge was at X)  → x += (X' - X),
 *                                                  width -= (X' - X)
 *
 * Horizontal seam is the y-axis mirror.
 *
 * 0.1.42 perf: the polygon rebuild used to happen in a SECOND O(N)
 * walk (``rebuildAllRectanglePolygons``) at every pointermove
 * during a drag. We now fold the rebuild into this same pass and
 * ONLY touch affected pieces — untouched pieces are returned by
 * reference, which lets React skip rerendering their SVG nodes.
 */
export function applySeamMove(
  pieces: Piece[], seam: Seam, newPosition: number,
): Piece[] {
  const delta = newPosition - seam.position;
  if (delta === 0) return pieces;
  const leftSet = new Set(seam.piece_left_ids);
  const rightSet = new Set(seam.piece_right_ids);
  return pieces.map((p) => {
    if (seam.orientation === "vertical") {
      if (leftSet.has(p.piece_id)) {
        return withRebuiltPolygon({
          ...p, nominal_width_mm: p.nominal_width_mm + delta,
        });
      }
      if (rightSet.has(p.piece_id)) {
        return withRebuiltPolygon({
          ...p,
          nominal_x_mm: p.nominal_x_mm + delta,
          nominal_width_mm: p.nominal_width_mm - delta,
        });
      }
    } else {
      if (leftSet.has(p.piece_id)) {
        return withRebuiltPolygon({
          ...p, nominal_height_mm: p.nominal_height_mm + delta,
        });
      }
      if (rightSet.has(p.piece_id)) {
        return withRebuiltPolygon({
          ...p,
          nominal_y_mm: p.nominal_y_mm + delta,
          nominal_height_mm: p.nominal_height_mm - delta,
        });
      }
    }
    return p;
  });
}

/** Inline rectangle-polygon rebuild used by ``applySeamMove`` so
 *  callers don't need a second O(N) pass. */
function withRebuiltPolygon(p: Piece): Piece {
  const { nominal_x_mm: x, nominal_y_mm: y,
          nominal_width_mm: w, nominal_height_mm: h } = p;
  p.polygon = [
    [x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y],
  ];
  return p;
}

/**
 * Re-derive a piece's polygon from its current nominal rectangle.
 * Used after a drag so the SVG renders the new shape immediately
 * — the canvas can't wait for the backend round-trip to repaint.
 */
export function rebuildRectanglePolygon(piece: Piece): Piece {
  const { nominal_x_mm: x, nominal_y_mm: y,
          nominal_width_mm: w, nominal_height_mm: h } = piece;
  return {
    ...piece,
    polygon: [
      [x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y],
    ],
  };
}

/** Same, applied across an array. Returns a new array. */
export function rebuildAllRectanglePolygons(pieces: Piece[]): Piece[] {
  return pieces.map(rebuildRectanglePolygon);
}

/**
 * Sanity-check exposed for tests. Returns ``true`` if every piece is
 * at least MIN_GAP_MM wide and tall — used by the canvas's drag
 * loop as a cheap "is this drag safe to apply" predicate.
 */
export function allPiecesAboveMinGap(pieces: Piece[]): boolean {
  return pieces.every(
    (p) =>
      p.nominal_width_mm >= MIN_GAP_MM
      && p.nominal_height_mm >= MIN_GAP_MM,
  );
}
