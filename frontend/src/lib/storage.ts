// Browser-local persistence for the editor's edited state.
//
// Foundation-milestone scope: one entry per demo_id, holding the
// committed pieces + plan + a savedAt timestamp. No multi-project
// support, no cross-device sync, no schema versioning beyond a
// defensive parse — we treat localStorage as a best-effort cache:
//   * if it's full → save silently fails (the editor keeps working
//     in-memory),
//   * if the cached payload doesn't parse / doesn't match the
//     current shape → we discard it and start from the pristine
//     state.
//
// Key naming: ``stonelayout:edits:<demo_id>``. The ``stonelayout:``
// prefix gives us a clean way to clear all keys in one sweep when
// a future schema change demands it.

import type {
  Assignments, FinalizationState, Piece, Plan, WorkflowStep,
} from "./types";

// 0.1.45 — bumped from "stonelayout:edits:" so old per-demo saves
// from before the completion-badge fix don't restore stale
// finalization / assignments that would make a "fresh" wizard look
// already-done. Old entries are left in localStorage; the browser
// will GC them when quota pressure kicks in.
const KEY_PREFIX = "stonelayout:v2:edits:";

export interface SavedState {
  demo_id: string;
  pieces: Piece[];
  plan: Plan;
  /** ISO timestamp of the save event. */
  savedAt: string;
  /** Step the designer was on at last save. Restored on next visit
   *  so the wizard resumes mid-flight rather than always landing
   *  on Step 1. Optional for backward compatibility. */
  currentStep?: WorkflowStep;
  /** Snapshot of pieces taken when the designer clicked "Finalize"
   *  in Step 2. Drives the Step-4 assignment surface. */
  finalization?: FinalizationState | null;
  /** Slab assignments built up in Step 4. */
  assignments?: Assignments;
  /** Designer override that lets a slab be assigned to multiple
   *  pieces simultaneously (Step 4). */
  allowDuplicateAssignments?: boolean;
  /** V1.2 — Advanced Factory Settings visibility. When true the
   *  manufacturing card is expanded and ``advancedManufacturingPolicy``
   *  drives the fit check + exports. When false the app uses the
   *  V1 exact/allow defaults regardless of what policy sits here. */
  advancedFactoryEnabled?: boolean;
  advancedManufacturingPolicy?: {
    blade_kerf_mm: number;
    edge_trim_mm: number;
    tolerance_mm: number;
    profile: "strict" | "standard" | "exact";
    exact_edge_action: "allow" | "warn" | "block";
    exact_edge_epsilon_mm: number;
  };
}

export function storageKey(demo_id: string): string {
  return `${KEY_PREFIX}${demo_id}`;
}

/** Read the saved state for a demo. Returns null when nothing has
 *  been saved, when localStorage is disabled, or when the cached
 *  payload doesn't pass a minimal shape check. */
export function loadSavedState(demo_id: string): SavedState | null {
  try {
    const raw = window.localStorage.getItem(storageKey(demo_id));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!isSavedStateShape(parsed)) return null;
    return parsed;
  } catch {
    return null;
  }
}

/** Persist the editor's current edited state. Silent no-op when
 *  localStorage isn't available (private windows, storage quota). */
export function saveState(state: SavedState): void {
  try {
    window.localStorage.setItem(storageKey(state.demo_id), JSON.stringify(state));
  } catch {
    /* ignore — in-memory state is still authoritative */
  }
}

/** Drop the saved entry for a demo. Used by "Reset edits" so that
 *  the next reload doesn't restore a discarded session. */
export function clearSavedState(demo_id: string): void {
  try {
    window.localStorage.removeItem(storageKey(demo_id));
  } catch {
    /* ignore */
  }
}

// ---------------------------------------------------------------------------
// shape guard — minimal: surface only the fields we actually rely on
// ---------------------------------------------------------------------------

function isSavedStateShape(v: unknown): v is SavedState {
  if (!v || typeof v !== "object") return false;
  const o = v as Record<string, unknown>;
  if (typeof o.demo_id !== "string") return false;
  if (!Array.isArray(o.pieces)) return false;
  if (typeof o.savedAt !== "string") return false;
  if (!o.plan || typeof o.plan !== "object") return false;
  // Spot-check one piece — if it has a string piece_id, assume the
  // rest of the shape is good. A more defensive check could
  // round-trip every field but the cost outweighs the upside for
  // a localStorage cache that the user can clear at any time.
  if (o.pieces.length > 0) {
    const p = o.pieces[0] as Record<string, unknown>;
    if (typeof p?.piece_id !== "string") return false;
  }
  return true;
}
