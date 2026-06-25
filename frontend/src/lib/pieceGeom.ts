// Geometry helpers that derive **real cut dimensions** for a Piece
// directly from its polygon.
//
// Why this lives in its own module
// --------------------------------
// The Piece model carries TWO size representations:
//
//   * ``nominal_width_mm`` / ``nominal_height_mm``
//       The size of the working slab tile this piece WOULD HAVE if
//       the floor were infinite. Always equal to the grid's tile
//       size — same value for full tiles AND for edge clips.
//
//   * ``polygon``
//       The closed ring of the ACTUAL cut piece in mm. For a tile
//       that ran into the floor boundary or a hole, this is much
//       smaller than the nominal rect.
//
// The factory cut plan needs the second one. The first one is a
// designer-traceability field — useful for "which row/column did
// this strip come from?" but DISASTROUS if it ends up on a DXF or
// in the matcher's "does this slab cover the piece?" check.
//
// Every display + export + match site in the editor goes through
// the helpers in this module so the UI, the matcher request and
// the DXF export agree on a single source of truth: the polygon.

import type { Piece, Point } from "./types";


/** Polygon-derived bounding box + area for a piece. ``area_m2`` uses
 *  the signed shoelace formula so non-rectangular pieces (e.g. an
 *  L-shape produced by sliver absorption) report the right area
 *  rather than the over-estimating bbox area. */
export interface CutDims {
  width_mm: number;
  height_mm: number;
  area_m2: number;
}


/** Compute cut dimensions for a piece. Three sources, in order of
 *  preference:
 *
 *    1. Backend-supplied ``bounding_*`` + ``actual_area_m2`` —
 *       authoritative and matches every other backend consumer
 *       (validation, packing, exporters).
 *    2. Polygon shoelace — cheap and consistent for editor-mutated
 *       pieces whose backend bounds may be stale after a seam drag.
 *    3. Nominal rect — defensive fallback for a degraded payload
 *       where the polygon was dropped (every Piece from the engine
 *       ships with a polygon, so this branch shouldn't fire in
 *       practice).
 *
 *  Heuristic for picking 1 vs. 2: if the backend bounds disagree
 *  with the polygon bbox by more than a millimetre, the editor has
 *  mutated the piece (seam drag / seam add) and the polygon is the
 *  fresher source. */
export function cutDimsForPiece(piece: Piece): CutDims {
  const poly = piece.polygon;
  if (!poly || poly.length < 3) {
    if (piece.bounding_width_mm && piece.bounding_height_mm
        && piece.actual_area_m2) {
      return {
        width_mm: piece.bounding_width_mm,
        height_mm: piece.bounding_height_mm,
        area_m2: piece.actual_area_m2,
      };
    }
    return {
      width_mm: piece.nominal_width_mm,
      height_mm: piece.nominal_height_mm,
      area_m2: (piece.nominal_width_mm * piece.nominal_height_mm) / 1_000_000,
    };
  }
  const fromPoly = cutDimsForPolygon(poly);
  const bw = piece.bounding_width_mm;
  const bh = piece.bounding_height_mm;
  const ba = piece.actual_area_m2;
  if (
    bw !== undefined && bh !== undefined && ba !== undefined
    && Math.abs(bw - fromPoly.width_mm) < 1
    && Math.abs(bh - fromPoly.height_mm) < 1
  ) {
    // Backend bounds agree with the polygon — prefer them because
    // ``actual_area_m2`` is the engine's exact shoelace value
    // (important for L-shapes where the bbox area > polygon area).
    return { width_mm: bw, height_mm: bh, area_m2: ba };
  }
  return fromPoly;
}


/** Same as ``cutDimsForPiece`` but takes a raw polygon. Useful for
 *  callers that already have the ring (e.g. canvas hit-tests). */
export function cutDimsForPolygon(polygon: readonly Point[]): CutDims {
  let xmin = Infinity, ymin = Infinity, xmax = -Infinity, ymax = -Infinity;
  for (const [x, y] of polygon) {
    if (x < xmin) xmin = x;
    if (x > xmax) xmax = x;
    if (y < ymin) ymin = y;
    if (y > ymax) ymax = y;
  }
  const width_mm = xmax - xmin;
  const height_mm = ymax - ymin;
  return {
    width_mm,
    height_mm,
    area_m2: shoelaceArea(polygon) / 1_000_000,
  };
}


/** Signed shoelace area for a closed ring (last vertex == first).
 *  Takes the absolute value so winding order doesn't flip the sign. */
export function shoelaceArea(polygon: readonly Point[]): number {
  if (polygon.length < 3) return 0;
  let sum = 0;
  for (let i = 0; i < polygon.length - 1; i += 1) {
    const [x0, y0] = polygon[i];
    const [x1, y1] = polygon[i + 1];
    sum += x0 * y1 - x1 * y0;
  }
  // Close the ring even if the caller forgot the duplicated vertex.
  const [xL, yL] = polygon[polygon.length - 1];
  const [x0, y0] = polygon[0];
  if (xL !== x0 || yL !== y0) {
    sum += xL * y0 - x0 * yL;
  }
  return Math.abs(sum) * 0.5;
}


/** Axis-aligned bbox derived from a polygon. Cheap hit-test that
 *  the swap-drag uses to figure out which piece is under the
 *  cursor — for rectangular pieces this is exact; for L-shapes it
 *  is a small over-estimate which is fine for a UX hover hint. */
export function bboxFromPolygon(
  polygon: readonly Point[],
): { x0: number; y0: number; x1: number; y1: number } | null {
  if (!polygon || polygon.length < 3) return null;
  let xmin = Infinity, ymin = Infinity, xmax = -Infinity, ymax = -Infinity;
  for (const [x, y] of polygon) {
    if (x < xmin) xmin = x;
    if (x > xmax) xmax = x;
    if (y < ymin) ymin = y;
    if (y > ymax) ymax = y;
  }
  return { x0: xmin, y0: ymin, x1: xmax, y1: ymax };
}
