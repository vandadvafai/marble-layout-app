// Pure helpers for the plan annotation tools.
//
// Each function takes the current ``Plan`` and an edit description,
// returns a new ``Plan`` with the requested change applied. No
// React state, no DOM — same shape as ``lib/editing.ts`` for the
// seam editor.
//
// The functions never mutate the input; they shallow-clone the
// affected list and patch the touched object. Untouched lists keep
// referential identity so React's reconciliation can skip them.

import type {
  Column, Doorway, GuideLine, Plan, Point, Space,
} from "./types";

/** Generate a short unique-ish ID. Prefixed so the kind is visible
 *  in the property panel and the affected_ids list from the
 *  backend rule report.
 *
 *  Not cryptographically unique — just unique-enough across one
 *  editing session. crypto.randomUUID would be cleaner but isn't
 *  guaranteed in every test runner. */
export function newObjectId(prefix: string): string {
  const t = Date.now().toString(36);
  const r = Math.random().toString(36).slice(2, 7);
  return `${prefix}_${t}${r}`;
}

/** Minimum dimension (mm) below which a freshly-drawn object is
 *  treated as a click rather than a real drag — avoids creating
 *  zero-sized doorways/columns/guides by accident. */
export const MIN_DRAW_LENGTH_MM = 50;

// ---------------------------------------------------------------------------
// shape helpers
// ---------------------------------------------------------------------------

/** Build the rectangle's 4-corner polygon from two opposite corners.
 *  Wound counter-clockwise for consistency with the engine's
 *  ArchitecturalPlan.column polygons. */
export function rectanglePolygon(a: Point, b: Point): Point[] {
  const [x0, x1] = a[0] < b[0] ? [a[0], b[0]] : [b[0], a[0]];
  const [y0, y1] = a[1] < b[1] ? [a[1], b[1]] : [b[1], a[1]];
  return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
}

/** Axis-aligned bounding box of a polygon as [x_min, y_min, x_max, y_max]. */
export function polygonBbox(polygon: Point[]): [number, number, number, number] {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const [x, y] of polygon) {
    if (x < x0) x0 = x;
    if (y < y0) y0 = y;
    if (x > x1) x1 = x;
    if (y > y1) y1 = y;
  }
  return [x0, y0, x1, y1];
}

/** Euclidean length of a 2-point segment. */
export function segmentLength([a, b]: [Point, Point]): number {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  return Math.sqrt(dx * dx + dy * dy);
}

// ---------------------------------------------------------------------------
// add
// ---------------------------------------------------------------------------

export function addDoorway(plan: Plan, segment: [Point, Point]): Plan {
  const doorway: Doorway = {
    doorway_id: newObjectId("door"),
    segment,
    width_mm: segmentLength(segment),
    is_main_entrance: false,
  };
  return { ...plan, doorways: [...plan.doorways, doorway] };
}

export function addColumn(plan: Plan, polygon: Point[]): Plan {
  const column: Column = {
    column_id: newObjectId("col"),
    polygon,
  };
  return { ...plan, columns: [...plan.columns, column] };
}

export function addGuideLine(plan: Plan, segment: [Point, Point]): Plan {
  const guide: GuideLine = {
    guide_line_id: newObjectId("guide"),
    segment,
    priority: 0,
    name: "",
  };
  return { ...plan, guide_lines: [...plan.guide_lines, guide] };
}

// ---------------------------------------------------------------------------
// update
// ---------------------------------------------------------------------------

export function updateDoorway(
  plan: Plan, doorway_id: string, patch: Partial<Doorway>,
): Plan {
  return {
    ...plan,
    doorways: plan.doorways.map((d) =>
      d.doorway_id === doorway_id ? { ...d, ...patch } : d,
    ),
  };
}

export function updateColumn(
  plan: Plan, column_id: string, patch: Partial<Column>,
): Plan {
  return {
    ...plan,
    columns: plan.columns.map((c) =>
      c.column_id === column_id ? { ...c, ...patch } : c,
    ),
  };
}

export function updateGuideLine(
  plan: Plan, guide_line_id: string, patch: Partial<GuideLine>,
): Plan {
  return {
    ...plan,
    guide_lines: plan.guide_lines.map((g) =>
      g.guide_line_id === guide_line_id ? { ...g, ...patch } : g,
    ),
  };
}

// ---------------------------------------------------------------------------
// delete
// ---------------------------------------------------------------------------

export function deleteDoorway(plan: Plan, doorway_id: string): Plan {
  return {
    ...plan,
    doorways: plan.doorways.filter((d) => d.doorway_id !== doorway_id),
  };
}

export function deleteColumn(plan: Plan, column_id: string): Plan {
  return {
    ...plan,
    columns: plan.columns.filter((c) => c.column_id !== column_id),
  };
}

export function deleteGuideLine(plan: Plan, guide_line_id: string): Plan {
  return {
    ...plan,
    guide_lines: plan.guide_lines.filter((g) => g.guide_line_id !== guide_line_id),
  };
}

// ---------------------------------------------------------------------------
// lookup
// ---------------------------------------------------------------------------

export function findDoorway(plan: Plan, id: string): Doorway | undefined {
  return plan.doorways.find((d) => d.doorway_id === id);
}

export function findColumn(plan: Plan, id: string): Column | undefined {
  return plan.columns.find((c) => c.column_id === id);
}

export function findGuideLine(plan: Plan, id: string): GuideLine | undefined {
  return plan.guide_lines.find((g) => g.guide_line_id === id);
}

// ---------------------------------------------------------------------------
// empty-plan fallback
// ---------------------------------------------------------------------------

/** Empty plan for demos that don't have one on disk. Used as the
 *  ``editedPlan`` seed when the backend's GET /demo-layouts/...
 *  response omits ``plan``. */
export function emptyPlan(target_id: string): Plan {
  return {
    target_id,
    spaces: [] as Space[],
    doorways: [],
    columns: [],
    guide_lines: [],
  };
}
