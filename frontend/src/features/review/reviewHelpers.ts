import type * as React from "react";
import type { ReviewItem, ReviewSourceFilter, ReviewStatusFilter } from "@/api/client";
import type { Badge } from "@/components/ui/badge";
import { t, type Language } from "@/i18n";

export type ReviewAction = "approve" | "dismiss" | "snooze" | "unsnooze" | "run_now";

// Structured feedback for a review action so the UI never has to guess the
// tone from message text: the caller records success/failure explicitly.
export type ReviewFeedback = {
  tone: "success" | "error";
  message: string;
  detail?: string;
};

export const reviewStatusFilters: Array<{ id: ReviewStatusFilter; labelKey: string }> = [
  { id: "pending", labelKey: "review.filter.status.pending" },
  { id: "snoozed", labelKey: "review.filter.status.snoozed" },
  { id: "all", labelKey: "review.filter.status.all" },
  { id: "approved", labelKey: "review.filter.status.approved" },
  { id: "dismissed", labelKey: "review.filter.status.dismissed" },
];

export const reviewSourceFilters: Array<{ id: ReviewSourceFilter; labelKey: string }> = [
  { id: "all", labelKey: "review.filter.source.all" },
  { id: "workflow_run", labelKey: "review.filter.source.workflow_run" },
  { id: "trigger", labelKey: "review.filter.source.trigger" },
  { id: "kg_change_digest", labelKey: "review.filter.source.kg_change_digest" },
  { id: "chat_followup", labelKey: "review.filter.source.chat_followup" },
  { id: "agent_followup", labelKey: "review.filter.source.agent_followup" },
  { id: "change_proposal", labelKey: "review.filter.source.change_proposal" },
];

export function reviewStatusVariant(status: string): React.ComponentProps<typeof Badge>["variant"] {
  if (status === "pending") return "warning";
  if (status === "snoozed") return "muted";
  if (status === "approved") return "success";
  if (status === "dismissed") return "danger";
  return "muted";
}

const REVIEW_STATUS_LABEL_KEYS: Record<string, string> = {
  pending: "review.itemStatus.pending",
  snoozed: "review.itemStatus.snoozed",
  approved: "review.itemStatus.approved",
  dismissed: "review.itemStatus.dismissed",
};

export function reviewStatusLabel(language: Language, status: string) {
  const key = REVIEW_STATUS_LABEL_KEYS[status];
  return key ? t(language, key) : status;
}

const REVIEW_SOURCE_LABEL_KEYS: Record<string, string> = {
  workflow_run: "review.source.workflow_run",
  trigger: "review.source.trigger",
  kg_change_digest: "review.source.kg_change_digest",
  chat_followup: "review.source.chat_followup",
  agent_followup: "review.source.agent_followup",
  change_proposal: "review.source.change_proposal",
};

export function reviewSourceLabel(language: Language, source?: string) {
  const key = source ? REVIEW_SOURCE_LABEL_KEYS[source] : undefined;
  if (key) return t(language, key);
  return source || t(language, "review.source.automation");
}

export function reviewSourceDetail(language: Language, provenance: Record<string, unknown>, source?: string) {
  const detail = provenance.source_detail;
  if (detail != null && String(detail).trim()) return String(detail);
  const triggerId = provenance.trigger_id;
  if (triggerId != null && String(triggerId).trim()) return String(triggerId);
  return reviewSourceLabel(language, source);
}

// Human labels for governance metadata. Unknown values fall back to the raw
// string so new backend classes still show something meaningful.
const REVIEW_RISK_LABEL_KEYS: Record<string, string> = {
  read: "review.risk.read",
  write: "review.risk.write",
  write_scoped: "review.risk.write_scoped",
  exec: "review.risk.exec",
  destructive: "review.risk.destructive",
};

export function reviewRiskLabel(language: Language, risk: unknown): string {
  const value = typeof risk === "string" ? risk.trim() : "";
  if (!value) return "";
  const key = REVIEW_RISK_LABEL_KEYS[value];
  return key ? t(language, key) : value;
}

const REVIEW_CHANGE_LABEL_KEYS: Record<string, string> = {
  read: "review.change.read",
  additive: "review.change.additive",
  mutation: "review.change.mutation",
  destructive: "review.change.destructive",
  exec: "review.change.exec",
};

export function reviewChangeClassLabel(language: Language, changeClass: unknown): string {
  const value = typeof changeClass === "string" ? changeClass.trim() : "";
  if (!value) return "";
  const key = REVIEW_CHANGE_LABEL_KEYS[value];
  return key ? t(language, key) : value;
}

export function defaultSnoozeUntil() {
  const until = new Date();
  until.setDate(until.getDate() + 1);
  return until.toISOString();
}

export function formatSnoozedUntil(language: Language, value?: string | null) {
  if (!value) return t(language, "review.snoozed.badge");
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return t(language, "review.snoozed.until", { value });
  const formatted = new Intl.DateTimeFormat(language === "ko" ? "ko-KR" : "en-US", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
  return t(language, "review.snoozed.until", { value: formatted });
}

export function isActionableReview(item: ReviewItem) {
  return item.effective_status === "pending" || item.effective_status === "snoozed";
}

export function hasRunBefore(item: ReviewItem) {
  const payload = item.payload || {};
  const provenance = item.provenance || {};
  return Boolean(payload.last_run_id || provenance.run_id);
}
