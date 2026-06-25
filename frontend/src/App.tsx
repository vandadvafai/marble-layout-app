// App shell — combines seam editing + plan annotation + undo/redo
// + the new "+ Seam" tool.
//
// State model:
//   * Pristine state from the backend (originalPieces / originalPlan)
//     — only used by "Reset edits".
//   * Committed editor history via useEditorHistory — pieces +
//     plan move through `history.commit(...)` on discrete events
//     (drag end, add/edit/delete, add-seam). Undo/redo step
//     through this history.
//   * Live drag preview (previewPieces) — bypasses history so
//     intermediate seam-drag frames don't pollute undo and don't
//     fire one validation per frame. Cleared on drag end (the
//     final state is what gets committed).
//
// Keyboard shortcuts (Cmd/Ctrl+Z, Cmd/Ctrl+Shift+Z) are bound at
// the window level so they work whether the toolbar buttons have
// focus or not; we skip the shortcut when the user is typing in
// an input field (don't steal the browser's text-undo).

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import LayoutCanvas from "./components/LayoutCanvas";
import ModeToolbar from "./components/ModeToolbar";
import PiecesPanel from "./components/PiecesPanel";
import PropertiesPanel from "./components/PropertiesPanel";
import SelectionProperties from "./components/SelectionProperties";
import Step1PlanPanel from "./components/Step1PlanPanel";
import Step3InventoryPanel from "./components/Step3InventoryPanel";
import Step4ExportBar from "./components/Step4ExportBar";
import ExportingOverlay from "./components/ExportingOverlay";
import HelpModal from "./components/HelpModal";
import StepperHeader from "./components/StepperHeader";
import ValidationSummary from "./components/ValidationSummary";
import {
  clearUploadedInventory, fetchCurrentInventory, fetchDemoLayout,
  fetchInventoryInfo, listDemos, postMatchInventory, postValidateLayout,
  regenerateLayout,
} from "./lib/api";
import {
  applySeamMove, rebuildAllRectanglePolygons, resolveTargetPosition,
} from "./lib/editing";
import { useEditorHistory } from "./lib/editorHistory";
import {
  addColumn, addDoorway, addGuideLine,
  deleteColumn, deleteDoorway, deleteGuideLine,
  emptyPlan,
  updateColumn, updateDoorway, updateGuideLine,
} from "./lib/planEdits";
import {
  autoAssignBestSlabs, pruneAssignments, setAssignment,
  summarizeAssignments, swapAssignments,
} from "./lib/finalAssign";
import {
  exportClientPng, exportFactoryDxf,
} from "./lib/exportLayout";
import {
  getAssignedImageRefs, useSlabImagesReady,
} from "./lib/imageReadiness";
import { addSeamAndSplit } from "./lib/seamAdd";
import { deriveSeams } from "./lib/seams";
import {
  clearSavedState, loadSavedState, saveState,
} from "./lib/storage";
import type {
  Assignments, Column, DemoIndexEntry, DemoLayoutResponse, Doorway,
  EditorMode, FinalizationState, GuideLine, InventoryInfo,
  InventoryMatchResponse, InventoryUploadSummary, Piece, Plan, Point,
  Selection, ValidationResult, WorkflowStep,
} from "./lib/types";
import { gateForStep } from "./lib/workflow";

const INITIAL_DEMO = "l_shape";
const VALIDATION_DEBOUNCE_MS = 200;
const SAVE_DEBOUNCE_MS = 500;

const EMPTY_INITIAL: { pieces: Piece[]; plan: Plan } = {
  pieces: [],
  plan: emptyPlan(""),
};

