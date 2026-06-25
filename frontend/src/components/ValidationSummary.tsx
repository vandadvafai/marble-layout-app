// Compact validation summary for Step 2's right panel.
//
// The full ValidationPanel was overwhelming the right stack while
// the designer was editing. The summary card surfaces only what
// matters at a glance:
//   * pass / warning / error state (single chip),
//   * total issue count,
//   * the most important rule violation (one line),
// and offers a "View details" toggle that expands into the full
// rule list inline.
//
// "Validation" today means: run the architectural rule layer
// against the current layout. That's a set of about 9 rules (R1..R9)
// that check geometric production constraints — seam alignment with
// doorways, no seams over columns, minimum coverage, etc. It's
// production-readiness, NOT slab-assignment readiness. As we add
// real assignment logic in Step 4, the rule layer will likely move
// there; for now it's the only readiness signal we have.

import { memo, useState } from "react";

import type {
  RuleResult, ValidationResult,
} from "../lib/types";

interface Props {
  validation: ValidationResult | null;
  isValidating: boolean;
  isEdited: boolean;
  error?: string | null;
  onValidateNow: () => void;
  onResetEdits: () => void;
}


/** Worst rule in the report — first violation by score impact (the
 *  most negative ``score_delta``). The backend doesn't tag rules as
 *  hard vs soft individually (the aggregate counts come from the
 *  rule layer's classification), so we pick by impact instead and
 *  return null if every rule passed. */
function pickTopIssue(validation: ValidationResult): RuleResult | null {
  const violations = validation.rules.filter(
    (r) => r.status === "violation",
  );
  if (violations.length === 0) return null;
  return violations.reduce(
    (worst, r) => (r.score_delta < worst.score_delta ? r : worst),
    violations[0],
  );
}


function ValidationSummaryImpl({
  validation, isValidating, isEdited, error,
  onValidateNow, onResetEdits,
}: Props) {
  const [expanded, setExpanded] = useState(false);

  // Idle / not-yet-validated / errored states all collapse to a
  // single compact strip — no large card cluttering the panel.
  if (error) {
    return (
      <div className="vs-strip vs-strip-error">
        <span className="vs-label">Validation</span>
        <span className="vs-error">{error}</span>
        <button
          type="button" className="vs-btn-sm"
          onClick={onValidateNow} disabled={isValidating}
        >
          Retry
        </button>
      </div>
    );
  }
  if (validation === null) {
    return (
      <div className="vs-strip">
        <span className="vs-label">Validation</span>
        <span className="vs-empty">
          {isValidating ? "running…" : "not run yet"}
        </span>
        <button
          type="button" className="vs-btn-sm"
          onClick={onValidateNow} disabled={isValidating}
        >
          Validate now
        </button>
      </div>
    );
  }

  const hard = validation.hard_violation_count;
  const soft = validation.soft_violation_count;
  const issues = hard + soft;
  // 3-state outcome: error (hard violation), warning (soft only),
  // or valid (none). Drives the chip colour + label.
  const outcome = hard > 0 ? "error" : soft > 0 ? "warning" : "valid";
  const top = pickTopIssue(validation);

  return (
    <div className={`vs-card vs-card-${outcome}`}>
      <div className="vs-card-head">
        <span className={`vs-chip vs-chip-${outcome}`}>
          {outcome === "valid"
            ? "VALID"
            : outcome === "warning" ? "WARNING" : "ERROR"}
        </span>
        <span className="vs-count">
          {issues === 0 ? "0 issues" : `${issues} issue${issues === 1 ? "" : "s"}`}
        </span>
        <span className="vs-score">
          score <strong>{validation.design_score.toFixed(1)}</strong>
        </span>
        <div className="vs-actions">
          <button
            type="button" className="vs-btn-sm"
            onClick={onValidateNow} disabled={isValidating}
          >
            {isValidating ? "…" : "Revalidate"}
          </button>
          <button
            type="button" className="vs-btn-sm"
            onClick={onResetEdits} disabled={!isEdited}
            title="Discard edits and reload pristine layout"
          >
            Reset
          </button>
        </div>
      </div>

      {top && !expanded && (
        <div className="vs-top-issue">
          <span className="vs-top-issue-id">{top.rule_id}</span>
          <span className="vs-top-issue-msg">{top.message}</span>
        </div>
      )}

      <button
        type="button"
        className="vs-toggle"
        onClick={() => setExpanded((v) => !v)}
      >
        {expanded ? "Hide details" : "View validation details"}
      </button>

      {expanded && (
        <ul className="vs-rule-list">
          {validation.rules.map((r) => (
            <li
              key={r.rule_id}
              className={`vs-rule vs-rule-${r.status}`}
            >
              <div className="vs-rule-head">
                <span className="vs-rule-id">{r.rule_id}</span>
                <span className="vs-rule-status">{r.status}</span>
                {r.score_delta !== 0 && (
                  <span className="vs-rule-delta">
                    {r.score_delta > 0 ? "+" : ""}
                    {r.score_delta.toFixed(1)}
                  </span>
                )}
              </div>
              <div className="vs-rule-msg">{r.message}</div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}


// 0.1.42 perf: memoised so a pan / drag preview that doesn't
// change validation state skips this re-render entirely. The
// callbacks come from App with stable identities (useCallback).
const ValidationSummary = memo(ValidationSummaryImpl);
export default ValidationSummary;
