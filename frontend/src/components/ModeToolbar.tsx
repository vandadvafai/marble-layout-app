// Editor-mode toolbar.
//
// Two button groups separated by a divider:
//   * Left  — Undo / Redo (with their disabled state driven by the
//     editor history).
//   * Right — mode switcher (Select / +Doorway / +Column / +Guide
//     line / +Seam).
//
// Keyboard shortcuts for undo/redo live in App.tsx so they're
// active anywhere, not just when the toolbar is mounted.

import { memo } from "react";

import type { EditorMode } from "../lib/types";

interface Props {
  mode: EditorMode;
  onChange: (mode: EditorMode) => void;
  canUndo: boolean;
  canRedo: boolean;
  onUndo: () => void;
  onRedo: () => void;
}

interface ModeDescriptor {
  mode: EditorMode;
  label: string;
  hint: string;
}

// Two-section toolbar (0.1.41):
//   * SELECTION_MODES — what to click / select. Mutually exclusive
//     so there's no ambiguity about whether pieces or seams respond
//     to a canvas click.
//   * EDITING_MODES   — drawing tools that ADD new objects. Each one
//     drops back to Select Pieces after a successful add.
const SELECTION_MODES: ModeDescriptor[] = [
  { mode: "select_piece", label: "Select Pieces",
    hint: "Click a piece to view its properties · drag canvas to pan" },
  { mode: "edit_seam", label: "Edit Seams",
    hint: "Click and drag a seam to move it (50 mm snap) · pieces are inert" },
];

const EDITING_MODES: ModeDescriptor[] = [
  { mode: "add_seam", label: "+ Seam",
    hint: "Drag across pieces to add a vertical or horizontal seam (50 mm snap)" },
  { mode: "doorway", label: "+ Doorway",
    hint: "Drag a line for the door threshold" },
  { mode: "column", label: "+ Column",
    hint: "Drag a rectangle for the column footprint" },
  { mode: "guide_line", label: "+ Guide line",
    hint: "Drag a line for the architectural axis" },
];

const ALL_MODES: ModeDescriptor[] = [...SELECTION_MODES, ...EDITING_MODES];

const SHORTCUT_HINT = navigator.platform.toLowerCase().includes("mac")
  ? "⌘Z / ⇧⌘Z"
  : "Ctrl+Z / Ctrl+Shift+Z";

function ModeToolbarImpl({
  mode, onChange, canUndo, canRedo, onUndo, onRedo,
}: Props) {
  const current = ALL_MODES.find((m) => m.mode === mode) ?? ALL_MODES[0];
  return (
    <div className="mode-toolbar">
      <div className="mode-buttons mode-buttons-history">
        <button
          type="button"
          className="mode-btn"
          onClick={onUndo}
          disabled={!canUndo}
          title={`Undo (${SHORTCUT_HINT.split(" / ")[0]})`}
        >
          ↶ Undo
        </button>
        <button
          type="button"
          className="mode-btn"
          onClick={onRedo}
          disabled={!canRedo}
          title={`Redo (${SHORTCUT_HINT.split(" / ")[1]})`}
        >
          ↷ Redo
        </button>
      </div>
      <div className="mode-toolbar-divider" />
      <div className="mode-section">
        <span className="mode-section-label">Selection</span>
        <div className="mode-buttons">
          {SELECTION_MODES.map((m) => (
            <button
              key={m.mode}
              type="button"
              className={
                `mode-btn ${m.mode === mode ? "mode-btn-active" : ""}`
              }
              onClick={() => onChange(m.mode)}
              title={m.hint}
            >
              {m.label}
            </button>
          ))}
        </div>
      </div>
      <div className="mode-toolbar-divider" />
      <div className="mode-section">
        <span className="mode-section-label">Editing</span>
        <div className="mode-buttons">
          {EDITING_MODES.map((m) => (
            <button
              key={m.mode}
              type="button"
              className={
                `mode-btn ${m.mode === mode ? "mode-btn-active" : ""}`
              }
              onClick={() => onChange(m.mode)}
              title={m.hint}
            >
              {m.label}
            </button>
          ))}
        </div>
      </div>
      <span className="mode-hint">{current.hint}</span>
    </div>
  );
}


// 0.1.42 perf: memoised. Mode/undo state changes infrequently
// compared with pan and drag events that re-render the App.
const ModeToolbar = memo(ModeToolbarImpl);
export default ModeToolbar;
