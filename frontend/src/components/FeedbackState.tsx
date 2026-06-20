import * as React from "react";
import { AlertTriangle, Inbox, RotateCcw } from "lucide-react";
import { t, type Language } from "@/i18n";

export type FeedbackTone = "empty" | "error";

/**
 * Shared empty / error feedback surface so every view tells the user what just
 * happened and what to do next, instead of a blank or silent failure.
 */
export function FeedbackState({
  tone,
  language,
  title,
  body,
  actionLabel,
  onAction,
}: {
  tone: FeedbackTone;
  language: Language;
  title: string;
  body?: string;
  actionLabel?: string;
  onAction?: () => void;
}) {
  const isError = tone === "error";
  const resolvedActionLabel = actionLabel || (isError ? t(language, "feedback.retry") : undefined);
  return (
    <div className={`feedback-state is-${tone}`} role={isError ? "alert" : "status"}>
      <span className="feedback-state-icon" aria-hidden="true">
        {isError ? <AlertTriangle className="h-4 w-4" /> : <Inbox className="h-4 w-4" />}
      </span>
      <div className="feedback-state-body">
        <strong>{title}</strong>
        {body ? <span>{body}</span> : null}
      </div>
      {resolvedActionLabel && onAction ? (
        <button type="button" className="feedback-state-action" onClick={onAction}>
          {isError ? <RotateCcw className="h-3.5 w-3.5" /> : null}
          {resolvedActionLabel}
        </button>
      ) : null}
    </div>
  );
}
