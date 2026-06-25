// Undo/redo history for the editor.
//
// The history records committed (pieces, plan) snapshots. The
// frontend drives the live state through `commit(next)` for
// discrete events (seam drag end, add/edit/delete on any plan
// object) and through a SEPARATE preview channel for in-flight
// drags (see App.tsx — `previewPieces`).
//
// Design notes:
//   * Every commit clears the redo stack — branching the future
//     would surprise designers and is overkill for V1.
//   * History is capped at MAX_HISTORY entries; oldest snapshots
//     are dropped when the cap is reached.
//   * `commit` short-circuits when the next state is identical
//     to the current present (referential equality on both
//     pieces and plan) so accidental no-op patches don't pollute
//     the undo stack.
//   * `reset` clears past + future and replants the present —
//     used when the user picks a different demo, or hits "Reset
//     edits" in the validation panel.
//   * `dispatch` from useReducer is stable across renders, so
//     the wrapped callbacks (`commit`, `undo`, etc.) are too —
//     safe to depend on from keyboard-shortcut effects.

import { useCallback, useReducer } from "react";

import type { Piece, Plan } from "./types";

export interface EditorSnapshot {
  pieces: Piece[];
  plan: Plan;
}

interface InternalState {
  past: EditorSnapshot[];
  present: EditorSnapshot;
  future: EditorSnapshot[];
}

type Action =
  | { type: "commit"; next: EditorSnapshot }
  | { type: "undo" }
  | { type: "redo" }
  | { type: "reset"; state: EditorSnapshot };

const MAX_HISTORY = 100;

function reducer(state: InternalState, action: Action): InternalState {
  switch (action.type) {
    case "commit": {
      const next = action.next;
      // Avoid identity-only commits — they'd pollute the undo stack
      // without giving the designer anything useful to step through.
      if (
        next.pieces === state.present.pieces
        && next.plan === state.present.plan
      ) {
        return state;
      }
      const past = trimHead([...state.past, state.present]);
      return { past, present: next, future: [] };
    }
    case "undo": {
      if (state.past.length === 0) return state;
      const prev = state.past[state.past.length - 1];
      return {
        past: state.past.slice(0, -1),
        present: prev,
        future: [state.present, ...state.future],
      };
    }
    case "redo": {
      if (state.future.length === 0) return state;
      const next = state.future[0];
      return {
        past: trimHead([...state.past, state.present]),
        present: next,
        future: state.future.slice(1),
      };
    }
    case "reset":
      return { past: [], present: action.state, future: [] };
  }
}

function trimHead<T>(arr: T[]): T[] {
  return arr.length > MAX_HISTORY
    ? arr.slice(arr.length - MAX_HISTORY)
    : arr;
}

function init(state: EditorSnapshot): InternalState {
  return { past: [], present: state, future: [] };
}

export interface EditorHistory {
  pieces: Piece[];
  plan: Plan;
  canUndo: boolean;
  canRedo: boolean;
  /** Snapshot count, useful for "1/12" indicators in the future. */
  pastCount: number;
  futureCount: number;
  /** Push a new snapshot to history. */
  commit: (next: EditorSnapshot) => void;
  /** Step backwards. No-op if past is empty. */
  undo: () => void;
  /** Step forwards. No-op if future is empty. */
  redo: () => void;
  /** Clear history and seed a new present. */
  reset: (state: EditorSnapshot) => void;
}

export function useEditorHistory(initial: EditorSnapshot): EditorHistory {
  const [state, dispatch] = useReducer(reducer, initial, init);

  const commit = useCallback(
    (next: EditorSnapshot) => dispatch({ type: "commit", next }),
    [],
  );
  const undo = useCallback(() => dispatch({ type: "undo" }), []);
  const redo = useCallback(() => dispatch({ type: "redo" }), []);
  const reset = useCallback(
    (s: EditorSnapshot) => dispatch({ type: "reset", state: s }),
    [],
  );

  return {
    pieces: state.present.pieces,
    plan: state.present.plan,
    canUndo: state.past.length > 0,
    canRedo: state.future.length > 0,
    pastCount: state.past.length,
    futureCount: state.future.length,
    commit,
    undo,
    redo,
    reset,
  };
}
