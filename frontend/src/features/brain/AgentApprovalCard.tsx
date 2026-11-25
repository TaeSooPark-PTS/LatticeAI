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

export function formatCountdown(remainingMs: number): string {
  const totalSeconds = Math.max(0, Math.floor(remainingMs / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

const URGENT_REMAINING_MS = 120_000;

export function AgentApprovalCard({
  language,
  approval,
  onResolved,
  onReplan,
}: {
  language: Language;
  approval: MessageApproval;
  onResolved: (resolution: ApprovalResolution) => void;
  onReplan?: (message: string) => void;
}) {
  const [busy, setBusy] = React.useState(false);
  const [editOpen, setEditOpen] = React.useState(false);
  const [editText, setEditText] = React.useState("");
  const [editInvalid, setEditInvalid] = React.useState(false);
  const primaryRef = React.useRef<HTMLButtonElement>(null);

  React.useEffect(() => {
    if (approval.status === "pending") primaryRef.current?.focus();
  }, []);

  const pending = approval.status === "pending";
  const expiryMs = React.useMemo(() => {
    if (!approval.expiresAt) return null;
    const parsed = Date.parse(approval.expiresAt);
    return Number.isFinite(parsed) ? parsed : null;
  }, [approval.expiresAt]);
  const [now, setNow] = React.useState(() => Date.now());

  React.useEffect(() => {
    if (!pending || expiryMs === null) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [pending, expiryMs]);

  const remainingMs = expiryMs === null ? null : expiryMs - now;
  const urgent = pending && remainingMs !== null && remainingMs > 0 && remainingMs < URGENT_REMAINING_MS;

  const expiredLocallyRef = React.useRef(false);
  React.useEffect(() => {
    if (!pending || remainingMs === null || remainingMs > 0 || expiredLocallyRef.current) return;
    expiredLocallyRef.current = true;
    onResolved({ kind: "expired" });
  }, [pending, remainingMs, onResolved]);

  async function resolve(decision: { approve: boolean; editedPlan?: Record<string, unknown> }) {
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
    const replanMessage = approval.status === "expired" ? approval.replanMessage || "" : "";
    return (
      <div
        className={`brain-approval-note is-${approval.status}`}
        role={approval.status === "approved" ? "status" : "alert"}
        data-testid="agent-approval-note"
      >
        <span>{t(language, noteKey, { reason: approval.errorReason || "" })}</span>
        {replanMessage && onReplan ? (
          <button
            type="button"
            className="brain-approval-replan"
            data-testid="approval-replan"
            onClick={() => onReplan(replanMessage)}
          >
            {t(language, "brain.approval.expiredReplan")}
          </button>
        ) : null}
      </div>
    );
  }

  const expiry = expiryTime(language, approval.expiresAt);
  return (
    <section
      className={`brain-approval-card ${urgent ? "is-urgent" : ""}`}
      aria-label={t(language, "brain.approval.aria")}
      data-testid="agent-approval-card"
    >
      <header className="brain-approval-head">
        <ShieldAlert className="h-4 w-4" aria-hidden="true" />
        <strong>{t(language, "brain.approval.title")}</strong>
        {remainingMs !== null ? (
          <span
            className={`brain-approval-countdown ${urgent ? "is-urgent" : ""}`}
            data-testid="approval-countdown"
          >
            {t(language, "brain.approval.countdown", { time: formatCountdown(remainingMs) })}
          </span>
        ) : null}
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
      {urgent ? (
        <small className="brain-approval-urgency" role="alert" data-testid="approval-urgency">
          {t(language, "brain.approval.countdown.warning")}
        </small>
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
