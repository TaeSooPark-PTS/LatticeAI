import * as React from "react";
// Route-scoped copy: importing the namespace registers it into the shared
// table and keeps it inside this lazy chunk instead of the entry bundle.
import "@/i18n/workspace";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import ReactFlow, { Background, Controls, Edge, Node } from "reactflow";
import { Bot, CalendarClock, GitBranch, PauseCircle, Play, ShieldCheck, Workflow } from "lucide-react";
import { latticeApi } from "@/api/client";
import { ActionButton, DataPanel, EntityList, KeyValueList, ModeGate, OperationResult, StructuredView, Tabs } from "@/components/primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { AutomationSuggestions } from "@/features/act/AutomationSuggestions";
import { InstalledAutomations } from "@/features/act/InstalledAutomations";
import { ReviewInbox } from "@/features/review/ReviewInbox";
import { useAppStore } from "@/store/appStore";
import { asArray, shortId } from "@/lib/utils";
import { t, type Language } from "@/i18n";
import { navigateHash } from "@/features/brain/navigation";

type ActTab = "agents" | "runs" | "workflows" | "hooks" | "tools";
type RunsSubTab = "runs" | "review";

const runsSubTabs: Array<{ id: RunsSubTab; labelKey: string }> = [
  { id: "runs", labelKey: "act.tab.runs" },
  { id: "review", labelKey: "act.tab.review" },
];

const tabs: Array<{ id: ActTab; labelKey: string; advancedLabelKey?: string }> = [
  { id: "agents", labelKey: "act.tab.goals" },
  { id: "runs", labelKey: "act.tab.runs" },
  { id: "workflows", labelKey: "act.tab.recipes" },
  { id: "hooks", labelKey: "act.tab.safeguards", advancedLabelKey: "act.tab.hooks" },
  { id: "tools", labelKey: "act.tab.permissions", advancedLabelKey: "act.tab.tools" },
];

export function ActPage({ initialTab }: { initialTab?: string }) {
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  const [tab, setTab] = React.useState<ActTab>(() => {
    if (initialTab === "review") return "runs";
    return (initialTab as ActTab) || "agents";
  });
  const [runsSubTab, setRunsSubTab] = React.useState<RunsSubTab>(initialTab === "review" ? "review" : "runs");
  React.useEffect(() => {
    if (initialTab === "review") {
      setTab("runs");
      setRunsSubTab("review");
      return;
    }
    if (initialTab === "runs") setRunsSubTab("runs");
    if (tabs.some((item) => item.id === initialTab)) setTab(initialTab as ActTab);
  }, [initialTab]);
  const selectTab = (next: ActTab) => {
    setTab(next);
    navigateHash("/" + next);
  };
  const selectRunsSubTab = (next: RunsSubTab) => {
    setRunsSubTab(next);
    navigateHash(next === "review" ? "/review" : "/runs");
  };
  return (
    <div className="product-page act-page space-y-5">
      <header className="page-hero">
        <div className="page-kicker"><Workflow className="h-4 w-4" /> {t(language, "act.kicker")}</div>
        <h1 className="page-title">{t(language, "act.title")}</h1>
        <p className="page-copy">{t(language, "act.copy")}</p>
      </header>
      <Tabs
        tabs={(mode === "basic" ? tabs.filter((item) => item.id === "agents" || item.id === "runs" || item.id === "workflows") : tabs).map((item) => ({
          id: item.id,
          label: t(language, mode === "basic" || !item.advancedLabelKey ? item.labelKey : item.advancedLabelKey),
        }))}
        value={tab}
        onChange={(id) => selectTab(id as ActTab)}
      />
      {tab === "agents" ? <AgentsPanel /> : null}
      {tab === "runs" ? <RunsPanel subTab={runsSubTab} onSubTabChange={selectRunsSubTab} /> : null}
      {tab === "workflows" ? <WorkflowsPanel /> : null}
      {tab === "hooks" ? <HooksPanel /> : null}
      {tab === "tools" ? <ToolsPanel /> : null}
    </div>
  );
}

