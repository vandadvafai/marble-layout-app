// Workflow step gating + metadata for the 4-step wizard.
//
// Centralised so the stepper header and the App's "next step"
// button stay consistent. Each step has a (id, label, description,
// short caption) and a ``canReach`` predicate that takes the current
// session state and answers "would clicking this step right now do
// anything useful?". The predicates are pure so they can be reused
// by tests.

import type {
  CalibrationCounts, CalibrationRecord, CalibrationRecordsResponse,
  FinalizationState, InventoryInfo, Layout, WorkflowStep,
} from "./types";


export interface StepDescriptor {
  id: WorkflowStep;
  title: string;        // headline on the stepper
  caption: string;      // sub-text under the headline
  primaryAction: string; // label for "go to next step" button on this step
}

export const STEPS: readonly StepDescriptor[] = [
  {
    id: 1,
    title: "Upload Plan",
    caption: "DXF or sample",
    primaryAction: "Continue to editor",
  },
  {
    id: 2,
    title: "Edit & Validate",
    caption: "Seams, plan, pieces",
    primaryAction: "Finalize layout",
  },
  {
    id: 3,
    title: "Upload Slabs",
    caption: "Excel + photos",
    primaryAction: "Continue to assignment",
  },
  {
    id: 4,
    title: "Assign & Export",
    caption: "Pick slabs, export",
    primaryAction: "Export",
  },
] as const;


export interface StepGate {
  /** Has the user satisfied this step's prerequisite? Used to
   *  decide whether the stepper chip is clickable AND whether the
   *  step's primary action is enabled. */
  reached: boolean;
  /** Friendly explanation shown as tooltip / button title when the
   *  step isn't reachable yet. */
  blockedReason: string | null;
}

export interface GateInputs {
  layout: Layout | null;
  finalization: FinalizationState | null;
  /** True when the designer has uploaded a slab inventory in Step 3
   *  that parsed, validated, and produced at least one usable slab.
   *  The fallback (demo / real-export) inventory does NOT satisfy
   *  this — Step 4 explicitly requires the designer's own upload to
   *  avoid factory cut plans being produced from sample data.
   *
   *  For a calibration-tracked upload this ALSO means every slab is
   *  resolved (approved or rejected — see ``isInventoryReady``); a
   *  ``needs_review`` or ``missing_photo`` record blocks Step 4 just
   *  as much as an empty inventory would. */
  inventoryReady: boolean;
}

/** Per-status calibration counts, computed straight from the record
 *  list — never trust a separately-cached count alongside the
 *  records themselves, that's a second source of truth waiting to
 *  drift out of sync. */
export function calibrationCounts(
  records: CalibrationRecord[],
): CalibrationCounts {
  const counts: CalibrationCounts = {
    approved: 0, needs_review: 0, missing_photo: 0, rejected: 0,
  };
  for (const r of records) counts[r.calibration_status] += 1;
  return counts;
}

/** Single source of truth for "is the Step-3 inventory ready for
 *  Step 4", shared by the stepper gate and the Step-3 completion
 *  checkmark so the two can never disagree.
 *
 *  Calibration-tracked (uploaded) inventory: every slab must be
 *  resolved to APPROVED or REJECTED — a NEEDS_REVIEW or
 *  MISSING_PHOTO record blocks Step 4 outright — AND at least one
 *  slab must be approved (a project where every slab was rejected
 *  has nothing to assign).
 *
 *  Demo / env-override inventory (``calibration.active`` is false —
 *  it never went through the Step-3 upload endpoint, so there's no
 *  calibration concept to gate on): falls back to the pre-M4 check,
 *  at least one valid record. */
export function isInventoryReady(
  calibration: CalibrationRecordsResponse | null,
  inventoryInfo: InventoryInfo | null,
): boolean {
  if (calibration && calibration.active) {
    const c = calibration.counts;
    return c.approved > 0 && c.needs_review === 0 && c.missing_photo === 0;
  }
  return inventoryInfo !== null && inventoryInfo.valid_count > 0;
}

/** Slab counts + the no-photo list for the Step-3 "Inventory
 *  summary" card. Same rule as ``isInventoryReady``: once a
 *  calibration-tracked upload is active, these numbers must come
 *  from the LIVE calibration records, not the frozen ``/upload``
 *  response — otherwise the summary card can silently disagree with
 *  the Calibration panel and the Step-4 gate sitting right beneath
 *  it (e.g. after an approve/reject/replace-image action that
 *  happened after the initial upload). */
export function inventorySummaryDisplay(
  calibration: CalibrationRecordsResponse | null,
  summary: {
    valid_slabs: number;
    invalid_slabs: number;
    slabs_without_photos: string[];
  } | null,
): { validSlabs: number; invalidSlabs: number; slabsWithoutPhotos: string[] } {
  if (calibration && calibration.active) {
    const c = calibration.counts;
    return {
      validSlabs: c.approved,
      invalidSlabs: c.rejected + c.needs_review + c.missing_photo,
      slabsWithoutPhotos: calibration.records
        .filter((r) => r.calibration_status === "missing_photo")
        .map((r) => r.slab_id),
    };
  }
  return {
    validSlabs: summary?.valid_slabs ?? 0,
    invalidSlabs: summary?.invalid_slabs ?? 0,
    slabsWithoutPhotos: summary?.slabs_without_photos ?? [],
  };
}

/** Single source of truth for the Step-4 lock message. Surfaced as
 *  the chip tooltip AND the banner the App shows when the designer
 *  clicks the blocked chip. */
export const STEP4_BLOCKED_MESSAGE =
  "Please complete Step 3: upload and validate slabs before "
  + "assigning/exporting.";

/** Per-step reachability gates. Step 1 is always reachable; Step 2
 *  needs a loaded layout (which the sample picker / future upload
 *  provides); Step 3 needs a finalized layout from Step 2; Step 4
 *  needs the same finalization PLUS a successful Step-3 upload with
 *  at least one valid slab. */
export function gateForStep(
  step: WorkflowStep, inputs: GateInputs,
): StepGate {
  switch (step) {
    case 1:
      return { reached: true, blockedReason: null };
    case 2:
      return inputs.layout !== null
        ? { reached: true, blockedReason: null }
        : {
            reached: false,
            blockedReason: "Pick a sample plan or upload a DXF first.",
          };
    case 3:
      return inputs.finalization !== null
        ? { reached: true, blockedReason: null }
        : {
            reached: false,
            blockedReason: "Finalize the layout in Step 2 to continue.",
          };
    case 4:
      if (inputs.finalization === null) {
        return {
          reached: false,
          blockedReason: "Finalize the layout in Step 2 to continue.",
        };
      }
      if (!inputs.inventoryReady) {
        return { reached: false, blockedReason: STEP4_BLOCKED_MESSAGE };
      }
      return { reached: true, blockedReason: null };
  }
}
