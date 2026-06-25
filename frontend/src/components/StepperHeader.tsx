// Stepper header for the 4-step production wizard. Replaces the
// old App title bar — shows progress through the workflow and lets
// the designer jump back to any reached step.
//
// Design intent (inspired by Drafted AI):
//   * One chip per step, numbered + titled
//   * Reached steps are clickable; gated steps show a tooltip
//   * The current step is highlighted with a primary accent
//   * Completed steps show a check mark
//
// Each chip's reachability is controlled by ``gates`` from
// ``lib/workflow.ts`` — the App computes them once per render and
// passes the array in so this component stays presentational.

import { HELP_UI, defaultHelpLang } from "../lib/helpContent";
import type { WorkflowStep } from "../lib/types";
import type { StepDescriptor, StepGate } from "../lib/workflow";
import { STEPS } from "../lib/workflow";

interface Props {
  current: WorkflowStep;
  gates: Record<WorkflowStep, StepGate>;
  /** Per-step "is this step done?" — drives the check mark. Step 1
   *  is "done" once a layout is loaded; Step 2 once finalized; etc.
   *  Computed by App so the stepper stays presentational. */
  completed: Record<WorkflowStep, boolean>;
  onChange: (step: WorkflowStep) => void;
  /** Optional handler invoked when the designer clicks a chip whose
   *  prerequisites aren't met. The App uses this to surface a banner
   *  with ``gate.blockedReason`` (the chip's tooltip would otherwise
   *  be the only feedback, which is easy to miss). When omitted the
   *  blocked chip behaves like before — clicks are no-ops. */
  onBlockedStep?: (step: WorkflowStep, reason: string) => void;
  /** 0.1.45 — "Start new project" handler. Always visible in the
   *  header so the designer can rewind no matter which step they're
   *  on. App owns the confirmation prompt + the actual reset. */
  onStartNewProject: () => void;
  /** 0.1.51 — open the bilingual help modal. */
  onOpenHelp: () => void;
}

export default function StepperHeader({
  current, gates, completed, onChange, onBlockedStep,
  onStartNewProject, onOpenHelp,
}: Props) {
  // The Help button shows the label in the user's preferred
  // language so first-time visitors recognise it immediately. The
  // modal itself then offers a full toggle.
  const helpLabel = HELP_UI[defaultHelpLang()].helpButton;
  return (
    <header className="stepper-header">
      <div className="stepper-brand">
        <span className="stepper-brand-logo" aria-hidden="true">
          <svg viewBox="0 0 32 32" width="32" height="32">
            <polygon
              points="16,3 30,28 2,28"
              fill="#0f1d3a"
              stroke="#0f1d3a"
              strokeWidth="1.5"
              strokeLinejoin="round"
            />
            <polygon
              points="16,11 24,25 8,25"
              fill="#ffffff"
            />
          </svg>
        </span>
        <span className="stepper-brand-text">Avandad — Layout Helper</span>
        <span className="stepper-brand-version" title="App version">
          v1.0.0
        </span>
      </div>
      <ol className="stepper-list">
        {STEPS.map((step, idx) => (
          <li
            key={step.id}
            className="stepper-item"
          >
            <StepChip
              step={step}
              isCurrent={step.id === current}
              isCompleted={completed[step.id]}
              gate={gates[step.id]}
              onClick={() => {
                const g = gates[step.id];
                if (g.reached) onChange(step.id);
                else if (onBlockedStep && g.blockedReason) {
                  onBlockedStep(step.id, g.blockedReason);
                }
              }}
            />
            {idx < STEPS.length - 1 && (
              <span
                className={
                  "stepper-connector"
                  + (completed[step.id] ? " stepper-connector-done" : "")
                }
                aria-hidden="true"
              >
                <svg viewBox="0 0 16 16" width="16" height="16">
                  <path
                    d="M3 8 H12 M9 5 L12 8 L9 11"
                    fill="none" stroke="currentColor"
                    strokeWidth="1.5" strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </span>
            )}
          </li>
        ))}
      </ol>
      <div className="stepper-header-actions">
        <button
          type="button"
          className="stepper-help-btn"
          onClick={onOpenHelp}
          title={helpLabel}
          aria-label={helpLabel}
        >
          <svg
            viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"
          >
            <circle
              cx="8" cy="8" r="6.5"
              fill="none" stroke="currentColor" strokeWidth="1.4"
            />
            <path
              d="M6 6.2 C6 4.8 7 4 8 4 C9.2 4 10 4.9 10 6 C10 7.2 8 7.4 8 9"
              fill="none" stroke="currentColor" strokeWidth="1.4"
              strokeLinecap="round"
            />
            <circle cx="8" cy="11.5" r="0.8" fill="currentColor" />
          </svg>
          <span>{helpLabel}</span>
        </button>
        <button
          type="button"
          className="stepper-reset-btn"
          onClick={onStartNewProject}
          title="Discard the active project (edits, assignments, upload) and return to Step 1"
        >
          <span>Start new project</span>
          <svg
            viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"
          >
            <path
              d="M8 3 V13 M3 8 H13"
              stroke="currentColor" strokeWidth="2" strokeLinecap="round"
            />
          </svg>
        </button>
      </div>
    </header>
  );
}

function StepChip({
  step, isCurrent, isCompleted, gate, onClick,
}: {
  step: StepDescriptor;
  isCurrent: boolean;
  isCompleted: boolean;
  gate: StepGate;
  onClick: () => void;
}) {
  const cls = [
    "stepper-chip",
    isCurrent && "stepper-chip-current",
    isCompleted && "stepper-chip-done",
    !gate.reached && "stepper-chip-blocked",
  ].filter(Boolean).join(" ");
  return (
    <button
      type="button"
      className={cls}
      onClick={onClick}
      // ``aria-disabled`` instead of ``disabled`` so the click still
      // fires when the chip is blocked — App relays the blocked
      // reason via onBlockedStep so the designer sees an explicit
      // message instead of a silently inert button.
      aria-disabled={!gate.reached}
      title={gate.blockedReason ?? `Step ${step.id} — ${step.title}`}
    >
      <span className="stepper-chip-num">
        {isCompleted ? "✓" : step.id}
      </span>
      <span className="stepper-chip-body">
        <span className="stepper-chip-title">{step.title}</span>
        <span className="stepper-chip-caption">{step.caption}</span>
      </span>
    </button>
  );
}