export default function App() {
  const [demos, setDemos] = useState<DemoIndexEntry[]>([]);
  const [demoId, setDemoId] = useState(INITIAL_DEMO);
  const [data, setData] = useState<DemoLayoutResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Pristine state retained for "Reset edits".
  const [originalPieces, setOriginalPieces] = useState<Piece[]>([]);
  const [originalPlan, setOriginalPlan] = useState<Plan | null>(null);

  // Committed editor state with undo/redo.
  const history = useEditorHistory(EMPTY_INITIAL);
  const editedPieces = history.pieces;
  const editedPlan = history.plan;

  // Live seam-drag preview — overrides history.pieces visually
  // without polluting the undo stack. Cleared on drag end.
  const [previewPieces, setPreviewPieces] = useState<Piece[] | null>(null);
  const displayedPieces = previewPieces ?? editedPieces;

  // Tool + selection.
  // The editor boots into Select Pieces — that's the canonical
  // "what am I looking at" mode. After any creation action
  // (add doorway / column / guide / seam) we drop back to this
  // mode, not edit_seam, so the designer doesn't get stuck in
  // seam-drag mode without realising it.
  const [mode, setMode] = useState<EditorMode>("select_piece");
  const [selection, setSelection] = useState<Selection | null>(null);

  // Validation.
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [isValidating, setIsValidating] = useState(false);

  // Inventory matching (read-only preview, separate request from
  // validation so a failure on one side doesn't poison the other).
  const [inventoryMatch, setInventoryMatch] = useState<InventoryMatchResponse | null>(null);
  const matchReqTokenRef = useRef(0);

  // Resolved inventory source (path / label / counts). Fetched ONCE
  // on boot — the backend reads the same env vars on every request,
  // so this won't change without a server restart. The panel header
  // uses this to show "Inventory: real project export · 8 valid" so
  // designers know which file is feeding the matcher BEFORE the
  // first match call.
  const [inventoryInfo, setInventoryInfo] = useState<InventoryInfo | null>(null);
  const [inventoryInfoError, setInventoryInfoError] = useState<string | null>(null);

  // Step-3 upload state. Restored from /api/inventory/current on
  // boot so a page refresh while the server is still running keeps
  // showing the upload summary in the Step-3 panel. Cleared (along
  // with the server-side session) by the panel's "Remove upload"
  // button.
  const [uploadSummary, setUploadSummary] = useState<InventoryUploadSummary | null>(null);
  const [uploadExcelName, setUploadExcelName] = useState<string | null>(null);
  const [uploadImageCount, setUploadImageCount] = useState<number>(0);

  // 0.1.44 — current working slab size + how it was chosen.
  // Populated when the user clicks "Generate layout from inventory
  // size" in Step 3. Surfaced in the canvas toolbar so the designer
  // always knows what tile size their seam grid is based on. Null
  // until the first regeneration; the canvas then falls back to the
  // layout's tile dims for display.
  const [tileChoice, setTileChoice] = useState<
    { tile_width_mm: number; tile_height_mm: number;
      basis: "explicit_override" | "inventory_median" } | null
  >(null);

  // Mode switch with selection cleanup (0.1.41).
  //
  // Switching mode also clears any selection that the new mode
  // wouldn't be able to display. Spec: "The selected mode controls
  // what is displayed. Never auto-switch because of what was
  // clicked." So:
  //   * Entering select_piece → drop any seam selection.
  //   * Entering edit_seam    → drop any piece selection.
  //   * Entering a creation   → drop everything (the designer is
  //     no longer interacting with the existing canvas).
  //
  // Plan annotations (doorway / column / guide_line) stay
  // selectable in either selection mode, so their selection
  // survives the switch.
  const onChangeMode = useCallback((next: EditorMode) => {
    setMode(next);
    setSelection((cur) => {
      if (cur === null) return cur;
      if (next === "select_piece") {
        return cur.kind === "seam" ? null : cur;
      }
      if (next === "edit_seam") {
        return cur.kind === "piece" ? null : cur;
      }
      return null;
    });
  }, []);

  // --- Workflow (4-step wizard, 0.1.39 milestone) -------------------------
  // ``currentStep`` drives which view is rendered. Steps 1 + 3 are
  // upload shells (no canvas); Step 2 is the existing editor; Step
  // 4 is the assignment + export surface. ``finalization`` is the
  // pieces snapshot the designer takes at the end of Step 2 — it
  // freezes the cut layout so the Step-4 surface doesn't mutate
  // as a side-effect of going back to Step 2 to fiddle. Assignments
  // are stored as piece_id → slab_id.
  const [currentStep, setCurrentStep] = useState<WorkflowStep>(1);
  // 0.1.51 — bilingual help modal. Closed by default; opened from
  // the header button. The modal owns its own language toggle so
  // the rest of the app stays unilingual.
  const [helpOpen, setHelpOpen] = useState(false);
  // 0.1.52 — global "generating PNG…" overlay. Promoted to App
  // state so the overlay can sit over the whole app, not just the
  // export bar. The bar still keeps its own "exporting…" button
  // label via the Promise returned by ``onExportPng``.
  const [pngExporting, setPngExporting] = useState(false);
  const [finalization, setFinalization] = useState<FinalizationState | null>(null);
  const [assignments, setAssignments] = useState<Assignments>({});
  const [allowDuplicateAssignments, setAllowDuplicateAssignments] = useState(false);
  // Manual swap mode (Step 4): when ON, clicking a piece on the canvas
  // starts a swap-drag whose drop target swaps the two pieces'
  // assignments. Geometry / cuts are NEVER mutated by a swap —
  // see ``onSwapAssignments`` below.
  const [swapMode, setSwapMode] = useState(false);
  // Step-lock message banner. Set when the designer clicks a chip
  // whose prerequisites aren't met (typically Step 4 before Step 3
  // is complete); cleared automatically after a few seconds or on
  // the next successful step change.
  const [stepBlockedMessage, setStepBlockedMessage] = useState<string | null>(null);
  const stepBlockedTimerRef = useRef<number | null>(null);

  // localStorage save metadata.
  const [lastSavedAt, setLastSavedAt] = useState<string | null>(null);

  const isEdited = useMemo(
    () => editedPieces !== originalPieces || editedPlan !== originalPlan,
    [editedPieces, originalPieces, editedPlan, originalPlan],
  );

  // --- demo index (once) ---------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    listDemos()
      .then((res) => { if (!cancelled) setDemos(res.demos); })
      .catch((e) => {
        if (!cancelled) setError(`Failed to load demo index: ${e.message}`);
      });
    return () => { cancelled = true; };
  }, []);

  // --- inventory source info (once) ---------------------------------------
  // Fetched at the same time as the demo index. The server resolves
  // the inventory file on every request, so the result is stable
  // until the server restarts — no need to refetch on demo changes.
  // Step-3 upload mutations re-call ``onInventoryInfoChange`` to push
  // a fresh copy in.
  useEffect(() => {
    let cancelled = false;
    fetchInventoryInfo()
      .then((info) => { if (!cancelled) setInventoryInfo(info); })
      .catch((e) => {
        if (!cancelled) {
          setInventoryInfoError(
            `Inventory not loaded: ${e.message}`,
          );
        }
      });
    // 0.1.43 — pull the active upload session so the Step-3 panel
    // can restore its preview after a refresh while the server is
    // still running. No-op when no upload is active.
    fetchCurrentInventory()
      .then((cur) => {
        if (cancelled) return;
        if (cur.active && cur.summary) {
          setUploadSummary(cur.summary);
          setUploadExcelName(cur.excel_filename ?? null);
          setUploadImageCount(cur.image_count ?? 0);
        }
      })
      .catch(() => {
        // Non-critical — the fallback inventory is fine.
      });
    return () => { cancelled = true; };
  }, []);

  // --- re-fetch on demo change --------------------------------------------
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setValidation(null);
    setValidationError(null);
    setInventoryMatch(null);
    setSelection(null);
    setPreviewPieces(null);
    setFinalization(null);
    setAssignments({});
    setAllowDuplicateAssignments(false);
    setTileChoice(null);
    fetchDemoLayout(demoId)
      .then((res) => {
        if (cancelled) return;
        setData(res);
        const seedPlan = res.plan ?? emptyPlan(res.layout.target.target_id);
        setOriginalPieces(res.layout.pieces);
        setOriginalPlan(seedPlan);
        // Restore from localStorage when a saved session exists for
        // this demo — otherwise fall back to the pristine pieces.
        const saved = loadSavedState(res.demo_id);
        if (saved) {
          history.reset({ pieces: saved.pieces, plan: saved.plan });
          setLastSavedAt(saved.savedAt);
          if (saved.finalization) setFinalization(saved.finalization);
          if (saved.assignments) setAssignments(saved.assignments);
          if (saved.allowDuplicateAssignments) {
            setAllowDuplicateAssignments(true);
          }
          // Resume on whatever step was last active — but only when
          // it's still reachable given the current state. If the
          // user finalized last time but the snapshot didn't load,
          // we'd land on Step 4 with no pieces, which is broken.
          if (saved.currentStep) {
            const wantedStep = saved.currentStep;
            const hasFinal = saved.finalization != null;
            if (
              wantedStep === 1 ||
              wantedStep === 2 ||
              (wantedStep === 3 && hasFinal) ||
              (wantedStep === 4 && hasFinal)
            ) {
              setCurrentStep(wantedStep);
            } else {
              setCurrentStep(2);
            }
          } else {
            setCurrentStep(2);
          }
        } else {
          history.reset({ pieces: res.layout.pieces, plan: seedPlan });
          setLastSavedAt(null);
          // Brand-new demo → start on Step 2 (the demo seed IS the
          // loaded plan; no upload needed).
          setCurrentStep(2);
        }
      })
      .catch((e) => { if (!cancelled) setError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
    // history.reset is stable (useCallback in useEditorHistory).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [demoId]);

  // --- debounced validation ------------------------------------------------
  const reqTokenRef = useRef(0);
  const debounceRef = useRef<number | null>(null);

  const runValidation = useCallback(async () => {
    if (!data || !editedPlan) return;
    const token = ++reqTokenRef.current;
    setIsValidating(true);
    setValidationError(null);
    try {
      const piecesWithPolygons = rebuildAllRectanglePolygons(editedPieces);
      const result = await postValidateLayout(
        data.demo_id, piecesWithPolygons, editedPlan,
      );
      if (token !== reqTokenRef.current) return;
      setValidation(result);
    } catch (e) {
      if (token !== reqTokenRef.current) return;
      setValidationError((e as Error).message);
    } finally {
      if (token === reqTokenRef.current) setIsValidating(false);
    }
  }, [data, editedPieces, editedPlan]);

  // Re-validate when the COMMITTED state changes (not the preview).
  // Debounced so numeric-input flurries get batched.
  useEffect(() => {
    if (!data) return;
    if (debounceRef.current !== null) {
      window.clearTimeout(debounceRef.current);
    }
    debounceRef.current = window.setTimeout(() => {
      runValidation();
    }, VALIDATION_DEBOUNCE_MS);
    return () => {
      if (debounceRef.current !== null) {
        window.clearTimeout(debounceRef.current);
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editedPieces, editedPlan, data]);

  // Inventory matching runs in parallel with validation. Plan
  // edits don't change which slabs a piece needs (matching is
  // purely dimensional), so we only re-fetch when the pieces
  // themselves change. Same debounce window so a single edit
  // produces one validate + one match call, not interleaved
  // network traffic.
  const matchDebounceRef = useRef<number | null>(null);
  const runMatch = useCallback(async () => {
    if (!data) return;
    const token = ++matchReqTokenRef.current;
    try {
      const result = await postMatchInventory(data.demo_id, editedPieces);
      if (token !== matchReqTokenRef.current) return;
      setInventoryMatch(result);
    } catch {
      // Match failures are non-blocking — the editor still works
      // without the slab preview. Leave the previous result in
      // place so the panel keeps showing useful info.
    }
  }, [data, editedPieces]);

  useEffect(() => {
    if (!data) return;
    if (matchDebounceRef.current !== null) {
      window.clearTimeout(matchDebounceRef.current);
    }
    matchDebounceRef.current = window.setTimeout(() => {
      runMatch();
    }, VALIDATION_DEBOUNCE_MS);
    return () => {
      if (matchDebounceRef.current !== null) {
        window.clearTimeout(matchDebounceRef.current);
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editedPieces, data]);

  // --- debounced localStorage save (only when edited) ----------------------
  const saveTimerRef = useRef<number | null>(null);
  useEffect(() => {
    if (!data) return;
    const hasWorkflowState =
      finalization !== null
      || Object.keys(assignments).length > 0
      || currentStep !== 2;
    if (!isEdited && !hasWorkflowState) return;
    if (saveTimerRef.current !== null) {
      window.clearTimeout(saveTimerRef.current);
    }
    saveTimerRef.current = window.setTimeout(() => {
      const savedAt = new Date().toISOString();
      saveState({
        demo_id: data.demo_id,
        pieces: editedPieces,
        plan: editedPlan,
        savedAt,
        currentStep,
        finalization,
        assignments,
        allowDuplicateAssignments,
      });
      setLastSavedAt(savedAt);
    }, SAVE_DEBOUNCE_MS);
    return () => {
      if (saveTimerRef.current !== null) {
        window.clearTimeout(saveTimerRef.current);
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    data, isEdited, editedPieces, editedPlan,
    currentStep, finalization, assignments, allowDuplicateAssignments,
  ]);

  // --- keyboard shortcuts: Cmd/Ctrl+Z, Cmd/Ctrl+Shift+Z --------------------
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Don't steal the browser's text-undo when the user is in
      // an input field.
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;

      const cmd = e.metaKey || e.ctrlKey;
      if (!cmd) return;
      if (e.key.toLowerCase() !== "z") return;
      e.preventDefault();
      if (e.shiftKey) history.redo();
      else history.undo();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [history.undo, history.redo]);

  // --- handlers ------------------------------------------------------------

  // Seam drag — live preview, then commit on release.
  //
  // 0.1.42 perf: the canvas can emit pointermove at 60–120 Hz. We
  // coalesce updates with requestAnimationFrame so only one preview
  // update lands per frame, and we read the LATEST pieces snapshot
  // at flush time so we don't pay the React state churn on every
  // event. The polygon rebuild used to happen here too (a second
  // O(N) walk); it now lives inside ``applySeamMove`` which only
  // touches affected pieces, so this handler just buffers.
  const dragPreviewRef = useRef<Piece[] | null>(null);
  const dragRafRef = useRef<number | null>(null);

  const onSeamDragMove = useCallback((next: Piece[]) => {
    dragPreviewRef.current = next;
    if (dragRafRef.current !== null) return;
    dragRafRef.current = window.requestAnimationFrame(() => {
      dragRafRef.current = null;
      const pending = dragPreviewRef.current;
      if (pending) setPreviewPieces(pending);
    });
  }, []);

  const onSeamDragEnd = useCallback(() => {
    // Flush any pending RAF before commit so the committed state
    // matches what the designer last saw on screen.
    if (dragRafRef.current !== null) {
      window.cancelAnimationFrame(dragRafRef.current);
      dragRafRef.current = null;
    }
    const finalPieces = dragPreviewRef.current ?? previewPieces;
    dragPreviewRef.current = null;
    if (finalPieces === null) return;
    history.commit({ pieces: finalPieces, plan: editedPlan });
    setPreviewPieces(null);
  }, [history, previewPieces, editedPlan]);

  // Plan-object adds.
  const onAddDoorway = useCallback((segment: [Point, Point]) => {
    history.commit({
      pieces: editedPieces,
      plan: addDoorway(editedPlan, segment),
    });
    setMode("select_piece");
  }, [history, editedPieces, editedPlan]);

  const onAddColumn = useCallback((polygon: Point[]) => {
    history.commit({
      pieces: editedPieces,
      plan: addColumn(editedPlan, polygon),
    });
    setMode("select_piece");
  }, [history, editedPieces, editedPlan]);

  const onAddGuideLine = useCallback((segment: [Point, Point]) => {
    history.commit({
      pieces: editedPieces,
      plan: addGuideLine(editedPlan, segment),
    });
    setMode("select_piece");
  }, [history, editedPieces, editedPlan]);

  // Add-seam — splits pieces in the target zone.
  const onAddSeam = useCallback(
    (args: {
      orientation: "vertical" | "horizontal";
      position: number;
      zone_id: string;
    }) => {
      const result = addSeamAndSplit(
        editedPieces, args.orientation, args.position, args.zone_id,
      );
      if (result.unchanged) {
        // No pieces could be split at that position — leave history
        // untouched and bounce out of seam-add mode so the designer
        // doesn't keep dragging at the same spot. A toast or
        // inline hint is a TODO for the next styling pass.
        setMode("select_piece");
        return;
      }
      history.commit({ pieces: result.pieces, plan: editedPlan });
      setMode("select_piece");
      setSelection(null);
    },
    [history, editedPieces, editedPlan],
  );

  // Plan-object patches.
  const onPatchDoorway = useCallback(
    (id: string, patch: Partial<Doorway>) => {
      history.commit({
        pieces: editedPieces,
        plan: updateDoorway(editedPlan, id, patch),
      });
    },
    [history, editedPieces, editedPlan],
  );
  const onPatchColumn = useCallback(
    (id: string, patch: Partial<Column>) => {
      history.commit({
        pieces: editedPieces,
        plan: updateColumn(editedPlan, id, patch),
      });
    },
    [history, editedPieces, editedPlan],
  );
  const onPatchGuideLine = useCallback(
    (id: string, patch: Partial<GuideLine>) => {
      history.commit({
        pieces: editedPieces,
        plan: updateGuideLine(editedPlan, id, patch),
      });
    },
    [history, editedPieces, editedPlan],
  );

  const onDeleteSelection = useCallback((sel: Selection) => {
    const nextPlan = (() => {
      switch (sel.kind) {
        case "doorway":    return deleteDoorway(editedPlan, sel.id);
        case "column":     return deleteColumn(editedPlan, sel.id);
        case "guide_line": return deleteGuideLine(editedPlan, sel.id);
        default:           return editedPlan;
      }
    })();
    if (nextPlan === editedPlan) return;
    history.commit({ pieces: editedPieces, plan: nextPlan });
    setSelection(null);
  }, [history, editedPieces, editedPlan]);

  const onResetEdits = useCallback(() => {
    if (!originalPlan) return;
    history.reset({ pieces: originalPieces, plan: originalPlan });
    setPreviewPieces(null);
    setValidation(null);
    setValidationError(null);
    setInventoryMatch(null);
    setSelection(null);
    // Reset also wipes any Step-3/4 progress — going back to a
    // clean editor with a stale finalized snapshot would confuse
    // the workflow gates.
    setFinalization(null);
    setAssignments({});
    setAllowDuplicateAssignments(false);
    setCurrentStep(2);
    if (data) clearSavedState(data.demo_id);
    setLastSavedAt(null);
  }, [history, originalPieces, originalPlan, data]);

  // 0.1.45 — "Start new project" wipes the in-session wizard state
  // AND the server-side uploaded inventory so the next plan starts
  // truly clean. Distinct from ``onResetEdits``:
  //   * onResetEdits keeps the loaded demo + drops the localStorage
  //     entry for it, landing the user back on Step 2.
  //   * onStartNewProject ALSO clears the upload session on the
  //     backend AND lands the user back on Step 1.
  // ``hasUserState`` decides whether a confirmation prompt is
  // needed; clicking the button on a pristine wizard skips the
  // alert and just snaps to Step 1.
  const hasUserState = useMemo(() => {
    return isEdited
      || finalization !== null
      || Object.keys(assignments).length > 0
      || tileChoice !== null
      || uploadSummary !== null;
  }, [
    isEdited, finalization, assignments, tileChoice, uploadSummary,
  ]);

  const performHardReset = useCallback(async () => {
    // Wipe the React state for the wizard.
    if (originalPlan) {
      history.reset({ pieces: originalPieces, plan: originalPlan });
    }
    setPreviewPieces(null);
    setValidation(null);
    setValidationError(null);
    setInventoryMatch(null);
    setSelection(null);
    setFinalization(null);
    setAssignments({});
    setAllowDuplicateAssignments(false);
    setTileChoice(null);
    setUploadSummary(null);
    setUploadExcelName(null);
    setUploadImageCount(0);
    setCurrentStep(1);
    setLastSavedAt(null);
    if (data) clearSavedState(data.demo_id);
    // Drop the matcher refetch cache so re-entering Step 4 later
    // forces a fresh request against whatever inventory is active
    // at that point.
    lastMatchedFinalizationRef.current = null;
    // Best-effort backend cleanup. Non-blocking — if the server
    // can't be reached the local reset still happened.
    try {
      await clearUploadedInventory();
      const fresh = await fetchInventoryInfo();
      setInventoryInfo(fresh);
    } catch {
      // ignore; the UI is already in its clean state.
    }
  }, [data, history, originalPieces, originalPlan]);

  const onStartNewProject = useCallback(async () => {
    if (hasUserState) {
      const ok = window.confirm(
        "Start a new project? This will clear seam edits, selected "
        + "slabs, assignments, uploaded inventory, validation "
        + "results, and saved workflow state for this plan.",
      );
      if (!ok) return;
    }
    await performHardReset();
  }, [hasUserState, performHardReset]);

  // 0.1.45 — picking a different sample plan with in-progress work
  // would silently mix old state (assignments, finalization, upload)
  // into the new plan. Confirm before switching; on yes, run the
  // hard reset BEFORE flipping demoId so the new demo's load doesn't
  // race with any pending cleanup.
  const onPickSample = useCallback(async (nextDemoId: string) => {
    if (nextDemoId === demoId) return;
    if (hasUserState) {
      const ok = window.confirm(
        "Switch to a different plan? Current seam edits, selected "
        + "slabs, assignments, uploaded inventory, and saved "
        + "workflow state for this plan will be cleared.",
      );
      if (!ok) return;
    }
    await performHardReset();
    setDemoId(nextDemoId);
  }, [demoId, hasUserState, performHardReset]);

  // --- Step 2 → Step 3: Finalize layout -----------------------------------
  // Snapshot the current edited pieces as the cut-piece plan. The
  // snapshot is what Steps 3 + 4 read from — they don't see live
  // Step-2 edits until the designer re-finalizes. Re-finalizing
  // prunes assignments whose piece_id is no longer in the snapshot
  // (e.g. a seam was removed) but keeps the rest.
  const onFinalize = useCallback(() => {
    const snapshotPieces = editedPieces.map((p) => ({ ...p }));
    setFinalization({
      pieces: snapshotPieces,
      finalizedAt: new Date().toISOString(),
    });
    setAssignments((current) =>
      pruneAssignments(current, snapshotPieces.map((p) => p.piece_id)),
    );
    setCurrentStep(3);
  }, [editedPieces]);

  // --- Step 4 assignment handlers ----------------------------------------
  const onAssignSlab = useCallback(
    (piece_id: string, slab_id: string) => {
      setAssignments((current) => setAssignment(current, piece_id, slab_id));
    }, [],
  );
  const onClearAssignment = useCallback((piece_id: string) => {
    setAssignments((current) => setAssignment(current, piece_id, null));
  }, []);
  const onToggleAllowDuplicates = useCallback(() => {
    setAllowDuplicateAssignments((v) => !v);
  }, []);

  // 0.1.46 — "Auto assign best slabs" fills every unassigned piece
  // with its highest-ranked candidate from the matcher response.
  // Existing assignments are preserved so designers can hand-pick
  // a couple of slabs and then click Auto-assign to fill the rest.
  const onAutoAssign = useCallback(() => {
    setAssignments((current) =>
      autoAssignBestSlabs(current, inventoryMatch, allowDuplicateAssignments),
    );
  }, [inventoryMatch, allowDuplicateAssignments]);

  const onClearAssignments = useCallback(() => {
    setAssignments({});
  }, []);

  // 0.1.53 — manual swap: drag the assigned slab of one piece onto
  // another piece to swap their slab_ids. Piece geometry, nominal
  // dimensions, absorbed-sliver flags, and the cut plan all live on
  // ``Piece`` objects (finalization.pieces) which this handler never
  // touches; only the (piece_id → slab_id) edge moves, so the
  // downstream DXF + PNG exports already pick up the new state via
  // the existing assignments reference.
  const onSwapAssignments = useCallback((a: string, b: string) => {
    if (!a || !b || a === b) return;
    setAssignments((current) => swapAssignments(current, a, b));
  }, []);
  const onToggleSwapMode = useCallback(() => {
    setSwapMode((v) => !v);
  }, []);

  // 0.1.50 — export handlers. Both close over the FINALISED
  // pieces (the same array Step 4 renders); the export bar already
  // gates the buttons behind "all assigned + no duplicates" so we
  // don't need a second check here.
  const onExportPng = useCallback(async () => {
    if (!data) return { ok: false, error: "No demo loaded." };
    if (pngExporting) {
      // Defensive — the bar already disables the button while
      // pngBusy, but a stale ref could squeeze a second click
      // through. Refuse it.
      return { ok: false, error: "An export is already running." };
    }
    setPngExporting(true);
    try {
      return await exportClientPng(data.demo_id);
    } finally {
      setPngExporting(false);
    }
  }, [data, pngExporting]);

  const onExportDxf = useCallback(async () => {
    if (!data || !finalization) {
      return { ok: false, error: "Layout not finalised." };
    }
    const doorways = editedPlan
      ? editedPlan.doorways.map(
          (d) => [d.segment[0], d.segment[1]] as [
            [number, number], [number, number],
          ],
        )
      : [];
    // Re-derive seams from the FINALISED pieces so the DXF lists
    // the cuts the factory will actually make, not whatever the
    // live Step-2 editor would show.
    const finalSeams = deriveSeams(finalization.pieces);
    const seamSegments = finalSeams.map((s) => {
      const [a, b] = s.range;
      return s.orientation === "vertical"
        ? [[s.position, a], [s.position, b]] as [
            [number, number], [number, number],
          ]
        : [[a, s.position], [b, s.position]] as [
            [number, number], [number, number],
          ];
    });
    return exportFactoryDxf(
      data.demo_id, finalization.pieces, assignments,
      doorways, seamSegments,
    );
  }, [data, finalization, assignments, editedPlan]);

  // 0.1.43 — when the Step-3 upload changes (new upload or clear),
  // refresh the resolved-inventory chip AND invalidate the Step-4
  // match cache so the next entry to Step 4 re-fetches against the
  // new inventory. Without invalidation, switching uploads would
  // leave stale candidate lists in the panel.
  const onInventoryInfoChange = useCallback((info: InventoryInfo | null) => {
    setInventoryInfo(info);
    setInventoryMatch(null);
    lastMatchedFinalizationRef.current = null;
  }, []);

  // 0.1.44 — "Generate layout from inventory size".
  //
  // Hits POST /api/demo-layouts/{id}/regenerate with the supplied
  // tile dimensions (or no body for inventory-median fallback),
  // installs the returned layout as the new pristine state, clears
  // any in-flight edits + assignments + finalization (the piece IDs
  // change with the grid so previous edits don't carry over), and
  // navigates the wizard to Step 2.
  const onRegenerateLayout = useCallback(async (
    tile?: { tile_width_mm: number; tile_height_mm: number },
  ) => {
    if (!data) return;
    const res = await regenerateLayout(data.demo_id, tile);
    const seedPlan =
      res.plan
        ? {
            target_id: res.plan.target_id,
            spaces: res.plan.spaces,
            doorways: res.plan.doorways,
            columns: res.plan.columns,
            guide_lines: res.plan.guide_lines,
          }
        : emptyPlan(res.demo_id);
    setData({
      demo_id: res.demo_id,
      label: res.label,
      layout: res.layout,
      plan: res.plan,
    });
    setOriginalPieces(res.layout.pieces);
    setOriginalPlan(seedPlan);
    history.reset({ pieces: res.layout.pieces, plan: seedPlan });
    setValidation(null);
    setValidationError(null);
    setInventoryMatch(null);
    setSelection(null);
    setPreviewPieces(null);
    setFinalization(null);
    setAssignments({});
    setAllowDuplicateAssignments(false);
    setTileChoice(res.tile_choice);
    // Drop any per-demo localStorage edits — they were keyed to the
    // OLD piece IDs.
    clearSavedState(res.demo_id);
    setLastSavedAt(null);
    setCurrentStep(2);
  }, [data, history]);

  // 0.1.42 perf: stable handler so PiecesPanel's React.memo can
  // skip re-renders on pan / drag preview ticks. The inline
  // arrows used to create a new identity every App render,
  // breaking shallow compare.
  const onSelectPieceById = useCallback((piece_id: string | null) => {
    setSelection(piece_id ? { kind: "piece", id: piece_id } : null);
  }, []);

  // --- numeric seam edit (from the side panel) -----------------------------
  // Re-derive the seam from the current pieces (the panel uses the
  // same derivation, but state can drift between input and apply if
  // a parallel edit lands). Snap + clamp to the seam's existing
  // bounds, then run applySeamMove and update the selection to the
  // new seam_id at the new position.
  const onPatchSeamPosition = useCallback(
    (seam_id: string, newPosition: number) => {
      const seams = deriveSeams(editedPieces);
      const seam = seams.find((s) => s.seam_id === seam_id);
      if (!seam) return;
      const target = resolveTargetPosition(seam, newPosition);
      if (target === seam.position) return;
      // 0.1.42: applySeamMove now rebuilds polygons for touched
      // pieces inline, so the separate ``rebuildAllRectanglePolygons``
      // pass is no longer needed here.
      const next = applySeamMove(editedPieces, seam, target);
      history.commit({ pieces: next, plan: editedPlan });
      const newSeamId = `${seam.zone_id}:${seam.orientation}:${target}`;
      setSelection({ kind: "seam", id: newSeamId });
    },
    [history, editedPieces, editedPlan],
  );

  // Derive seams once per render — both the canvas and the seam-form
  // need the same list.
  const seams = useMemo(() => deriveSeams(editedPieces), [editedPieces]);

  // --- Workflow derived state ---------------------------------------------
  // Step 3 is "complete" for gating purposes only when the designer
  // has uploaded inventory that produced at least one valid slab.
  // The fallback (real-export / demo) inventory does NOT satisfy
  // this — exporting a factory cut plan from sample data would be a
  // production footgun. Surfaced via gateForStep so the Step-4 chip
  // and the in-panel "Continue" button stay consistent.
  const inventoryReady = useMemo(
    () => uploadSummary !== null && uploadSummary.valid_slabs > 0,
    [uploadSummary],
  );
  const gateInputs = useMemo(() => ({
    layout: data?.layout ?? null,
    finalization,
    inventoryReady,
  }), [data, finalization, inventoryReady]);
  const gates = useMemo(() => ({
    1: gateForStep(1, gateInputs),
    2: gateForStep(2, gateInputs),
    3: gateForStep(3, gateInputs),
    4: gateForStep(4, gateInputs),
  } as const), [gateInputs]);

  // Wrap raw setCurrentStep so callers that try to land on an
  // unreachable step land on a banner instead. Step 4 is the main
  // user-facing case (blocked before slab upload).
  const requestStepChange = useCallback((next: WorkflowStep) => {
    const g = gates[next];
    if (g.reached) {
      setCurrentStep(next);
      setStepBlockedMessage(null);
      return;
    }
    if (g.blockedReason) setStepBlockedMessage(g.blockedReason);
  }, [gates]);

  const showStepBlockedMessage = useCallback(
    (_step: WorkflowStep, reason: string) => {
      setStepBlockedMessage(reason);
    }, [],
  );

  // Auto-dismiss the banner after a few seconds so it doesn't sit
  // around forever once the designer has read it. Cleared on the
  // next successful step change too (above).
  useEffect(() => {
    if (stepBlockedMessage === null) return;
    if (stepBlockedTimerRef.current !== null) {
      window.clearTimeout(stepBlockedTimerRef.current);
    }
    stepBlockedTimerRef.current = window.setTimeout(() => {
      setStepBlockedMessage(null);
    }, 6000);
    return () => {
      if (stepBlockedTimerRef.current !== null) {
        window.clearTimeout(stepBlockedTimerRef.current);
        stepBlockedTimerRef.current = null;
      }
    };
  }, [stepBlockedMessage]);

  // Pieces shown in Step 4. Always the frozen snapshot — re-finalize
  // to refresh. Step 1 / 2 use ``displayedPieces`` (live edits).
  const piecesForStep4 = finalization?.pieces ?? [];

  const assignmentSummary = useMemo(
    () => summarizeAssignments(
      piecesForStep4.map((p) => p.piece_id),
      assignments,
      inventoryMatch,
      allowDuplicateAssignments,
    ),
    [piecesForStep4, assignments, inventoryMatch, allowDuplicateAssignments],
  );

  // 0.1.53 — Piece-ids whose currently-assigned slab is too small.
  // Drives the red ring on the canvas and the per-row warning chip
  // in PiecesPanel. Cheap derivation: walk the matcher response and
  // flag any assignment whose slab_id isn't in the piece's full
  // candidate list (Step-4 matcher returns the whole inventory).
  const invalidAssignmentPieceIds = useMemo(() => {
    const out = new Set<string>();
    if (!inventoryMatch) return out;
    const byId = new Map<string, typeof inventoryMatch.pieces[number]>();
    for (const pm of inventoryMatch.pieces) byId.set(pm.piece_id, pm);
    for (const [pid, slabId] of Object.entries(assignments)) {
      if (!slabId) continue;
      const pm = byId.get(pid);
      if (!pm) continue;
      const fits = pm.candidates.some((c) => c.slab_id === slabId);
      if (!fits) out.add(pid);
    }
    return out;
  }, [assignments, inventoryMatch]);

  // 0.1.52 — slab-image readiness. Recomputes the URL list every
  // render (cheap, just lookups) and feeds it into the readiness
  // hook so the Step-4 export bar can gate the PNG button on
  // "every image actually loaded in the browser". Same URL scheme
  // the canvas uses, so hits the same HTTP cache.
  const assignedImageRefs = useMemo(
    () => getAssignedImageRefs(piecesForStep4, assignments, inventoryMatch),
    [piecesForStep4, assignments, inventoryMatch],
  );
  const imageReadiness = useSlabImagesReady(assignedImageRefs);

  // 0.1.45 — completion is the AND of two things: (a) the user has
  // moved BEYOND the step, and (b) the step's required data is
  // present. Without clause (a), rewinding to Step 1 would leave
  // every later step's check mark green based purely on data still
  // sitting in memory — the bug the persistence audit flagged.
  //
  // Step 3 specifically: the previous predicate was
  // ``finalization !== null`` which conflates it with Step 2. The
  // correct check is "is a usable inventory active" — uploaded
  // session OR the fallback inventory's valid_count > 0.
  const completedSteps = useMemo(() => {
    const inventoryReady =
      inventoryInfo !== null && inventoryInfo.valid_count > 0;
    const allAssigned =
      assignmentSummary.total > 0
      && assignmentSummary.assigned === assignmentSummary.total;
    return {
      1: currentStep > 1 && data !== null,
      2: currentStep > 2 && data !== null,
      3: currentStep > 3 && inventoryReady,
      4: currentStep === 4 && allAssigned,
    } as const;
  }, [currentStep, data, inventoryInfo, assignmentSummary]);

  // When the designer enters Step 4 (or finalizes a fresh layout),
  // kick off a match against the FINAL pieces so the candidate list
  // reflects the actual cut-piece sizes. The Step-2 background match
  // already runs against edited pieces; here we redo the request with
  // the snapshot so a late finalize doesn't leave a stale list.
  // De-dupe: stash the finalization reference we last matched
  // against. Going Step 2 → Step 4 → Step 2 → Step 4 without
  // re-finalizing should NOT spam the matcher with identical
  // requests. The reference identity of ``finalization`` is the
  // right key here — onFinalize allocates a fresh snapshot each
  // time, so equal snapshots get the same reference.
  const lastMatchedFinalizationRef = useRef<FinalizationState | null>(null);
  useEffect(() => {
    if (currentStep !== 4) return;
    if (!data || !finalization) return;
    if (lastMatchedFinalizationRef.current === finalization) return;
    lastMatchedFinalizationRef.current = finalization;
    const token = ++matchReqTokenRef.current;
    // 0.1.48 — Step 4 needs FULL inventory visibility for auto-
    // assignment. The matcher defaults to top_k=3 which is great
    // for the Step-2 preview but fatal here: a tile-uniform layout
    // gets the same 3 slabs back for every piece, so unique-slab
    // auto-assign exhausts after 3 picks even when many more slabs
    // would fit. Ask for ``valid_count`` (whole inventory) when we
    // know it, with a sane upper bound the backend also clamps.
    const inventoryTopK = Math.max(
      32,
      inventoryInfo?.valid_count ?? 0,
    );
    postMatchInventory(
      data.demo_id, finalization.pieces, true, inventoryTopK,
    )
      .then((result) => {
        if (matchReqTokenRef.current === token) {
          setInventoryMatch(result);
        }
      })
      .catch((e) => {
        if (matchReqTokenRef.current === token) {
          // The matcher endpoint is non-critical; log and move on
          // rather than blocking the panel.
          console.warn("Step 4 match-inventory failed:", e);
        }
      });
  }, [currentStep, data, finalization, inventoryInfo]);

  return (
    <div className="app">
      <StepperHeader
        current={currentStep}
        gates={gates}
        completed={completedSteps}
        onChange={requestStepChange}
        onBlockedStep={showStepBlockedMessage}
        onStartNewProject={onStartNewProject}
        onOpenHelp={() => setHelpOpen(true)}
      />
      {stepBlockedMessage && (
        <div
          className="step-blocked-banner"
          role="alert"
          aria-live="polite"
          onClick={() => setStepBlockedMessage(null)}
          title="Click to dismiss"
        >
          {stepBlockedMessage}
        </div>
      )}
      <HelpModal open={helpOpen} onClose={() => setHelpOpen(false)} />
      <ExportingOverlay
        open={pngExporting}
        message="Generating client image… Please wait."
      />

      {error && (
        <div className="error">
          <strong>API error.</strong> {error}
          <p className="error-hint">
            Make sure the backend is running:&nbsp;
            <code>python scripts/run_api_server.py</code>
          </p>
        </div>
      )}

      {/* Step 1 — Upload Plan ----------------------------------------- */}
      {currentStep === 1 && (
        <Step1PlanPanel
          demos={demos}
          selectedDemoId={data?.demo_id ?? null}
          loading={loading}
          onPickSample={onPickSample}
          onContinue={() => requestStepChange(2)}
          canContinue={gates[2].reached}
        />
      )}

      {/* Step 2 — Plan validation / seam editor (the existing screen) */}
      {currentStep === 2 && (
        data && editedPlan ? (
          <>
            <ModeToolbar
              mode={mode}
              onChange={onChangeMode}
              canUndo={history.canUndo}
              canRedo={history.canRedo}
              onUndo={history.undo}
              onRedo={history.redo}
            />
            <div className="canvas-and-panel">
              <LayoutCanvas
                layout={data.layout}
                pieces={displayedPieces}
                plan={editedPlan}
                validation={validation}
                mode={mode}
                selection={selection}
                onSelect={setSelection}
                onSeamDragMove={onSeamDragMove}
                onSeamDragEnd={onSeamDragEnd}
                onAddDoorway={onAddDoorway}
                onAddColumn={onAddColumn}
                onAddGuideLine={onAddGuideLine}
                onAddSeam={onAddSeam}
                tileChoice={tileChoice}
              />
              <div className="right-stack">
                {/* Always-visible "Properties" summary at the top —
                    one card per current selection (piece / seam /
                    annotation). Shows ID, dimensions, area, risk
                    badges and slab-match status without forcing the
                    designer to expand a row. Empty when nothing is
                    selected. */}
                <SelectionProperties
                  selection={selection}
                  pieces={displayedPieces}
                  seams={seams}
                  layout={data.layout}
                  validation={validation}
                  inventoryMatch={inventoryMatch}
                  mode={mode}
                />
                {/* PropertiesPanel below the summary is the edit form
                    for doorways / columns / guide lines / seams —
                    rendered only when one of those is selected. The
                    SelectionProperties card above already shows the
                    summary; this form is the actionable surface. */}
                <PropertiesPanel
                  seams={seams}
                  plan={editedPlan}
                  selection={selection}
                  onPatchDoorway={onPatchDoorway}
                  onPatchColumn={onPatchColumn}
                  onPatchGuideLine={onPatchGuideLine}
                  onPatchSeamPosition={onPatchSeamPosition}
                  onDelete={onDeleteSelection}
                />
                <ValidationSummary
                  validation={validation}
                  isValidating={isValidating}
                  isEdited={isEdited}
                  error={validationError}
                  onValidateNow={runValidation}
                  onResetEdits={onResetEdits}
                />
                <PiecesPanel
                  pieces={displayedPieces}
                  layout={data.layout}
                  validation={validation}
                  inventoryMatch={inventoryMatch}
                  inventoryInfo={inventoryInfo}
                  inventoryInfoError={inventoryInfoError}
                  selectedPieceId={
                    selection?.kind === "piece" ? selection.id : null
                  }
                  onSelectPiece={onSelectPieceById}
                />
                {/* Primary "next step" action sits below the right
                    stack so it doesn't compete with the editing
                    tools but is always within reach. */}
                <div className="step-next-bar">
                  <span className="step-next-hint">
                    {lastSavedAt
                      ? `Saved ${formatSavedAt(lastSavedAt)}${isEdited ? " · edited" : ""}`
                      : (isEdited ? "Edited" : "")}
                  </span>
                  <button
                    type="button"
                    className="step-panel-primary"
                    onClick={onFinalize}
                    disabled={displayedPieces.length === 0}
                    title="Freeze the current pieces and continue to slab upload"
                  >
                    Finalize layout →
                  </button>
                </div>
              </div>
            </div>
          </>
        ) : (
          <div className="placeholder">Loading layout…</div>
        )
      )}

      {/* Step 3 — Upload slab data ------------------------------------ */}
      {currentStep === 3 && (
        <Step3InventoryPanel
          info={inventoryInfo}
          infoError={inventoryInfoError}
          onInfoChange={onInventoryInfoChange}
          uploadSummary={uploadSummary}
          uploadExcelName={uploadExcelName}
          uploadImageCount={uploadImageCount}
          onUploadResult={(s, name, count) => {
            setUploadSummary(s);
            setUploadExcelName(name);
            setUploadImageCount(count);
          }}
          onRegenerateLayout={onRegenerateLayout}
          onContinue={() => requestStepChange(4)}
          canContinue={gates[4].reached}
        />
      )}

      {/* Step 4 — Assign + export ------------------------------------- */}
      {currentStep === 4 && data && finalization && (
        <div className="canvas-and-panel">
          <LayoutCanvas
            layout={data.layout}
            pieces={finalization.pieces}
            plan={editedPlan ?? emptyPlan("")}
            validation={null}
            mode="select_piece"
            selection={selection}
            onSelect={setSelection}
            onSeamDragMove={() => {}}
            onSeamDragEnd={() => {}}
            onAddDoorway={() => {}}
            onAddColumn={() => {}}
            onAddGuideLine={() => {}}
            onAddSeam={() => {}}
            tileChoice={tileChoice}
            assignments={assignments}
            inventoryMatch={inventoryMatch}
            swapMode={swapMode}
            onSwapAssignments={onSwapAssignments}
            invalidPieceIds={invalidAssignmentPieceIds}
          />
          <div className="right-stack">
            <SelectionProperties
              selection={selection}
              pieces={finalization.pieces}
              seams={[]}
              layout={data.layout}
              validation={null}
              inventoryMatch={inventoryMatch}
              assignments={assignments}
            />
            <Step4ExportBar
              total={assignmentSummary.total}
              assigned={assignmentSummary.assigned}
              unassigned={assignmentSummary.unassigned}
              noMatch={assignmentSummary.no_match}
              tooSmall={assignmentSummary.too_small}
              duplicate={assignmentSummary.duplicate}
              swapMode={swapMode}
              onToggleSwapMode={onToggleSwapMode}
              canAutoAssign={
                inventoryMatch !== null
                && inventoryMatch.pieces.some((p) => p.candidates.length > 0)
              }
              onAutoAssign={onAutoAssign}
              onClearAssignments={onClearAssignments}
              inventoryValidCount={inventoryInfo?.valid_count ?? null}
              inventoryUnusedCount={(() => {
                const valid = inventoryInfo?.valid_count ?? 0;
                if (!valid) return 0;
                const used = new Set(
                  Object.values(assignments).filter((v): v is string => !!v),
                );
                return Math.max(0, valid - used.size);
              })()}
              onExportPng={onExportPng}
              onExportDxf={onExportDxf}
              imageReadiness={imageReadiness}
            />
            <PiecesPanel
              pieces={finalization.pieces}
              layout={data.layout}
              validation={null}
              inventoryMatch={inventoryMatch}
              inventoryInfo={inventoryInfo}
              inventoryInfoError={inventoryInfoError}
              selectedPieceId={
                selection?.kind === "piece" ? selection.id : null
              }
              onSelectPiece={onSelectPieceById}
              assignmentMode={true}
              assignments={assignments}
              allowDuplicateAssignments={allowDuplicateAssignments}
              onAssignSlab={onAssignSlab}
              onClearAssignment={onClearAssignment}
              onToggleAllowDuplicates={onToggleAllowDuplicates}
            />
            <div className="step-next-bar">
              <button
                type="button"
                className="step-panel-btn"
                onClick={() => requestStepChange(2)}
                title="Go back to the seam editor"
              >
                ← Back to editor
              </button>
            </div>
          </div>
        </div>
      )}

      <footer className="app-footer">
        4-step workflow · Step 1: Upload · Step 2: Edit · Step 3:
        Slabs · Step 4: Assign + Export
      </footer>
    </div>
  );
}

/** Format an ISO timestamp as a compact local time-of-day for the
 *  header status. Used only by the App's status string, so kept
 *  inline rather than in lib/. */
function formatSavedAt(iso: string): string {
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "—";
    return d.toLocaleTimeString([], {
      hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return "—";
  }
}
