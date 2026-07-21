import * as React from "react";
import { PencilLine, ShieldAlert } from "lucide-react";

import { t, type Language } from "@/i18n";
import { type ApprovalResolution, resolveApprovalRequest } from "./approvalFlow";
import type { MessageApproval } from "./types";

function expiryTime(language: Language, expiresAt: string): string {
  if (!expiresAt) return "";
  const date = new Date(expiresAt);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString(language === "ko" ? "ko-KR" : "en-US", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

// Inline governance surface for an awaiting_approval agent run (backlog #2).
// Not a modal: the run is parked server-side behind a single-use token, so the
// card lives in the conversation where the pause happened. The primary action
// receives focus when the card appears; terminal states (approved/cancelled/
// expired/error) persist on the message so the card stays honest across
// re-renders.
export function AgentApprovalCard({
  language,
  approval,
  onResolved,
}: {
  language: Language;
  approval: MessageApproval;
  onResolved: (resolution: ApprovalResolution) => void;
}) {
  const [busy, setBusy] = React.useState(false);
  const [editOpen, setEditOpen] = React.useState(false);
  const [editText, setEditText] = React.useState("");
  const [editInvalid, setEditInvalid] = React.useState(false);
  const primaryRef = React.useRef<HTMLButtonElement>(null);

  // Focus the primary action only when the card first appears (not on later
  // re-renders), so keyboard users land on the decision immediately.
  React.useEffect(() => {
    if (approval.status === "pending") primaryRef.current?.focus();
    // Intentionally mount-only.
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function resolve(decision: { approve: boolean; editedPlan?: Record<string, unknown> }) {
    if (busy) return;
    setBusy(true);
    try {
      onResolved(await resolveApprovalRequest(approval, decision));
    } finally {
      setBusy(false);
    }
  }

  function openEditor() {
    setEditText(
      JSON.stringify(
        approval.plan ?? { goal: approval.planSummary, steps: [] },
        null,
        2,
      ),
    );
    setEditInvalid(false);
    setEditOpen(true);
  }

  function submitEdited() {
    let parsed: unknown;
    try {
      parsed = JSON.parse(editText);
    } catch {
      setEditInvalid(true);
      return;
    }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      setEditInvalid(true);
      return;
    }
    setEditInvalid(false);
    void resolve({ approve: true, editedPlan: parsed as Record<string, unknown> });
  }

  if (approval.status !== "pending") {
    const noteKey =
      approval.status === "approved" ? "brain.approval.resolved"
      : approval.status === "cancelled" ? "brain.approval.cancelled"
      : approval.status === "expired" ? "brain.approval.expired"
      : "brain.approval.error";
    return (
      <p
        className={`brain-approval-note is-${approval.status}`}
        role={approval.status === "approved" ? "status" : "alert"}
        data-testid="agent-approval-note"
      >
        {t(language, noteKey, { reason: approval.errorReason || "" })}
      </p>
    );
  }

  const expiry = expiryTime(language, approval.expiresAt);
  return (
    <section
      className="brain-approval-card"
      aria-label={t(language, "brain.approval.aria")}
      data-testid="agent-approval-card"
    >
      <header className="brain-approval-head">
        <ShieldAlert className="h-4 w-4" aria-hidden="true" />
        <strong>{t(language, "brain.approval.title")}</strong>
      </header>
      {approval.planSummary ? (
        <div className="brain-approval-summary">
          <span className="brain-approval-summary-label">{t(language, "brain.approval.summaryLabel")}</span>
          <pre data-testid="approval-plan-summary">{approval.planSummary}</pre>
        </div>
      ) : null}
      {expiry ? (
        <small className="brain-approval-expiry">{t(language, "brain.approval.expiry", { time: expiry })}</small>
      ) : null}
      {editOpen ? (
        <div className="brain-approval-editor">
          <label htmlFor="brain-approval-plan-editor">{t(language, "brain.approval.edit.label")}</label>
          <textarea
            id="brain-approval-plan-editor"
            data-testid="approval-edit-textarea"
            value={editText}
            rows={8}
            spellCheck={false}
            onChange={(event) => setEditText(event.target.value)}
          />
          {editInvalid ? (
            <small className="brain-approval-edit-error" role="alert">
              {t(language, "brain.approval.edit.invalid")}
            </small>
          ) : null}
          <div className="brain-approval-actions">
            <button
              type="button"
              className="brain-approval-primary"
              data-testid="approval-edit-run"
              disabled={busy}
              onClick={submitEdited}
            >
              {busy ? t(language, "brain.approval.running") : t(language, "brain.approval.edit.run")}
            </button>
            <button type="button" disabled={busy} onClick={() => setEditOpen(false)}>
              {t(language, "brain.approval.edit.close")}
            </button>
          </div>
        </div>
      ) : (
        <div className="brain-approval-actions">
          <button
            ref={primaryRef}
            type="button"
            className="brain-approval-primary"
            data-testid="approval-approve"
            disabled={busy}
            onClick={() => void resolve({ approve: true })}
          >
            {busy ? t(language, "brain.approval.running") : t(language, "brain.approval.approve")}
          </button>
          <button type="button" data-testid="approval-edit" disabled={busy} onClick={openEditor}>
            <PencilLine className="h-3.5 w-3.5" aria-hidden="true" />
            {t(language, "brain.approval.edit")}
          </button>
          <button type="button" data-testid="approval-cancel" disabled={busy} onClick={() => void resolve({ approve: false })}>
            {t(language, "brain.approval.cancel")}
          </button>
        </div>
      )}
    </section>
  );
}
