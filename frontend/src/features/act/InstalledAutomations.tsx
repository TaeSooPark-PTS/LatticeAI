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

// Exported for tests: every render-site call is guarded by a truthy check, so
// the empty-input contract is only reachable directly.
export function shortWhen(value?: string): string {
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
    <Card className="installed-automations" data-testid="installed-automations">
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
          <div className="flex flex-col gap-4">
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
                <div key={id} className="rounded-xl border border-border bg-card p-4 shadow-sm space-y-3 flex flex-col justify-between" data-testid="installed-automation-card">
                  <div className="space-y-2">
                    <div className="flex items-start justify-between gap-2">
                      <p className="min-w-0 font-bold text-sm text-foreground truncate" title={automation.name}>{automation.name}</p>
                      <div className="flex items-center gap-1.5 shrink-0">
                        <Badge variant="success" className="text-[10px]">
                          <ShieldCheck className="h-3 w-3 mr-0.5" /> {t(language, "act.automation.local")}
                        </Badge>
                        <Badge variant={automation.enabled ? "success" : "muted"} className="text-[10px]">
                          {automation.enabled
                            ? t(language, "act.installed.onBadge")
                            : t(language, "act.installed.draftBadge")}
                        </Badge>
                      </div>
                    </div>

                    <div className="rounded-md border border-border/50 bg-muted/30 p-2.5 text-xs space-y-1" data-testid="automation-last-execution">
                      <div className="flex items-center gap-1.5 text-muted-foreground font-medium">
                        <History className="h-3.5 w-3.5 text-primary shrink-0" aria-hidden="true" />
                        <span>{t(language, "act.installed.lastRun")}</span>
                      </div>
                      {last ? (
                        <p className="text-[11px] text-foreground/90 pl-5">
                          <span className="font-semibold text-primary">{modeLabel}</span> · {String(last.status || "")}
                          {last.finished_at ? ` · ${shortWhen(last.finished_at)}` : ""}
                          {last.summary ? ` — ${last.summary}` : ""}
                        </p>
                      ) : (
                        <p className="text-[11px] text-muted-foreground pl-5">{t(language, "act.installed.noRuns")}</p>
                      )}
                    </div>

                    {result?.review_item_id ? (
                      <p className="text-xs text-destructive font-medium" role="status">{t(language, "act.installed.failedReview")}</p>
                    ) : null}
                  </div>

                  {/* Two-step Action Controls */}
                  <div className="pt-2 border-t border-border/40 space-y-2">
                    <div className="flex items-center gap-2">
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={runNow.isPending}
                        onClick={() => runNow.mutate({ id, dryRun: true })}
                        className="flex-1 text-xs h-8"
                      >
                        <PlayCircle className="h-3.5 w-3.5 mr-1" />
                        {isBusy && runNow.variables?.dryRun
                          ? t(language, "act.installed.dryRunning")
                          : t(language, "act.installed.runNow")}
                      </Button>

                      {unlockedLive ? (
                        <Button
                          size="sm"
                          disabled={runNow.isPending}
                          onClick={() => runNow.mutate({ id, dryRun: false })}
                          className="flex-1 text-xs h-8 shadow-sm"
                        >
                          {isBusy && !runNow.variables?.dryRun
                            ? t(language, "act.installed.liveRunning")
                            : t(language, "act.installed.runLive")}
                        </Button>
                      ) : null}
                    </div>
                    <p className="text-[10px] text-muted-foreground text-center">{t(language, "act.installed.dryRunHint")}</p>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
