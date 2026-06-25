// Workflow step gating + metadata for the 4-step wizard.
//
// Centralised so the stepper header and the App's "next step"
// button stay consistent. Each step has a (id, label, description,
// short caption) and a ``canReach`` predicate that takes the current
// session state and answers "would clicking this step right now do
// anything useful?". The predicates are pure so they can be reused
// by tests.

import type {
  FinalizationState, Layout, WorkflowStep,
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
   *  avoid factory cut plans being produced from sample data. */
  inventoryReady: boolean;
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
