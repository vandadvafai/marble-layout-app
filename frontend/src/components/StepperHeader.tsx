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
      <div className="stepper-brand">Stone Layout</div>
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
              />
            )}
          </li>
        ))}
      </ol>
      <button
        type="button"
        className="stepper-help-btn"
        onClick={onOpenHelp}
        title={helpLabel}
        aria-label={helpLabel}
      >
        {helpLabel}
      </button>
      <button
        type="button"
        className="stepper-reset-btn"
        onClick={onStartNewProject}
        title="Discard the active project (edits, assignments, upload) and return to Step 1"
      >
        Start new project
      </button>
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
