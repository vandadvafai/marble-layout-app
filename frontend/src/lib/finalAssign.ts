// Pure helpers for the Step-4 slab-assignment surface.
//
// Designers finalize the cut layout in Step 2, then in Step 4 they
// pick a stock slab for each piece. By default a slab can only be
// assigned to ONE piece — assigning the same slab to two pieces
// raises a "duplicate" status unless the designer flips the
// allow-duplicate toggle. All four UX states (unassigned / assigned
// / no_match / duplicate) are resolved here so the panel and any
// future export read from the same vocabulary.

import type {
  AssignmentStatus, Assignments, InventoryMatchResponse,
  PieceMatchResult,
} from "./types";


/** Inspect the current assignments table and return the set of
 *  slab_ids that are assigned to more than one piece. The set is
 *  empty when ``allowDuplicates`` is true — the conflict concept
 *  doesn't apply when duplicates are explicitly allowed. */
export function detectDuplicateSlabs(
  assignments: Assignments, allowDuplicates: boolean,
): Set<string> {
  if (allowDuplicates) return new Set();
  const counts = new Map<string, number>();
  for (const slab_id of Object.values(assignments)) {
    if (!slab_id) continue;
    counts.set(slab_id, (counts.get(slab_id) ?? 0) + 1);
  }
  const dupes = new Set<string>();
  for (const [slab_id, count] of counts) {
    if (count > 1) dupes.add(slab_id);
  }
  return dupes;
}

/** Resolve the assignment status for one piece. The matcher's
 *  per-piece result tells us whether ANY slab can cover the piece;
 *  combined with the current assignments table that gives us the
 *  five UX states.
 *
 *  Validity check (0.1.53 — manual swap milestone): when a piece
 *  has an assignment, we look up the slab in the matcher's
 *  per-piece candidate list. The Step-4 matcher request asks for
 *  the FULL inventory (top_k = max(32, valid_count)), so absence
 *  from the candidate list means "this slab cannot cover this
 *  piece" — i.e. a manual swap dropped a too-small slab on this
 *  piece. The chip widens to ``too_small`` and the export bar
 *  refuses to run until the swap is undone or rerouted.
 *
 *  ``duplicate`` wins over ``too_small`` when both apply — the
 *  duplicate is the more recoverable conflict (one of the two
 *  pieces is still legitimately holding it), so we surface that
 *  first. */
export function assignmentStatusFor(
  piece_id: string,
  assignments: Assignments,
  match: PieceMatchResult | null,
  duplicateSlabIds: Set<string>,
): AssignmentStatus {
  const assigned = assignments[piece_id];
  if (assigned) {
    if (duplicateSlabIds.has(assigned)) return "duplicate";
    if (match && !isSlabValidForPiece(assigned, match)) return "too_small";
    return "assigned";
  }
  if (match && match.status === "no_match") return "no_match";
  return "unassigned";
}

/** True when the assigned slab appears in the matcher's candidate
 *  list for this piece. Step 4 requests the full inventory so
 *  candidates ≡ "slabs that can cover this piece"; an assigned
 *  slab that is NOT in candidates is too small (or otherwise
 *  geometrically unfit). When the matcher response is unavailable
 *  (e.g. still in flight) we conservatively treat the assignment
 *  as valid — flagging unknown-as-invalid would flicker chips on
 *  every re-fetch. */
export function isSlabValidForPiece(
  slab_id: string, match: PieceMatchResult,
): boolean {
  if (!match.candidates || match.candidates.length === 0) return false;
  for (const c of match.candidates) {
    if (c.slab_id === slab_id) return true;
  }
  return false;
}

/** Human-readable label used in chips and dropdowns. Centralised so
 *  the panel and the toolbar speak the same vocabulary. */
export function assignmentStatusLabel(s: AssignmentStatus): string {
  return {
    unassigned: "unassigned",
    assigned: "assigned",
    no_match: "no matching slab",
    too_small: "slab too small",
    duplicate: "duplicate slab",
  }[s];
}

/** Apply a single (piece_id, slab_id | null) assignment, returning a
 *  new ``Assignments`` table. Passing ``null`` clears the row. */
export function setAssignment(
  current: Assignments, piece_id: string, slab_id: string | null,
): Assignments {
  const next: Assignments = { ...current };
  if (slab_id === null) {
    delete next[piece_id];
  } else {
    next[piece_id] = slab_id;
  }
  return next;
}

/** Swap the slab assignments of two pieces. Piece geometry,
 *  measurements, cut dimensions, and absorbed-sliver flags live on
 *  the ``Piece`` objects themselves and are NEVER touched by this
 *  helper — only the (piece_id → slab_id) edge moves. Either side
 *  may be unassigned (slab_id = undefined); in that case the
 *  opposite side becomes unassigned after the swap. */
