import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { History, PlayCircle, ShieldCheck, Workflow } from "lucide-react";
import { latticeApi } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { asArray } from "@/lib/utils";
import { t, type Language } from "@/i18n";

type LastExecution = {
  mode?: string;
  status?: string;
  summary?: string;
  run_id?: string | null;
  finished_at?: string;
};

type InstalledAutomation = {
  id: string;
  name: string;
  enabled: boolean;
  requires_user_enable: boolean;
  creates: string[];
  last_execution?: LastExecution | null;
};

type RunNowResponse = {
  workflow_id?: string;
  dry_run?: boolean;
  status?: string;
  review_item_id?: string;
  last_execution?: LastExecution;
};

function shortWhen(value?: string): string {
  if (!value) return "";
  return value.replace("T", " ").slice(0, 16);
}

/** Installed automations with dry-run-first "run now" + last execution line (backlog #6). */
export function InstalledAutomations({ language }: { language: Language }) {
  const qc = useQueryClient();
  const overview = useQuery({ queryKey: ["automationOverview"], queryFn: latticeApi.automationOverview });
  // Dry-run first: a card unlocks its real run only after a dry run reported
  // what would happen. Keyed per workflow so cards act independently.
  const [dryRunDone, setDryRunDone] = React.useState<Record<string, boolean>>({});
  const [lastResult, setLastResult] = React.useState<Record<string, RunNowResponse>>({});
  const runNow = useMutation({
    mutationFn: ({ id, dryRun }: { id: string; dryRun: boolean }) => latticeApi.runAutomationNow(id, dryRun),
    onSuccess: async (result, variables) => {
      const body = (result?.data || {}) as RunNowResponse;
      if (result?.ok) {
        if (variables.dryRun) setDryRunDone((prev) => ({ ...prev, [variables.id]: true }));
        setLastResult((prev) => ({ ...prev, [variables.id]: body }));
      }
      await qc.invalidateQueries({ queryKey: ["automationOverview"] });
    },
  });

  const data = (overview.data?.data || {}) as Record<string, unknown>;
  const installed = asArray<InstalledAutomation>(data.installed);

  return (
    <Card className="installed-automations xl:col-span-2" data-testid="installed-automations">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Workflow className="h-4 w-4" /> {t(language, "act.installed.title")}
        </CardTitle>
        <CardDescription>{t(language, "act.installed.subtitle")}</CardDescription>
      </CardHeader>
      <CardContent>
        {installed.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t(language, "act.installed.empty")}</p>
        ) : (
          <div className="grid gap-3 lg:grid-cols-2">
            {installed.map((automation) => {
              const id = String(automation.id || "");
              const isBusy = runNow.isPending && runNow.variables?.id === id;
              const unlockedLive = Boolean(dryRunDone[id]);
              const result = lastResult[id];
              const last = automation.last_execution || result?.last_execution || null;
              const modeLabel = last?.mode === "dry_run"
                ? t(language, "act.installed.mode.dry")
                : t(language, "act.installed.mode.live");
              return (
                <div key={id} className="rounded-lg border border-border bg-background/70 p-4" data-testid="installed-automation-card">
                  <div className="flex items-start justify-between gap-2">
                    <p className="min-w-0 truncate font-medium" title={automation.name}>{automation.name}</p>
                    <Badge variant={automation.enabled ? "success" : "muted"}>
                      {automation.enabled
                        ? t(language, "act.installed.onBadge")
                        : t(language, "act.installed.draftBadge")}
                    </Badge>
                  </div>
                  <p className="mt-2 flex items-start gap-1.5 text-xs text-muted-foreground" data-testid="automation-last-execution">
                    <History className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                    {last ? (
                      <span>
                        {t(language, "act.installed.lastRun")} · {modeLabel} · {String(last.status || "")}
                        {last.finished_at ? ` · ${shortWhen(last.finished_at)}` : ""}
                        {last.summary ? ` — ${last.summary}` : ""}
                      </span>
                    ) : (
                      <span>{t(language, "act.installed.noRuns")}</span>
                    )}
                  </p>
                  {result?.review_item_id ? (
                    <p className="mt-1 text-xs text-red-600" role="status">{t(language, "act.installed.failedReview")}</p>
                  ) : null}
                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    <Badge variant="success">
                      <ShieldCheck className="h-3 w-3" /> {t(language, "act.automation.local")}
                    </Badge>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={runNow.isPending}
                      onClick={() => runNow.mutate({ id, dryRun: true })}
                    >
                      <PlayCircle className="h-3.5 w-3.5" />
                      {isBusy && runNow.variables?.dryRun
                        ? t(language, "act.installed.dryRunning")
                        : t(language, "act.installed.runNow")}
                    </Button>
                    {unlockedLive ? (
                      <Button
                        size="sm"
                        disabled={runNow.isPending}
                        onClick={() => runNow.mutate({ id, dryRun: false })}
                      >
                        {isBusy && !runNow.variables?.dryRun
                          ? t(language, "act.installed.liveRunning")
                          : t(language, "act.installed.runLive")}
                      </Button>
                    ) : null}
                  </div>
                  <p className="mt-2 text-[10px] text-muted-foreground">{t(language, "act.installed.dryRunHint")}</p>
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
