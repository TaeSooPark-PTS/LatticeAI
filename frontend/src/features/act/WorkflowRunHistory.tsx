import * as React from "react";
import "@/i18n/workspace";
import { useQuery } from "@tanstack/react-query";
import { History } from "lucide-react";

import { latticeApi } from "@/api/client";
import type { WorkflowReplayFrame } from "@/api/actReview";
import { EmptyState } from "@/components/primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { runStatusLabel } from "@/features/act/actHelpers";
import { shortWhen } from "@/features/act/InstalledAutomations";
import { t, type Language } from "@/i18n";

function Note({ text, alert }: { text: string; alert?: boolean }) {
  return <p role={alert ? "alert" : "status"} className={`text-sm ${alert ? "text-destructive" : "text-muted-foreground"}`}>{text}</p>;
}

export function WorkflowRunHistory({
  language,
  workflows,
}: {
  language: Language;
  workflows: Array<Record<string, unknown>>;
}) {
  const [picked, setPicked] = React.useState("");
  const [runId, setRunId] = React.useState("");
  const options = workflows.map((workflow) => {
    const id = String(workflow.id || workflow.workflow_id);
    return { id, name: String(workflow.name || id) };
  });
  const workflowId = options.some((option) => option.id === picked) ? picked : options[0]?.id || "";
  const runs = useQuery({
    queryKey: ["workflowDefinitionRuns", workflowId],
    queryFn: () => latticeApi.workflowDefinitionRuns(workflowId),
    enabled: Boolean(workflowId),
  });
  const rows = runs.data?.data.runs || [];
  const selected = rows.some((run) => run.id === runId) ? runId : "";

  return (
    <Card data-testid="workflow-run-history">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <History className="h-4 w-4" aria-hidden="true" /> {t(language, "act.history.title")}
        </CardTitle>
        <CardDescription>{t(language, "act.history.detail")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {!options.length ? <EmptyState title={t(language, "act.history.noWorkflows")} /> : (
          <>
            <label className="grid max-w-sm gap-1 text-xs text-muted-foreground">
              {t(language, "act.history.workflow")}
              <select
                className="h-9 rounded-md border border-input bg-background px-2 text-sm text-foreground"
                value={workflowId}
                onChange={(event) => {
                  setPicked(event.target.value);
                  setRunId("");
                }}
              >
                {options.map((option) => <option key={option.id} value={option.id}>{option.name}</option>)}
              </select>
            </label>
            {runs.isLoading ? <Note text={t(language, "ui.loading")} /> : null}
            {runs.data && !runs.data.ok ? <Note alert text={runs.data.error || t(language, "act.history.loadError")} /> : null}
            {runs.data?.ok && !rows.length ? <Note text={t(language, "act.history.empty")} /> : null}
            {rows.length ? (
              <ul className="m-0 max-h-64 list-none divide-y divide-border overflow-y-auto rounded-md border border-border p-0">
                {rows.map((run) => (
                  <li key={run.id}>
                    <button
                      type="button"
                      aria-pressed={selected === run.id}
                      className={`flex w-full flex-wrap items-center justify-between gap-2 p-2.5 text-left text-sm hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring ${selected === run.id ? "bg-muted" : ""}`}
                      onClick={() => setRunId(String(run.id))}
                    >
                      <span className="tabular-nums">{shortWhen(run.created_at)}</span>
                      <Badge variant={run.status === "ok" ? "success" : run.status === "awaiting_approval" ? "warning" : "muted"}>
                        {runStatusLabel(String(run.status || ""), language)}
                      </Badge>
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}
            {selected ? <RunReplay key={selected} language={language} runId={selected} /> : null}
          </>
        )}
      </CardContent>
    </Card>
  );
}

function eventLabel(language: Language, event: string) {
  const key = `act.history.event.${event}`;
  const label = t(language, key);
  return label === key ? event : label;
}

function frameOutput(frame: WorkflowReplayFrame) {
  const parts = [frame.why, frame.raw?.detail].filter((part): part is string => typeof part === "string" && part !== "");
  if (frame.output !== null && frame.output !== undefined) {
    parts.push(typeof frame.output === "string" ? frame.output : JSON.stringify(frame.output, null, 2));
  }
  return parts.join("\n");
}

function RunReplay({ language, runId }: { language: Language; runId: string }) {
  const [step, setStep] = React.useState(0);
  const replay = useQuery({ queryKey: ["workflowReplay", runId], queryFn: () => latticeApi.workflowRunReplay(runId) });
  if (!replay.data) return <Note text={t(language, "ui.loading")} />;
  if (!replay.data.ok) return <Note alert text={replay.data.error || t(language, "act.history.loadError")} />;
  const frames = replay.data.data.replay.frames || [];
  const frame = frames[Math.min(step, frames.length - 1)];
  if (!frame) return <Note text={t(language, "act.history.noSteps")} />;
  const current = frames.indexOf(frame);
  const output = frameOutput(frame);
  return (
    <section aria-label={t(language, "act.history.replay")} className="space-y-2 border-t border-border pt-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span role="status" className="text-sm font-medium tabular-nums">
          {t(language, "act.history.position", { current: current + 1, total: frames.length })}
        </span>
        <div className="flex gap-2">
          <Button size="sm" variant="outline" disabled={current === 0} onClick={() => setStep(current - 1)}>{t(language, "act.history.previous")}</Button>
          <Button size="sm" variant="outline" disabled={current === frames.length - 1} onClick={() => setStep(current + 1)}>{t(language, "act.history.next")}</Button>
        </div>
      </div>
      <ol className="m-0 list-none space-y-1 p-0">
        {frames.map((item, index) => (
          <li key={item.index}>
            <button
              type="button"
              aria-current={index === current ? "step" : undefined}
              className={`flex w-full flex-wrap items-baseline gap-2 rounded-md border px-2.5 py-1.5 text-left text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${index === current ? "border-border bg-muted" : "border-transparent hover:bg-muted"}`}
              onClick={() => setStep(index)}
            >
              <span className="tabular-nums text-muted-foreground">{index + 1}</span>
              <span className="font-medium">{eventLabel(language, item.event)}</span>
              {item.actor !== "workflow" ? <code className="text-xs">{item.actor}</code> : null}
              {typeof item.decision === "string" ? (
                <span className="ml-auto text-xs text-muted-foreground">{runStatusLabel(item.decision, language)}</span>
              ) : null}
            </button>
          </li>
        ))}
      </ol>
      <p className="text-xs tabular-nums text-muted-foreground">{shortWhen(frame.when || "")}</p>
      {output ? (
        <pre className="m-0 max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-md border border-border p-2.5 text-xs">{output}</pre>
      ) : (
        <Note text={t(language, "act.history.noOutput")} />
      )}
    </section>
  );
}