function AgentsPanel() {
  const qc = useQueryClient();
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  const [goal, setGoal] = React.useState("");
  const runtime = useQuery({ queryKey: ["agentRuntime"], queryFn: latticeApi.agentRuntime });
  const registry = useQuery({ queryKey: ["agentRegistry"], queryFn: latticeApi.agentRegistry, enabled: mode !== "basic" });
  const caps = useQuery({ queryKey: ["agentCapabilities"], queryFn: latticeApi.agentCapabilities, enabled: mode !== "basic" });
  const run = useMutation({
    mutationFn: () => latticeApi.runAgent(goal, ["planner", "executor", "reviewer"]),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["agentRuntime"] }),
  });
  const [agentName, setAgentName] = React.useState("");
  const register = useMutation({
    mutationFn: () => latticeApi.registerAgent({ name: agentName, type: "custom", capabilities: [] }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["agentRegistry"] }),
  });
  const runtimeData = (runtime.data?.data || {}) as Record<string, unknown>;
  const runtimeMeta = (runtimeData.runtime || {}) as Record<string, unknown>;
  const runtimeReady = Boolean(runtimeMeta.ready);
  const runtimeReason = mode === "basic"
    ? t(language, "act.goal.modelRequired")
    : String(runtimeMeta.unavailable_reason || t(language, "act.runtime.modelUnavailable"));
  const canRunAgent = Boolean(goal.trim()) && runtimeReady && !run.isPending;
  return (
    <div className="work-start-flow grid gap-4 xl:grid-cols-[0.9fr_1.1fr]">
      <Card className="work-goal-card">
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Bot className="h-4 w-4" /> {t(language, "act.goal.title")}</CardTitle>
          <CardDescription>{t(language, "act.goal.description")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Textarea value={goal} onChange={(e) => setGoal(e.target.value)} placeholder={t(language, "act.goal.placeholder")} />
          {!runtimeReady ? <Badge variant="warning">{runtimeReason}</Badge> : null}
          <Button
            className="w-full"
            variant={runtimeReady ? "default" : "outline"}
            disabled={!canRunAgent}
            onClick={() => run.mutate()}
          >
            <Play className="h-4 w-4" /> {runtimeReady ? t(language, "act.goal.start") : t(language, "act.goal.needsModel")}
          </Button>
          {run.data ? <OperationResult result={run.data} successLabel={t(language, "act.goal.completed")} /> : null}
          {run.data && (run.data as any)?.data ? (
            <div className="text-xs text-emerald-600">{t(language, "act.goal.brainSaved")}</div>
          ) : null}
        </CardContent>
      </Card>
      {mode === "basic" ? (
        <div className="work-safety-note">
          <ShieldCheck className="h-4 w-4" aria-hidden="true" />
          <span>{t(language, "act.goal.safety")}</span>
        </div>
      ) : (
        <>
          <DataPanel title={t(language, "act.panel.readiness")} result={runtime.data}>
            {(data) => <StructuredView value={data} />}
          </DataPanel>
          <DataPanel title={t(language, "act.panel.agentTeam")} result={registry.data}>
            {(data) => (
              <div className="space-y-3">
                <EntityList items={(data as Record<string, unknown>).agents} titleKey="name" metaKey="type" labelPrefix="act.agentRole" />
                <div className="flex gap-2">
                  <Input value={agentName} onChange={(e) => setAgentName(e.target.value)} placeholder={t(language, "act.agent.namePlaceholder")} />
                  <Button disabled={!agentName.trim() || register.isPending} onClick={() => register.mutate()}>{t(language, "act.agent.register")}</Button>
                </div>
              </div>
            )}
          </DataPanel>
          <DataPanel title={t(language, "act.panel.capabilities")} result={caps.data}>
            {(data) => <StructuredView value={data} />}
          </DataPanel>
        </>
      )}
    </div>
  );
}

function RunsPanel({ subTab, onSubTabChange }: { subTab: RunsSubTab; onSubTabChange: (tab: RunsSubTab) => void }) {
  const language = useAppStore((state) => state.language);
  return (
    <div className="space-y-4">
      <Tabs
        tabs={runsSubTabs.map((item) => ({ id: item.id, label: t(language, item.labelKey) }))}
        value={subTab}
        onChange={(id) => onSubTabChange(id as RunsSubTab)}
      />
      {subTab === "runs" ? <RunsListPanel /> : <ReviewInbox />}
    </div>
  );
}

