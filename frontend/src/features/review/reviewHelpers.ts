import type * as React from "react";
import type { ReviewItem, ReviewSourceFilter, ReviewStatusFilter } from "@/api/client";
import type { Badge } from "@/components/ui/badge";

export type ReviewAction = "approve" | "dismiss" | "snooze" | "unsnooze" | "run_now";

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
];

export function reviewStatusVariant(status: string): React.ComponentProps<typeof Badge>["variant"] {
  if (status === "pending") return "warning";
  if (status === "snoozed") return "muted";
  if (status === "approved") return "success";
  if (status === "dismissed") return "danger";
  return "muted";
}

export function reviewSourceLabel(source?: string) {
  if (source === "workflow_run") return "Workflow run";
  if (source === "trigger") return "Trigger";
  if (source === "kg_change_digest") return "Knowledge digest";
  if (source === "chat_followup") return "Brain chat";
  if (source === "agent_followup") return "Agent follow-up";
  return source || "Automation";
}

export function reviewSourceDetail(provenance: Record<string, unknown>, source?: string) {
  const detail = provenance.source_detail;
  if (detail != null && String(detail).trim()) return String(detail);
  const triggerId = provenance.trigger_id;
  if (triggerId != null && String(triggerId).trim()) return String(triggerId);
  return reviewSourceLabel(source);
}

export function defaultSnoozeUntil() {
  const until = new Date();
  until.setDate(until.getDate() + 1);
  return until.toISOString();
}

export function formatSnoozedUntil(value?: string | null) {
  if (!value) return "Snoozed";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return `Snoozed until ${value}`;
  return `Snoozed until ${new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date)}`;
}

export function isActionableReview(item: ReviewItem) {
  return item.effective_status === "pending" || item.effective_status === "snoozed";
}

export function hasRunBefore(item: ReviewItem) {
  const payload = item.payload || {};
  const provenance = item.provenance || {};
  return Boolean(payload.last_run_id || provenance.run_id);
}