export function swapAssignments(
  current: Assignments, piece_a: string, piece_b: string,
): Assignments {
  if (piece_a === piece_b) return current;
  const next: Assignments = { ...current };
  const a = next[piece_a] ?? null;
  const b = next[piece_b] ?? null;
  if (b !== null) next[piece_a] = b; else delete next[piece_a];
  if (a !== null) next[piece_b] = a; else delete next[piece_b];
  return next;
}

/** Drop assignments for piece_ids that no longer exist in the
 *  finalized snapshot. Used when re-finalizing so stale ids don't
 *  keep pointing into the assignments table. */
export function pruneAssignments(
  current: Assignments, piece_ids: Iterable<string>,
): Assignments {
  const keep = new Set(piece_ids);
  const next: Assignments = {};
  for (const [pid, slab_id] of Object.entries(current)) {
    if (keep.has(pid)) next[pid] = slab_id;
  }
  return next;
}

/** Auto-pick the best slab for every piece that doesn't already
 *  have an assignment. Returns a NEW Assignments table; the caller
 *  decides whether to install it via ``setAssignments(...)``.
 *
 *  Ranking per piece (highest first):
 *    1. Lowest ``waste_fraction``       — tighter fit.
 *    2. Candidates that have an ``image_path`` are preferred over
 *       those without (so the canvas + properties surface has
 *       something visual to show).
 *    3. Avoid reusing slab_ids that are already taken by another
 *       piece in the same pass (unless ``allowDuplicates`` is on).
 *       The "taken" set is built up as we walk pieces so the first
 *       piece gets its best slab and later pieces fall back to
 *       their next-best.
 *
 *  Pieces with no candidates (matcher status ``no_match``) are
 *  skipped; the UI continues to flag them as "no slab match".
 *  Existing assignments are NEVER overwritten — call
 *  ``clearAssignments`` first if you want a fresh sweep. */
export function autoAssignBestSlabs(
  current: Assignments,
  match: InventoryMatchResponse | null,
  allowDuplicates: boolean = false,
): Assignments {
  if (!match) return current;
  const next: Assignments = { ...current };
  const taken = new Set<string>();
  if (!allowDuplicates) {
    for (const v of Object.values(next)) {
      if (v) taken.add(v);
    }
  }

  // Process pieces in matcher-response order so the result is
  // deterministic. Within each piece's candidate list we re-sort
  // by our preference function — the matcher already orders by
  // waste, but we want image-bearing ties to win.
  for (const pm of match.pieces) {
    if (next[pm.piece_id]) continue;            // keep existing
    if (pm.candidates.length === 0) continue;   // no_match
    const ranked = [...pm.candidates].sort((a, b) => {
      // (1) tighter fit first
      if (a.waste_fraction !== b.waste_fraction) {
        return a.waste_fraction - b.waste_fraction;
      }
      // (2) image-bearing slab wins the tie
      const aHasImg = a.image_path ? 1 : 0;
      const bHasImg = b.image_path ? 1 : 0;
      if (aHasImg !== bHasImg) return bHasImg - aHasImg;
      // (3) stable by slab_id so the same input always yields the
      // same output (handy for tests + screenshots).
      return a.slab_id.localeCompare(b.slab_id);
    });
    const chosen = ranked.find((c) =>
      allowDuplicates || !taken.has(c.slab_id),
    );
    if (!chosen) continue;  // every candidate already taken
    next[pm.piece_id] = chosen.slab_id;
    if (!allowDuplicates) taken.add(chosen.slab_id);
  }
  return next;
}


/** Drop every assignment in one shot. Useful for "auto-assign
 *  again from scratch" — pair with autoAssignBestSlabs to overwrite. */
export function clearAssignments(): Assignments {
  return {};
}


/** Per-status counts the Step-4 stepper + assignment header use to
 *  show "5 of 10 assigned · 1 conflict". */
export function summarizeAssignments(
  piece_ids: string[],
  assignments: Assignments,
  match: InventoryMatchResponse | null,
  allowDuplicates: boolean,
): {
  total: number;
  assigned: number;
  unassigned: number;
  no_match: number;
  too_small: number;
  duplicate: number;
} {
  const matchById = new Map<string, PieceMatchResult>();
  if (match) for (const pm of match.pieces) matchById.set(pm.piece_id, pm);
  const dupes = detectDuplicateSlabs(assignments, allowDuplicates);
  let assigned = 0, unassigned = 0, no_match = 0, too_small = 0, duplicate = 0;
  for (const pid of piece_ids) {
    const s = assignmentStatusFor(
      pid, assignments, matchById.get(pid) ?? null, dupes,
    );
    if (s === "assigned") assigned += 1;
    else if (s === "duplicate") duplicate += 1;
    else if (s === "no_match") no_match += 1;
    else if (s === "too_small") too_small += 1;
    else unassigned += 1;
  }
  return {
    total: piece_ids.length,
    assigned, unassigned, no_match, too_small, duplicate,
  };
}
