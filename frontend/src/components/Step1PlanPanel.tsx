// Step 1 — "Upload Plan" panel.
//
// The production target is: designer drops a DXF, the backend
// parses it, and the editor jumps to Step 2. For the V1 milestone
// we ship the SHELL — an upload button that's wired to a file
// picker but doesn't yet POST anywhere — plus the existing sample
// plan picker reframed as the "or use a sample" alternative.
//
// Once real upload arrives, the upload button's ``onPickFile``
// will POST to a /api/plans endpoint and call back into App with
// the resulting demo_id. The sample picker stays in place as a
// secondary control so QA + demos don't depend on a DXF being on
// the operator's machine.

import { useRef, useState } from "react";

import type { DemoIndexEntry } from "../lib/types";


interface Props {
  demos: DemoIndexEntry[];
  /** Currently picked sample / loaded plan. Drives the inline
   *  "Current: <name>" hint under the picker. Null until the user
   *  has chosen one. */
  selectedDemoId: string | null;
  loading: boolean;
  onPickSample: (demo_id: string) => void;
  onContinue: () => void;
  /** True when the next-step gate is satisfied (a plan is loaded).
   *  Disables the primary action until then. */
  canContinue: boolean;
}

export default function Step1PlanPanel({
  demos, selectedDemoId, loading,
  onPickSample, onContinue, canContinue,
}: Props) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  // Local-only file name. The real upload backend isn't wired in
  // this milestone — picking a file just stores its name so the UI
  // shows "Selected: my_plan.dxf · upload not implemented yet".
  const [pickedFileName, setPickedFileName] = useState<string | null>(null);

  const selectedDemo = demos.find((d) => d.demo_id === selectedDemoId) ?? null;

  return (
    <div className="step-panel step-panel-1">
      <div className="step-panel-intro">
        <h2 className="step-panel-title">Step 1 · Upload plan</h2>
        <p className="step-panel-caption">
          Drop a DXF or CAD plan to begin. Don't have one handy?
          Pick a sample plan to walk through the workflow.
        </p>
      </div>

      <div className="step-panel-card">
        <div className="step-panel-card-title">Upload a DXF</div>
        <input
          ref={fileInputRef}
          type="file"
          accept=".dxf,.dwg"
          className="step-panel-file-input"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) setPickedFileName(f.name);
          }}
        />
        <button
          type="button"
          className="step-panel-btn"
          onClick={() => fileInputRef.current?.click()}
        >
          Choose DXF…
        </button>
        {pickedFileName && (
          <div className="step-panel-file-status">
            Selected: <strong>{pickedFileName}</strong>
            <span className="step-panel-file-note">
              · upload not implemented in this milestone — pick a
              sample plan below to continue.
            </span>
          </div>
        )}
      </div>

      <div className="step-panel-card">
        <div className="step-panel-card-title">Or use a sample plan</div>
        <div className="step-panel-samples">
          {demos.length === 0 && (
            <div className="step-panel-empty">Loading sample plans…</div>
          )}
          {demos.map((d) => (
            <button
              key={d.demo_id}
              type="button"
              className={
                "step-panel-sample"
                + (d.demo_id === selectedDemoId
                  ? " step-panel-sample-active" : "")
              }
              onClick={() => onPickSample(d.demo_id)}
              disabled={loading}
            >
              <span className="step-panel-sample-label">{d.label}</span>
              <span className="step-panel-sample-id">{d.demo_id}</span>
            </button>
          ))}
        </div>
        {selectedDemo && (
          <div className="step-panel-file-status">
            Loaded: <strong>{selectedDemo.label}</strong>
            {loading && (
              <span className="step-panel-file-note"> · loading…</span>
            )}
          </div>
        )}
      </div>

      <div className="step-panel-actions">
        <button
          type="button"
          className="step-panel-primary"
          onClick={onContinue}
          disabled={!canContinue}
          title={
            canContinue
              ? "Continue to plan editor"
              : "Pick a sample plan or upload a DXF first"
          }
        >
          Continue to editor →
        </button>
      </div>
    </div>
  );
}
