// Per-piece risk classification used by the PiecesPanel sort.
//
// "Risk" here means: how much human attention does this piece
// deserve before fabrication? A piece below the 10 cm cuttable
// minimum is a critical risk — it can't be made and will halt the
// job. An edge piece needs a partial cut but is otherwise routine.
//
// The classifier combines two information sources:
//
//   * the piece's intrinsic flags (is_edge_piece, notes containing
//     "absorbed_sliver:", nominal dimensions vs. the grid tile),
//   * the latest validation result (per-piece is_below_min and
//     is_small from the backend rule report; per-seam crosses_doorways
//     to flag pieces sitting on bad seams).
//
// When ``validation`` is null we degrade gracefully — only the
// intrinsic checks fire. That means a freshly-loaded layout shows
// real risks (below 10 cm, oversized, edge) before the user even
// triggers validation, and the picture sharpens once R1/R2/R6
// classifications come back.
//
// Scoring policy (highest wins):
//
//   100  below 10 cm minimum (R1)         — cannot be cut
//    80  small piece below threshold      — designer attention
//    60  on a seam crossing a doorway     — R2 conflict
//    40  absorbed-sliver holder OR        — needs wider slab
//        nominal dim exceeds median tile     than the median tile
//    20  edge piece (partial cut required)
//     0  normal full tile
//
// Each risk also pushes a chip onto the row's badges so a designer
// scanning the panel sees WHY a piece sorted high without expanding it.

import type {
  InventoryMatchResponse, Layout, Piece, PieceMatchResult,
  ValidationResult,
} from "./types";

export type RiskLevel = "critical" | "high" | "medium" | "low" | "none";

export type RiskBadgeVariant = "critical" | "warn" | "info";

export interface RiskBadge {
  label: string;
  variant: RiskBadgeVariant;
}

export interface PieceRisk {
  piece_id: string;
  level: RiskLevel;
  /** Numeric score for sort comparisons — higher = riskier. */
  score: number;
  badges: RiskBadge[];
}

export interface PieceWithRisk {
  piece: Piece;
  risk: PieceRisk;
}

/** Below this nominal side, R1 fires and the piece can't be cut. */
const R1_THRESHOLD_MM = 100;
/** Tolerance when comparing nominal dimensions to grid tile size. */
const DIM_TOLERANCE_MM = 1;
/** Waste fraction above which a slab match is "high waste" — a
 *  third of the slab going to scrap is worth flagging. */
const HIGH_WASTE_THRESHOLD = 0.30;

export function computePieceRisk(
  piece: Piece,
  validation: ValidationResult | null,
  layout: Layout,
  inventoryMatch: PieceMatchResult | null = null,
): PieceRisk {
  const badges: RiskBadge[] = [];
  let score = 0;

  // 0) Inventory matching outcomes outrank everything else: a
  //    piece with no matching slab can't be fabricated at all, so
  //    it deserves the top of the list. High waste is the
  //    "yellow-light" tier just below.
  if (inventoryMatch) {
    if (inventoryMatch.status === "no_match") {
      badges.push({ label: "no slab match", variant: "critical" });
      score = Math.max(score, 200);
    } else {
      const best = inventoryMatch.candidates[0];
      if (best && best.waste_fraction >= HIGH_WASTE_THRESHOLD) {
        badges.push({
          label: `${Math.round(best.waste_fraction * 100)}% waste`,
          variant: "warn",
        });
        score = Math.max(score, 150);
      }
    }
  }

  // 1) Below R1 minimum. Read directly from the validation report
  //    when available; fall back to dimension check so a freshly
  //    loaded layout still surfaces critical risks.
  const pe = validation?.pieces.find((p) => p.piece_id === piece.piece_id);
  const isBelowMin = pe?.is_below_min
    || piece.nominal_width_mm < R1_THRESHOLD_MM
    || piece.nominal_height_mm < R1_THRESHOLD_MM;
  if (isBelowMin) {
    badges.push({ label: "< 10 cm", variant: "critical" });
    score = Math.max(score, 100);
  }

  // 2) Small piece (above R1 minimum but below the architectural
  //    plan's small-piece threshold). Only the backend knows the
  //    threshold; we wait for validation to surface this.
  if (!isBelowMin && pe?.is_small) {
    badges.push({ label: "small", variant: "warn" });
    score = Math.max(score, 80);
  }

  // 3) Piece sits on a seam that crosses a doorway. We look at the
  //    seam evaluations, then check whether the seam's piece pair
  //    includes this piece.
  const badSeams = validation?.seams.filter((s) => s.crosses_doorways.length > 0) ?? [];
  const onBadSeam = badSeams.some(
    (s) => s.piece_a_id === piece.piece_id || s.piece_b_id === piece.piece_id,
  );
  if (onBadSeam) {
    badges.push({ label: "seam in doorway", variant: "critical" });
    score = Math.max(score, 60);
  }

  // 4a) Absorbed-sliver holder — the piece swallowed a neighbour
  //    sliver, so its slab needs to be wider than the median tile.
  const isAbsorbed = (piece.notes || []).some((n) => n.startsWith("absorbed_sliver:"));
  if (isAbsorbed) {
    badges.push({ label: "absorbed sliver", variant: "warn" });
    score = Math.max(score, 40);
  }

  // 4b) Oversized: nominal dim exceeds the median grid tile. Same
  //    "needs a bigger slab" implication; distinct badge because
  //    not all oversized pieces have absorbed slivers.
  const tileW = layout.grid.tile_width_mm;
  const tileH = layout.grid.tile_height_mm;
  const oversized = piece.nominal_width_mm > tileW + DIM_TOLERANCE_MM
    || piece.nominal_height_mm > tileH + DIM_TOLERANCE_MM;
  if (oversized) {
    badges.push({ label: "oversized slab", variant: "warn" });
    score = Math.max(score, 40);
  }

  // 5) Edge piece — needs a partial cut. Routine but worth flagging.
  if (piece.is_edge_piece) {
    badges.push({ label: "edge", variant: "info" });
    score = Math.max(score, 20);
  }

  // 6) Otherwise: full tile. No badge; the row's neutral state.
  if (badges.length === 0 && piece.is_full_tile) {
    badges.push({ label: "full tile", variant: "info" });
  }

  return {
    piece_id: piece.piece_id,
    level: scoreToLevel(score),
    score,
    badges,
  };
}

/** Stable sort: highest risk first, ties broken alphabetically by
 *  piece_id so the order is deterministic when two pieces share a
 *  score (e.g. all the "full tile" rows). */
export function sortPiecesByRisk(
  pieces: Piece[],
  validation: ValidationResult | null,
  layout: Layout,
  inventoryMatch: InventoryMatchResponse | null = null,
): PieceWithRisk[] {
  const matchById = new Map<string, PieceMatchResult>();
  if (inventoryMatch) {
    for (const pm of inventoryMatch.pieces) {
      matchById.set(pm.piece_id, pm);
    }
  }
  return pieces
    .map((piece) => ({
      piece,
      risk: computePieceRisk(
        piece, validation, layout,
        matchById.get(piece.piece_id) ?? null,
      ),
    }))
    .sort((a, b) => {
      if (b.risk.score !== a.risk.score) return b.risk.score - a.risk.score;
      return a.piece.piece_id.localeCompare(b.piece.piece_id);
    });
}

function scoreToLevel(score: number): RiskLevel {
  if (score >= 150) return "critical";
  if (score >= 100) return "critical";
  if (score >= 80) return "high";
  if (score >= 40) return "medium";
  if (score >= 20) return "low";
  return "none";
}
