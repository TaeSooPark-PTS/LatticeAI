import { COPY, t, type Language } from "@/i18n";

export function firstString(...values: unknown[]): string {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

export const ACTIVE_RUN_STATUSES = ["queued", "running", "cancelling", "awaiting_approval"];

export function downloadJson(data: unknown, name: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${name.replace(/[\\/:*?"<>|]+/g, "-").trim() || "workflow"}.json`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export function runStatusLabel(status: string, language: Language) {
  const key = `act.runStatus.${status}`;
  const label = t(language, key);
  return label === key ? t(language, "act.status.unknown") : label;
}

export function humanRunTitle(run: Record<string, unknown>): string {
  const direct = firstString(run.workflow_name, run.name, run.goal, run.title, run.query);
  if (direct) return direct;
  const input = run.input;
  if (typeof input === "string" && input.trim()) return input.trim();
  if (input && typeof input === "object" && !Array.isArray(input)) {
    const nested = input as Record<string, unknown>;
    return firstString(nested.goal, nested.name, nested.title, nested.query, nested.text, nested.prompt);
  }
  return "";
}

export function approvalActionLabel(data: Record<string, unknown>, language: Language): string {
  const rawAction = firstString(data.action, data.tool, data.type) || "";
  const actionToken = rawAction.toLowerCase().replace(/[\s-]+/g, "_");
  const actionCopy = actionToken
    ? COPY[language]?.[`act.approval.action.${actionToken}`] ?? COPY.ko[`act.approval.action.${actionToken}`]
    : undefined;
  return (
    actionCopy ||
    firstString(data.action_label, rawAction) ||
    t(language, "act.approval.defaultAction")
  );
}
