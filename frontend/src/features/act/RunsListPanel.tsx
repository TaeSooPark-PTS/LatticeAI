import * as React from "react";
import "@/i18n/workspace";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { latticeApi } from "@/api/client";
import { ActionButton, DataPanel, EntityList } from "@/components/primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { InstalledAutomations } from "@/features/act/InstalledAutomations";
import { ACTIVE_RUN_STATUSES, approvalActionLabel, firstString, humanRunTitle, runStatusLabel } from "@/features/act/actHelpers";
import { useAppStore } from "@/store/appStore";
import { asArray, shortId } from "@/lib/utils";
import { t, type Language } from "@/i18n";

const ACTIVITY_LIMIT = 100;
const PAGE_SIZE = 10;

const RUN_FILTERS = {
  active: ["queued", "pending", "running", "cancelling"],
  approval: ["awaiting_approval", "waiting_approval"],
  done: ["ok", "retried_ok", "succeed", "succeeded", "complet", "complete", "completed"],
  failed: ["failed", "error", "rejected", "interrupted"],
  cancelled: ["cancelled"],
};

type RunFilter = "all" | keyof typeof RUN_FILTERS;

export function RunsListPanel() {
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  const runtime = useQuery({ queryKey: ["agentRuntime"], queryFn: latticeApi.agentRuntime });
  const workflows = useQuery({ queryKey: ["workflowRuns"], queryFn: latticeApi.workflowRuns });
  const activity = useQuery({ queryKey: ["activityRuns"], queryFn: () => latticeApi.activityRuns(ACTIVITY_LIMIT) });
  const pending = useQuery({ queryKey: ["permissions"], queryFn: latticeApi.permissionsPending });

  const activityData = activity.data?.data as Record<string, unknown> | undefined;
  const activityRuns = asArray<Record<string, unknown>>(activityData?.runs);
  const agentRuns = asArray<Record<string, unknown>>((runtime.data?.data as Record<string, unknown>)?.runs);
  const workflowRuns = asArray<Record<string, unknown>>((workflows.data?.data as Record<string, unknown>)?.runs);

  const activityError = activity.data && !activity.data.ok ? activity.data.error || t(language, "ui.status.unavailable") : "";
  const fallbackError = [runtime.data, workflows.data].find((result) => result && !result.ok);
  const useActivity = !activityError && activityRuns.length > 0;
  const loading = !activity.data || (!useActivity && (!runtime.data || !workflows.data));
  const combinedRuns = useActivity ? activityRuns : [
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

      <Card className="data-panel overflow-hidden">
        <CardHeader>
          <CardTitle>{t(language, "act.panel.combinedRuns")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {activityError ? (
            <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-warning/40 p-3 text-sm">
              <div>
                <p className="font-medium">{t(language, "act.runs.loadError")}</p>
                <p className="text-xs text-muted-foreground">{activityError}</p>
                {combinedRuns.length ? <p className="text-xs text-muted-foreground">{t(language, "act.runs.partial")}</p> : null}
              </div>
              <Button variant="outline" size="sm" onClick={() => void activity.refetch()}>{t(language, "act.runs.retry")}</Button>
            </div>
          ) : null}
          {!loading && !useActivity && fallbackError ? (
            <p role="alert" className="text-sm text-warning">
              {t(language, "act.runs.sourceError", { reason: fallbackError.error || t(language, "ui.status.unavailable") })}
            </p>
          ) : null}
          {loading ? <p role="status" className="text-sm text-muted-foreground">{t(language, "ui.loading")}</p> : null}
          {!loading && (combinedRuns.length > 0 || (!activityError && !fallbackError)) ? <RunList runs={combinedRuns} /> : null}
          {useActivity && activityRuns.length >= ACTIVITY_LIMIT ? (
            <p className="text-xs text-muted-foreground">{t(language, "act.runs.capped", { count: ACTIVITY_LIMIT })}</p>
          ) : null}
        </CardContent>
      </Card>
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

function WorkflowRunActions({ runId, status, language }: { runId: string; status: string; language: Language }) {
  const qc = useQueryClient();
  const [notice, setNotice] = React.useState<{ ok: boolean; text: string } | null>(null);
  const action = useMutation({
    mutationFn: async (kind: "stop" | "approve" | "deny") => {
      const result = kind === "stop"
        ? await latticeApi.stopWorkflowRun(runId)
        : await latticeApi.resumeWorkflowRun(runId, kind === "approve");
      const body = result.data as Record<string, unknown>;
      if (!result.ok) setNotice({ ok: false, text: result.error || t(language, "ui.status.unavailable") });
      else if (kind === "stop" && body.stopped !== true) {
        setNotice({ ok: false, text: t(language, body.reason === "run already finished" ? "act.run.alreadyFinished" : "act.run.notStopped") });
      } else setNotice({ ok: true, text: t(language, kind === "stop" ? "act.run.stopped" : "ui.done") });
      await Promise.all(
        ["workflowRuns", "activityRuns", "agentRuntime", "workflowDefinitionRuns", "workflowReplay"]
          .map((key) => qc.invalidateQueries({ queryKey: [key] })),
      );
    },
  });
  const active = ACTIVE_RUN_STATUSES.includes(status);
  if (!active && !notice) return null;
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2">
      {active ? (
        <Button variant="outline" disabled={action.isPending} onClick={() => action.mutate("stop")}>{t(language, "act.action.stop")}</Button>
      ) : null}
      {status === "awaiting_approval" ? (
        <>
          <Button variant="outline" disabled={action.isPending} onClick={() => action.mutate("approve")}>{t(language, "act.action.resumeApproved")}</Button>
          <Button variant="destructive" disabled={action.isPending} onClick={() => action.mutate("deny")}>{t(language, "act.action.resumeDenied")}</Button>
        </>
      ) : null}
      {notice ? <span role="status" className={`text-xs ${notice.ok ? "text-success" : "text-warning"}`}>{notice.text}</span> : null}
    </div>
  );
}

function RunList({ runs }: { runs: Array<Record<string, unknown>> }) {
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  const [filter, setFilter] = React.useState<RunFilter>("all");
  const [visible, setVisible] = React.useState(PAGE_SIZE);
  if (!runs.length) return <EntityList items={[]} />;
  const matching = filter === "all" ? runs : runs.filter((run) => RUN_FILTERS[filter].includes(String(run.status || "")));
  return (
    <div className="grid gap-2">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <label className="grid gap-1 text-xs text-muted-foreground">
          {t(language, "act.runs.filter")}
          <select
            className="h-9 rounded-md border border-input bg-background px-2 text-sm text-foreground"
            value={filter}
            onChange={(event) => {
              setFilter(event.target.value as RunFilter);
              setVisible(PAGE_SIZE);
            }}
          >
            {(["all", "active", "approval", "done", "failed", "cancelled"] as const).map((option) => (
              <option key={option} value={option}>{t(language, `act.runs.filter.${option}`)}</option>
            ))}
          </select>
        </label>
        <span role="status" className="text-xs tabular-nums text-muted-foreground">
          {t(language, "act.runs.count", { shown: Math.min(visible, matching.length), total: matching.length })}
        </span>
      </div>
      {matching.length ? null : <p className="text-sm text-muted-foreground">{t(language, "act.runs.noMatch")}</p>}
      {matching.slice(0, visible).map((run, index) => {
        const id = String(run.run_id || run.id);
        const status = String(run.status || "");
        const label = runStatusLabel(status, language);
        const isWorkflow = run.source === "workflow" || Boolean(run.workflow_id);
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
            {isWorkflow ? <WorkflowRunActions runId={id} status={status} language={language} /> : null}
          </div>
        );
      })}
      {matching.length > visible ? (
        <Button variant="outline" onClick={() => setVisible(visible + PAGE_SIZE)}>{t(language, "act.runs.more")}</Button>
      ) : null}
    </div>
  );
}
