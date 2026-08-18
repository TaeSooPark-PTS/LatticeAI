import * as React from "react";
import "@/i18n/workspace";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, GitBranch, ShieldCheck } from "lucide-react";
import type { Edge, Node } from "reactflow";
import { latticeApi } from "@/api/client";
import { LazyPanel } from "@/components/ErrorBoundary";
import { ActionButton, DataPanel, EmptyState, EntityList, OperationResult, StructuredView } from "@/components/primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { AutomationSuggestions } from "@/features/act/AutomationSuggestions";
import { InstalledAutomations } from "@/features/act/InstalledAutomations";
import { firstString } from "@/features/act/actHelpers";
import { useAppStore } from "@/store/appStore";
import { asArray } from "@/lib/utils";
import { t, type Language } from "@/i18n";

const WorkflowGraph = React.lazy(() =>
  import("./WorkflowGraph").then((module) => ({ default: module.WorkflowGraph })),
);

export function WorkflowsPanel() {
  const qc = useQueryClient();
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  const defs = useQuery({ queryKey: ["workflowDefinitions"], queryFn: latticeApi.workflowDefinitions });
  const triggers = useQuery({ queryKey: ["workflowTriggers"], queryFn: latticeApi.workflowTriggers });
  const recipes = useQuery({ queryKey: ["automationRecipes"], queryFn: latticeApi.automationRecipes });
  const [name, setName] = React.useState(() => t(language, "act.workflow.defaultName"));
  const [importText, setImportText] = React.useState("");
  const create = useMutation({
    mutationFn: () => latticeApi.createWorkflow({
      name: name.trim() || t(language, "act.workflow.defaultName"),
      nodes: manualWorkflowNodes(language),
      metadata: { created_from: "desktop-act-ui" },
    }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["workflowDefinitions"] }),
  });
  const importWorkflow = useMutation({
    mutationFn: () => latticeApi.importWorkflow(JSON.parse(importText) as Record<string, unknown>),
    onSuccess: () => {
      setImportText("");
      qc.invalidateQueries({ queryKey: ["workflowDefinitions"] });
    },
  });
  const installRecipe = useMutation({
    mutationFn: ({ recipeId, enabled }: { recipeId: string; enabled: boolean }) => latticeApi.installAutomationRecipe(recipeId, enabled),
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["workflowDefinitions"] }),
        qc.invalidateQueries({ queryKey: ["workflowTriggers"] }),
      ]);
    },
  });
  const workflows = asArray<Record<string, unknown>>((defs.data?.data as Record<string, unknown>)?.workflows);
  const installedRecipes = new Map(
    workflows
      .filter((workflow) => {
        const metadata = (workflow.metadata || {}) as Record<string, unknown>;
        return metadata.created_from === "brain_automation_recipe" && metadata.recipe_id;
      })
      .map((workflow) => {
        /* v8 ignore next -- unreachable: the preceding `.filter` only keeps
           workflows whose `metadata.created_from` already matched, which is
           impossible unless `workflow.metadata` was itself a truthy object.
           Kept as defense-in-depth against the two falling out of sync. */
        const metadata = (workflow.metadata || {}) as Record<string, unknown>;
        return [String(metadata.recipe_id), workflow] as [string, Record<string, unknown>];
      }),
  );
  const nodes: Node[] = workflows.slice(0, 12).map((workflow, index) => ({
    id: String(workflow.id || workflow.workflow_id || index),
    position: { x: (index % 4) * 190, y: Math.floor(index / 4) * 120 },
    data: { label: String(workflow.name || workflow.id || t(language, "act.workflow.fallbackName", { index: index + 1 })) },
  }));
  const edges: Edge[] = nodes.slice(1).map((node, index) => ({ id: `e-${index}`, source: nodes[index].id, target: node.id }));
  return (
    <div className="flex flex-col gap-4">
      <AutomationSuggestions language={language} />
      <InstalledAutomations language={language} />
      <DataPanel title={t(language, "act.automation.title")} result={recipes.data}>
        {(data) => {
          const items = asArray<Record<string, unknown>>((data as Record<string, unknown>).recipes);
          return (
            <div className="flex flex-col sm:flex-row flex-wrap gap-3">
              {items.map((recipe) => {
                const id = String(recipe.id || "");
                const copy = (suffix: string, fallback: string) => {
                  const key = `act.recipe.${id}${suffix}`;
                  const value = t(language, key);
                  return value === key ? fallback : value;
                };
                const cadenceRaw = String(recipe.cadence || "");
                const cadenceKey = `act.cadence.${cadenceRaw}`;
                const cadence = t(language, cadenceKey);
                const consent = (recipe.consent || {}) as Record<string, unknown>;
                const creates = asArray<string>(recipe.creates);
                return (
                  <div key={id} className="rounded-lg border border-border bg-background/70 p-4">
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <div className="flex items-center gap-2 font-medium">
                          <CalendarClock className="h-4 w-4" /> {copy("", String(recipe.name || id))}
                        </div>
                        <p className="mt-2 text-sm text-muted-foreground">{copy(".summary", String(recipe.summary || ""))}</p>
                      </div>
                      <Badge variant="muted">{cadence !== cadenceKey ? cadence : cadenceRaw || t(language, "act.status.draft")}</Badge>
                    </div>
                    <p className="mt-3 text-sm">{copy(".value", String(recipe.user_value || ""))}</p>
                    <div className="mt-3 flex flex-wrap gap-2">
                      <Badge variant="success"><ShieldCheck className="h-3 w-3" /> {t(language, "act.automation.local")}</Badge>
                      {consent.requires_user_enable ? <Badge variant="warning">{t(language, "act.automation.consent")}</Badge> : null}
                      {creates.slice(0, 2).map((item) => {
                        const key = `act.creates.${item}`;
                        const label = t(language, key);
                        return <Badge key={item} variant="muted">{label === key ? item : label}</Badge>;
                      })}
                    </div>
                    {(() => {
                      const installedWorkflow = installedRecipes.get(id);
                      const installedMetadata = (installedWorkflow?.metadata || {}) as Record<string, unknown>;
                      const isEnabled = installedMetadata.automation_state === "enabled";
                      const isInstalling = installRecipe.isPending && installRecipe.variables?.recipeId === id;
                      type InstallResult = { recipe?: { recipe_id?: string }; enabled?: boolean } | undefined;
                      const last = installRecipe.data?.data as InstallResult;
                      const lastRid = last && last.recipe && last.recipe.recipe_id ? String(last.recipe.recipe_id) : "";
                      const justSucceeded = !installRecipe.isPending && lastRid === id;
                      const installed = Boolean(installedWorkflow);
                      const enabled = isEnabled || Boolean(justSucceeded && last?.enabled);
                      const btnLabel = isInstalling
                        ? t(language, installRecipe.variables?.enabled ? "act.automation.enabling" : "act.automation.creating")
                        : enabled
                          ? t(language, "act.automation.active")
                          : installed
                            ? t(language, "act.automation.enable")
                            : justSucceeded
                              ? t(language, "act.automation.created")
                              : t(language, "act.automation.create");
                      return (
                        <>
                          <Button
                            className="mt-4 w-full"
                            variant={enabled || installed ? "secondary" : "outline"}
                            disabled={!id || isInstalling || enabled}
                            onClick={() => {
                              /* v8 ignore next -- unreachable: the only trigger is this
                                 button, which is itself disabled by `isInstalling ||
                                 enabled` (a superset of this guard) in the same render.
                                 Kept as defense-in-depth. */
                              if (enabled || isInstalling) return;
                              installRecipe.mutate({ recipeId: id, enabled: installed });
                            }}
                          >
                            {btnLabel}
                          </Button>
                          {justSucceeded && !enabled ? (
                            <p className="mt-1 text-[10px] text-green-600">{t(language, "act.automation.draftReady")}</p>
                          ) : null}
                          {installed && !enabled && !justSucceeded ? (
                            <p className="mt-1 text-[10px] text-muted-foreground">{t(language, "act.automation.enableHint")}</p>
                          ) : null}
                          {enabled ? (
                            <p className="mt-1 text-[10px] text-green-600">{t(language, "act.automation.activeHint")}</p>
                          ) : null}
                        </>
                      );
                    })()}
                  </div>
                );
              })}
            </div>
          );
        }}
      </DataPanel>
      {mode === "basic" ? (
        <DataPanel
          title={t(language, "act.trigger.title")}
          description={t(language, "act.trigger.detail")}
          result={triggers.data}
          className="xl:col-span-2"
        >
          {(data) => <TriggerSummary data={data as Record<string, unknown>} />}
        </DataPanel>
      ) : (
        <>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><GitBranch className="h-4 w-4" /> {t(language, "act.workflow.graph")}</CardTitle>
          <CardDescription>{t(language, "act.workflow.graphDetail")}</CardDescription>
        </CardHeader>
        <CardContent>
          <LazyPanel language={language} resetKey="workflow-graph">
            <WorkflowGraph nodes={nodes} edges={edges} />
          </LazyPanel>
        </CardContent>
      </Card>
      <DataPanel title={t(language, "act.panel.definitions")} result={defs.data}>
        {() => (
          <div className="space-y-3">
            <div className="grid gap-2 rounded-md border border-border p-3">
              <div className="flex flex-wrap gap-2">
                <Input value={name} onChange={(event) => setName(event.target.value)} placeholder={t(language, "act.workflow.namePlaceholder")} />
                <Button disabled={create.isPending} onClick={() => create.mutate()}>{t(language, "act.workflow.create")}</Button>
              </div>
              <Textarea value={importText} onChange={(event) => setImportText(event.target.value)} placeholder={t(language, "act.workflow.importPlaceholder")} />
              <Button variant="outline" disabled={!importText.trim() || importWorkflow.isPending} onClick={() => importWorkflow.mutate()}>{t(language, "act.workflow.import")}</Button>
              {create.data ? <OperationResult result={create.data} successLabel={t(language, "act.workflow.created")} /> : null}
              {importWorkflow.data ? <OperationResult result={importWorkflow.data} successLabel={t(language, "act.workflow.imported")} /> : null}
            </div>
            {workflows.length ? workflows.map((workflow) => {
              const id = String(workflow.id || workflow.workflow_id);
              return (
                <div key={id} className="rounded-md border border-border p-3">
                  <div className="font-medium">{String(workflow.name || id)}</div>
                  <div className="mt-2 flex gap-2">
                    <ActionButton label={t(language, "act.workflow.run")} action={() => latticeApi.runWorkflow(id)} invalidate={["workflowRuns"]} />
                    <ActionButton label={t(language, "act.workflow.export")} action={() => latticeApi.exportWorkflow(id)} />
                  </div>
                </div>
              );
            }) : <EntityList items={[]} />}
          </div>
        )}
      </DataPanel>
      <DataPanel title={t(language, "act.panel.triggers")} result={triggers.data} className="xl:col-span-2">
        {(data) => <StructuredView value={data} />}
      </DataPanel>
        </>
      )}
    </div>
  );
}

