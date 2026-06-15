import type * as React from "react";
import type { ReviewItem, ReviewSourceFilter, ReviewStatusFilter } from "@/api/client";
import type { Badge } from "@/components/ui/badge";

export type ReviewAction = "approve" | "dismiss" | "snooze" | "unsnooze" | "run_now";

export const reviewStatusFilters: Array<{ id: ReviewStatusFilter; label: string }> = [
  { id: "pending", label: "Pending" },
  { id: "snoozed", label: "Snoozed" },
  { id: "all", label: "All" },
  { id: "approved", label: "Approved" },
  { id: "dismissed", label: "Dismissed" },
];

export const reviewSourceFilters: Array<{ id: ReviewSourceFilter; label: string }> = [
  { id: "all", label: "All sources" },
  { id: "workflow_run", label: "Workflow" },
  { id: "trigger", label: "Trigger" },
  { id: "kg_change_digest", label: "KG digest" },
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
  if (source === "kg_change_digest") return "KG digest";
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
