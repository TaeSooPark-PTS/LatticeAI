import * as React from "react";
import "@/i18n/workspace";
import { useQuery } from "@tanstack/react-query";
import { latticeApi } from "@/api/client";
import { ActionButton, DataPanel, EntityList } from "@/components/primitives";
import { Badge } from "@/components/ui/badge";
import { InstalledAutomations } from "@/features/act/InstalledAutomations";
import { approvalActionLabel, firstString, humanRunTitle, runStatusLabel } from "@/features/act/actHelpers";
import { useAppStore } from "@/store/appStore";
import { asArray, shortId } from "@/lib/utils";
import { t, type Language } from "@/i18n";

export function RunsListPanel() {
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  const runtime = useQuery({ queryKey: ["agentRuntime"], queryFn: latticeApi.agentRuntime });
  const workflows = useQuery({ queryKey: ["workflowRuns"], queryFn: latticeApi.workflowRuns });
  const activity = useQuery({ queryKey: ["activityRuns"], queryFn: () => latticeApi.activityRuns(20) });
  const pending = useQuery({ queryKey: ["permissions"], queryFn: latticeApi.permissionsPending });

  const activityData = (activity.data?.data || activity.data) as Record<string, unknown> | undefined;
  const activityRuns = asArray<Record<string, unknown>>(activityData?.runs);
  const agentRuns = asArray<Record<string, unknown>>((runtime.data?.data as Record<string, unknown>)?.runs);
  const workflowRuns = asArray<Record<string, unknown>>((workflows.data?.data as Record<string, unknown>)?.runs);

  const combinedRuns = activityRuns.length > 0 ? activityRuns : [
    ...agentRuns.map((r) => ({ ...r, source: "agent" })),
    ...workflowRuns.map((r) => ({ ...r, source: "workflow" })),
  ];

  return (
    <div className="space-y-6">
      <DataPanel title={t(language, "act.panel.approvalInbox")} result={pending.data} className="is-attention">
        {(data) => {
          const pendingMap = ((data as Record<string, unknown>).pending || {}) as Record<string, unknown>;
          const rows = Object.entries(pendingMap);
          return rows.length ? (
            <div className="grid gap-3">
              {rows.map(([token, value], index) => (
                <div key={token} className="flex flex-wrap items-center justify-between gap-4 rounded-lg border border-amber-500/30 bg-card p-4 shadow-sm">
                  <div className="space-y-1">
                    <div className="font-semibold text-sm flex items-center gap-2">
                      <span className="h-2 w-2 shrink-0 rounded-full bg-amber-500" aria-hidden="true" />
                      {mode === "basic" ? t(language, "act.approval.request", { index: index + 1 }) : shortId(token, 16)}
                    </div>
                    <div className="mt-1">
                      <HumanPermissionDetails data={(value || {}) as Record<string, unknown>} language={language} />
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <ActionButton label={t(language, "act.action.approve")} action={() => latticeApi.approvePermission(token)} invalidate={["permissions"]} />
                    <ActionButton label={t(language, "act.action.deny")} action={() => latticeApi.denyPermission(token)} invalidate={["permissions"]} variant="destructive" />
                  </div>
                </div>
              ))}
            </div>
          ) : <EntityList items={[]} />;
        }}
      </DataPanel>

      <InstalledAutomations language={language} />

      <DataPanel title={t(language, "act.panel.combinedRuns")} result={activity.data?.ok !== false ? activity.data : runtime.data}>
        {() => <RunList runs={combinedRuns} kind="combined" />}
      </DataPanel>
    </div>
  );
}

function HumanPermissionDetails({ data, language }: { data: Record<string, unknown>; language: Language }) {
  const actionLabel = approvalActionLabel(data, language);
  const targetPath = firstString(data.path, data.target, data.file, data.resource);
  const filename = targetPath ? (targetPath.split("/").pop() || targetPath) : "";
  const requester = firstString(data.user_email, data.requested_by, data.actor);
  const reason = firstString(data.reason, data.purpose, data.explanation);

  return (
    <div className="space-y-1.5 text-sm">
      <div className="font-medium text-foreground flex flex-wrap items-center gap-2">
        <Badge variant="warning">{actionLabel}</Badge>
        {targetPath ? (
          <code className="bg-muted px-1.5 py-0.5 rounded text-xs break-all" title={targetPath}>
            <strong>{filename}</strong>
          </code>
        ) : null}
      </div>
      {reason ? <p className="text-xs text-muted-foreground">{reason}</p> : null}
      {requester ? <p className="text-[11px] text-muted-foreground/80">{t(language, "act.approval.requestedBy")}: {requester}</p> : null}
    </div>
  );
}

function RunList({ runs, kind }: { runs: Array<Record<string, unknown>>; kind: "agent" | "workflow" | "combined" }) {
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  if (!runs.length) return <EntityList items={[]} />;
  return (
    <div className="grid gap-2">
      {runs.slice(0, 10).map((run, index) => {
        const id = String(run.run_id || run.id);
        const status = String(run.status || "");
        const label = runStatusLabel(status, language);
        const isWorkflow = run.source === "workflow" || kind === "workflow" || Boolean(run.workflow_id);
        const title = humanRunTitle(run)
          || (mode === "basic" ? t(language, "act.run.fallbackName", { index: index + 1 }) : shortId(id, 18));
        const isSuccess = /^(ok|retried_ok|succeed(?:ed)?|complet(?:e|ed)?)$/i.test(status);
        const needsApproval = status === "awaiting_approval" || status === "waiting_approval";
        return (
          <div key={id} className="rounded-md border border-border bg-background p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="font-medium flex items-center gap-2">
                <Badge variant="muted">{isWorkflow ? t(language, "act.tab.recipes") : t(language, "act.tab.goals")}</Badge>
                <span>{title}</span>
              </div>
              <Badge variant={isSuccess ? "success" : needsApproval ? "warning" : "muted"}>{label}</Badge>
            </div>
            {mode === "basic" ? null : <div className="mt-1 text-xs text-muted-foreground">{shortId(id, 18)}</div>}
            <div className="mt-2 flex flex-wrap gap-2">
              <ActionButton label={t(language, "act.action.stop")} action={() => isWorkflow ? latticeApi.stopWorkflowRun(id) : latticeApi.stopAgentRun(id)} />
              {needsApproval && isWorkflow ? (
                <>
                  <ActionButton label={t(language, "act.action.resumeApproved")} action={() => latticeApi.resumeWorkflowRun(id, true)} />
                  <ActionButton label={t(language, "act.action.resumeDenied")} action={() => latticeApi.resumeWorkflowRun(id, false)} variant="destructive" />
                </>
              ) : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}