function TriggerSummary({ data }: { data: Record<string, unknown> }) {
  const language = useAppStore((state) => state.language);
  const armed = asArray<Record<string, unknown>>(data.armed);
  const running = data.running !== false;
  if (!armed.length) {
    return <EmptyState title={t(language, "act.trigger.empty")} />;
  }
  return (
    <div className="grid gap-2">
      {armed.map((trigger, index) => {
        const kindKey = `act.trigger.when.${String(trigger.kind || "")}`;
        const when = t(language, kindKey);
        const title = firstString(trigger.name, trigger.label)
          || t(language, "act.workflow.fallbackName", { index: index + 1 });
        return (
          <div
            key={String(trigger.workflow_id || trigger.id || index)}
            className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border p-3 bg-background"
          >
            <div>
              <div className="font-medium">{title}</div>
              <div className="mt-1 text-sm text-muted-foreground">
                {when === kindKey ? t(language, "act.trigger.when.unknown") : when}
              </div>
            </div>
            <Badge variant={running ? "success" : "muted"}>
              {t(language, running ? "act.trigger.running" : "act.trigger.paused")}
            </Badge>
          </div>
        );
      })}
    </div>
  );
}

function manualWorkflowNodes(language: Language): Array<Record<string, unknown>> {
  return [
    {
      id: "trigger",
      type: "trigger",
      name: t(language, "act.workflow.manualStart"),
      config: { trigger: "manual" },
      next: "output",
    },
    {
      id: "output",
      type: "output",
      name: t(language, "act.workflow.output"),
      config: { value: t(language, "act.workflow.completed") },
      next: null,
    },
  ];
}
