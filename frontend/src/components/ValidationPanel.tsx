// Side panel that surfaces the backend's RuleReport for the
// current edited layout.
//
// Foundation-milestone scope: present the per-rule outcomes so
// the designer sees R1/R2/R7/R9 status at a glance. A future
// milestone will let the user click a rule to spotlight its
// affected pieces; for now affected IDs are listed inline so the
// information is at least retrievable.

import type { RuleStatus, ValidationResult } from "../lib/types";

interface Props {
  /** null while we have never validated; isValidating overrides. */
  validation: ValidationResult | null;
  isValidating: boolean;
  isEdited: boolean;
  error?: string | null;
  onValidateNow: () => void;
  onResetEdits: () => void;
}

const STATUS_LABEL: Record<RuleStatus, string> = {
  pass: "PASS",
  violation: "VIOLATION",
  reward: "REWARD",
  info: "info",
  not_applicable: "n/a",
};

const STATUS_CLASS: Record<RuleStatus, string> = {
  pass: "rule-pass",
  violation: "rule-violation",
  reward: "rule-reward",
  info: "rule-info",
  not_applicable: "rule-na",
};

export default function ValidationPanel({
  validation, isValidating, isEdited, error,
  onValidateNow, onResetEdits,
}: Props) {
  const isValid = validation?.is_valid ?? null;
  return (
    <aside className="validation-panel">
      <header className="vp-header">
        <span className="vp-title">Validation</span>
        <div className="vp-actions">
          <button
            type="button"
            className="vp-btn"
            onClick={onValidateNow}
            disabled={isValidating}
          >
            {isValidating ? "Validating…" : "Validate now"}
          </button>
          <button
            type="button"
            className="vp-btn"
            onClick={onResetEdits}
            disabled={!isEdited}
          >
            Reset edits
          </button>
        </div>
      </header>

      {error ? (
        <div className="vp-error">{error}</div>
      ) : validation === null ? (
        <div className="vp-empty">
          Edit a seam to validate, or click <em>Validate now</em>.
        </div>
      ) : (
        <>
          <div
            className={`vp-summary ${
              isValid ? "vp-summary-valid" : "vp-summary-invalid"
            }`}
          >
            <div className="vp-summary-badge">
              {isValid ? "VALID" : "INVALID"}
            </div>
            <div className="vp-summary-meta">
              <div>
                score <strong>{validation.design_score.toFixed(1)}</strong>
              </div>
              <div>
                hard violations{" "}
                <strong>{validation.hard_violation_count}</strong>
              </div>
              <div>
                soft violations{" "}
                <strong>{validation.soft_violation_count}</strong>
              </div>
              <div>
                rewards <strong>{validation.reward_count}</strong>
              </div>
            </div>
          </div>

          <ul className="vp-rule-list">
            {validation.rules.map((r) => {
              const cls = STATUS_CLASS[r.status];
              const label = STATUS_LABEL[r.status];
              const showDelta = r.score_delta !== 0;
              return (
                <li key={r.rule_id} className={`vp-rule ${cls}`}>
                  <div className="vp-rule-head">
                    <span className="vp-rule-id">{r.rule_id}</span>
                    <span className="vp-rule-status">{label}</span>
                    {showDelta && (
                      <span className="vp-rule-delta">
                        {r.score_delta > 0 ? "+" : ""}
                        {r.score_delta.toFixed(1)}
                      </span>
                    )}
                  </div>
                  <div className="vp-rule-msg">{r.message}</div>
                  {r.affected_ids.length > 0 && (
                    <div className="vp-rule-affected" title={r.affected_ids.join(", ")}>
                      {r.affected_ids.length} affected:{" "}
                      <code>{r.affected_ids.slice(0, 4).join(", ")}</code>
                      {r.affected_ids.length > 4 && "…"}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        </>
      )}
    </aside>
  );
}