function RunsListPanel() {
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  const runtime = useQuery({ queryKey: ["agentRuntime"], queryFn: latticeApi.agentRuntime });
  const workflows = useQuery({ queryKey: ["workflowRuns"], queryFn: latticeApi.workflowRuns });
  const pending = useQuery({ queryKey: ["permissions"], queryFn: latticeApi.permissionsPending });
  const agentRuns = asArray<Record<string, unknown>>((runtime.data?.data as Record<string, unknown>)?.runs);
  const workflowRuns = asArray<Record<string, unknown>>((workflows.data?.data as Record<string, unknown>)?.runs);
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <DataPanel title={t(language, "act.panel.agentRuns")} result={runtime.data}>
        {() => <RunList runs={agentRuns} kind="agent" />}
      </DataPanel>
      <DataPanel title={t(language, "act.panel.workflowRuns")} result={workflows.data}>
        {() => <RunList runs={workflowRuns} kind="workflow" />}
      </DataPanel>
      <DataPanel title={t(language, "act.panel.approvalInbox")} result={pending.data} className="xl:col-span-2">
        {(data) => {
          const pendingMap = ((data as Record<string, unknown>).pending || {}) as Record<string, unknown>;
          const rows = Object.entries(pendingMap);
          return rows.length ? (
            <div className="grid gap-2">
              {rows.map(([token, value], index) => (
                <div key={token} className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border p-3">
                  <div>
                    <div className="font-medium">{mode === "basic" ? t(language, "act.approval.request", { index: index + 1 }) : shortId(token, 16)}</div>
                    <div className="mt-2">
                      <KeyValueList data={(value || {}) as Record<string, unknown>} limit={5} />
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
    </div>
  );
}

function RunList({ runs, kind }: { runs: Array<Record<string, unknown>>; kind: "agent" | "workflow" }) {
  const language = useAppStore((state) => state.language);
  if (!runs.length) return <EntityList items={[]} />;
  return (
    <div className="grid gap-2">
      {runs.slice(0, 10).map((run) => {
        const id = String(run.run_id || run.id);
        const status = String(run.status || t(language, "act.status.unknown"));
        return (
          <div key={id} className="rounded-md border border-border bg-background p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="font-medium">{shortId(id, 18)}</div>
              <Badge variant={status === "succeeded" ? "success" : status === "awaiting_approval" ? "warning" : "muted"}>{status}</Badge>
            </div>
            <div className="mt-2 flex flex-wrap gap-2">
              <ActionButton label={t(language, "act.action.stop")} action={() => kind === "agent" ? latticeApi.stopAgentRun(id) : latticeApi.stopWorkflowRun(id)} />
              {status === "awaiting_approval" && kind === "workflow" ? (
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

function WorkflowsPanel() {
  const qc = useQueryClient();
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
      .map((workflow) => [String(((workflow.metadata || {}) as Record<string, unknown>).recipe_id), workflow]),
  );
  const nodes: Node[] = workflows.slice(0, 12).map((workflow, index) => ({
    id: String(workflow.id || workflow.workflow_id || index),
    position: { x: (index % 4) * 190, y: Math.floor(index / 4) * 120 },
    data: { label: String(workflow.name || workflow.id || t(language, "act.workflow.fallbackName", { index: index + 1 })) },
  }));
  const edges: Edge[] = nodes.slice(1).map((node, index) => ({ id: `e-${index}`, source: nodes[index].id, target: node.id }));
  return (
    <div className="grid gap-4 xl:grid-cols-[1.2fr_0.8fr]">
      <AutomationSuggestions language={language} />
      <InstalledAutomations language={language} />
      <DataPanel title={t(language, "act.automation.title")} result={recipes.data} className="xl:col-span-2">
        {(data) => {
          const items = asArray<Record<string, unknown>>((data as Record<string, unknown>).recipes);
          return (
            <div className="grid gap-3 lg:grid-cols-3">
              {items.map((recipe) => {
                const id = String(recipe.id || "");
                // Recipe copy ships from the backend in English; the id is the
                // contract, so translate by id and keep the server text for
                // recipes we do not know yet.
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
                      const last = installRecipe.data as InstallResult;
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
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><GitBranch className="h-4 w-4" /> {t(language, "act.workflow.graph")}</CardTitle>
          <CardDescription>{t(language, "act.workflow.graphDetail")}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="h-[440px] rounded-lg border border-border">
            <ReactFlow nodes={nodes} edges={edges} fitView>
              <Background />
              <Controls />
            </ReactFlow>
          </div>
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

function HooksPanel() {
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  const hooks = useQuery({ queryKey: ["hooks"], queryFn: latticeApi.hooks });
  const runs = useQuery({ queryKey: ["hookRuns"], queryFn: latticeApi.hookRuns });
  if (mode === "basic") {
    return (
      <div className="grid gap-4 xl:grid-cols-2">
        <DataPanel title={t(language, "act.panel.safeguards")} result={hooks.data}>
          {(data) => <EntityList items={(data as Record<string, unknown>).hooks} titleKey="name" metaKey="kind" />}
        </DataPanel>
        <ModeGate title={t(language, "act.hooks.detailed")} detail={t(language, "act.hooks.detailedHint")} />
      </div>
    );
  }
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <DataPanel title={t(language, "act.panel.hooks")} result={hooks.data}>
        {(data) => <EntityList items={(data as Record<string, unknown>).hooks} titleKey="name" metaKey="kind" />}
      </DataPanel>
      <DataPanel title={t(language, "act.panel.hookRuns")} result={runs.data}>
        {(data) => <EntityList items={(data as Record<string, unknown>).runs} titleKey="hook_id" metaKey="status" />}
      </DataPanel>
      <Card className="xl:col-span-2">
        <CardHeader>
        <CardTitle className="flex items-center gap-2"><PauseCircle className="h-4 w-4" /> {t(language, "act.hooks.runManual")}</CardTitle>
          <CardDescription>{t(language, "act.hooks.runManualHint")}</CardDescription>
        </CardHeader>
        <CardContent>
          <ActionButton label={t(language, "act.hooks.runAll")} action={() => latticeApi.hookRun({ event: "manual" })} invalidate={["hookRuns"]} />
        </CardContent>
      </Card>
    </div>
  );
}

function ToolsPanel() {
  const language = useAppStore((state) => state.language);
  const tools = useQuery({ queryKey: ["toolPermissions"], queryFn: latticeApi.toolPermissions });
  return (
    <DataPanel title={t(language, "act.panel.permissions")} result={tools.data}>
      {(data) => <EntityList items={(data as Record<string, unknown>).permissions || data} titleKey="tool" metaKey="risk" />}
    </DataPanel>
  );
}
