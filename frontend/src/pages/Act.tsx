import * as React from "react";
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
import { useAppStore } from "@/store/appStore";
import { asArray, shortId } from "@/lib/utils";

type ActTab = "agents" | "runs" | "workflows" | "hooks" | "tools";

const tabs: Array<{ id: ActTab; label: string }> = [
  { id: "agents", label: "Goals" },
  { id: "runs", label: "Runs" },
  { id: "workflows", label: "Recipes" },
  { id: "hooks", label: "Safeguards" },
  { id: "tools", label: "Permissions" },
];

export function ActPage({ initialTab }: { initialTab?: string }) {
  const mode = useAppStore((state) => state.mode);
  const [tab, setTab] = React.useState<ActTab>((initialTab as ActTab) || "agents");
  React.useEffect(() => {
    if (tabs.some((item) => item.id === initialTab)) setTab(initialTab as ActTab);
  }, [initialTab]);
  return (
    <div className="space-y-5">
      <header className="page-hero">
        <div className="page-kicker"><Workflow className="h-4 w-4" /> Automate</div>
        <h1 className="page-title">Make work move, with a hand on the door.</h1>
        <p className="page-copy">Give Lattice a goal, review each run, and approve sensitive actions before anything important changes.</p>
      </header>
      <Tabs tabs={tabs.map((item) => mode === "basic" ? item : item.id === "hooks" ? { ...item, label: "Hooks" } : item.id === "tools" ? { ...item, label: "Tools" } : item)} value={tab} onChange={(id) => setTab(id as ActTab)} />
      {tab === "agents" ? <AgentsPanel /> : null}
      {tab === "runs" ? <RunsPanel /> : null}
      {tab === "workflows" ? <WorkflowsPanel /> : null}
      {tab === "hooks" ? <HooksPanel /> : null}
      {tab === "tools" ? <ToolsPanel /> : null}
    </div>
  );
}

function AgentsPanel() {
  const qc = useQueryClient();
  const mode = useAppStore((state) => state.mode);
  const [goal, setGoal] = React.useState("");
  const runtime = useQuery({ queryKey: ["agentRuntime"], queryFn: latticeApi.agentRuntime });
  const registry = useQuery({ queryKey: ["agentRegistry"], queryFn: latticeApi.agentRegistry });
  const caps = useQuery({ queryKey: ["agentCapabilities"], queryFn: latticeApi.agentCapabilities });
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
  const runtimeReason = mode === "basic" ? "Load a local model before running agents." : String(runtimeMeta.unavailable_reason || "Load an LLM-backed model before running agents.");
  const canRunAgent = Boolean(goal.trim()) && runtimeReady && !run.isPending;
  return (
    <div className="grid gap-4 xl:grid-cols-[0.9fr_1.1fr]">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Bot className="h-4 w-4" /> Start with a goal</CardTitle>
          <CardDescription>Lattice will plan, execute, and review only when the local model is ready.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Textarea value={goal} onChange={(e) => setGoal(e.target.value)} placeholder="What should Lattice help you accomplish?" />
          {!runtimeReady ? <Badge variant="warning">{runtimeReason}</Badge> : null}
          <Button
            className="w-full"
            variant={runtimeReady ? "default" : "outline"}
            disabled={!canRunAgent}
            onClick={() => run.mutate()}
          >
            <Play className="h-4 w-4" /> {runtimeReady ? "Start Run" : "Load a model first"}
          </Button>
          {run.data ? <OperationResult result={run.data} successLabel="Agent run request completed" /> : null}
        </CardContent>
      </Card>
      <DataPanel title="Readiness" result={runtime.data}>
        {(data) => mode === "basic" ? (
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="rounded-lg border border-border bg-background/55 p-3">
              <div className="text-sm font-medium">Model</div>
              <Badge variant={runtimeReady ? "success" : "warning"}>{runtimeReady ? "ready" : "needed"}</Badge>
            </div>
            <div className="rounded-lg border border-border bg-background/55 p-3">
              <div className="text-sm font-medium">Planner</div>
              <Badge variant="muted">{runtimeReady ? "available" : "waiting"}</Badge>
            </div>
            <div className="rounded-lg border border-border bg-background/55 p-3">
              <div className="text-sm font-medium">Review</div>
              <Badge variant="success">approval required</Badge>
            </div>
          </div>
        ) : <StructuredView value={data} />}
      </DataPanel>
      <DataPanel title="Agent team" result={registry.data}>
        {(data) => (
          <div className="space-y-3">
            <EntityList items={(data as Record<string, unknown>).agents} titleKey="name" metaKey="type" />
            <div className="flex gap-2">
              <Input value={agentName} onChange={(e) => setAgentName(e.target.value)} placeholder="New custom agent name" />
              <Button disabled={!agentName.trim() || register.isPending} onClick={() => register.mutate()}>Register</Button>
            </div>
          </div>
        )}
      </DataPanel>
      <DataPanel title={mode === "basic" ? "What Lattice can do" : "What agents can do"} result={caps.data}>
        {(data) => <StructuredView value={data} />}
      </DataPanel>
    </div>
  );
}

function RunsPanel() {
  const mode = useAppStore((state) => state.mode);
  const runtime = useQuery({ queryKey: ["agentRuntime"], queryFn: latticeApi.agentRuntime });
  const workflows = useQuery({ queryKey: ["workflowRuns"], queryFn: latticeApi.workflowRuns });
  const pending = useQuery({ queryKey: ["permissions"], queryFn: latticeApi.permissionsPending });
  const agentRuns = asArray<Record<string, unknown>>((runtime.data?.data as Record<string, unknown>)?.runs);
  const workflowRuns = asArray<Record<string, unknown>>((workflows.data?.data as Record<string, unknown>)?.runs);
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <DataPanel title="Agent runs" result={runtime.data}>
        {() => <RunList runs={agentRuns} kind="agent" />}
      </DataPanel>
      <DataPanel title="Workflow runs" result={workflows.data}>
        {() => <RunList runs={workflowRuns} kind="workflow" />}
      </DataPanel>
      <DataPanel title="Approval inbox" result={pending.data} className="xl:col-span-2">
        {(data) => {
          const pendingMap = ((data as Record<string, unknown>).pending || {}) as Record<string, unknown>;
          const rows = Object.entries(pendingMap);
          return rows.length ? (
            <div className="grid gap-2">
              {rows.map(([token, value], index) => (
                <div key={token} className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border p-3">
                  <div>
                    <div className="font-medium">{mode === "basic" ? `Approval request ${index + 1}` : shortId(token, 16)}</div>
                    <div className="mt-2">
                      <KeyValueList data={(value || {}) as Record<string, unknown>} limit={5} />
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <ActionButton label="Approve" action={() => latticeApi.approvePermission(token)} invalidate={["permissions"]} />
                    <ActionButton label="Deny" action={() => latticeApi.denyPermission(token)} invalidate={["permissions"]} variant="destructive" />
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
  if (!runs.length) return <EntityList items={[]} />;
  return (
    <div className="grid gap-2">
      {runs.slice(0, 10).map((run) => {
        const id = String(run.run_id || run.id);
        const status = String(run.status || "unknown");
        return (
          <div key={id} className="rounded-md border border-border bg-background p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="font-medium">{shortId(id, 18)}</div>
              <Badge variant={status === "succeeded" ? "success" : status === "awaiting_approval" ? "warning" : "muted"}>{status}</Badge>
            </div>
            <div className="mt-2 flex flex-wrap gap-2">
              <ActionButton label="Stop" action={() => kind === "agent" ? latticeApi.stopAgentRun(id) : latticeApi.stopWorkflowRun(id)} />
              {status === "awaiting_approval" && kind === "workflow" ? (
                <>
                  <ActionButton label="Resume approved" action={() => latticeApi.resumeWorkflowRun(id, true)} />
                  <ActionButton label="Resume denied" action={() => latticeApi.resumeWorkflowRun(id, false)} variant="destructive" />
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
  const defs = useQuery({ queryKey: ["workflowDefinitions"], queryFn: latticeApi.workflowDefinitions });
  const triggers = useQuery({ queryKey: ["workflowTriggers"], queryFn: latticeApi.workflowTriggers });
  const recipes = useQuery({ queryKey: ["automationRecipes"], queryFn: latticeApi.automationRecipes });
  const [name, setName] = React.useState("Manual workflow");
  const [importText, setImportText] = React.useState("");
  const create = useMutation({
    mutationFn: () => latticeApi.createWorkflow({
      name: name.trim() || "Manual workflow",
      nodes: manualWorkflowNodes(),
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
    mutationFn: (recipeId: string) => latticeApi.installAutomationRecipe(recipeId, false),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workflowDefinitions"] });
      qc.invalidateQueries({ queryKey: ["workflowTriggers"] });
    },
  });
  const workflows = asArray<Record<string, unknown>>((defs.data?.data as Record<string, unknown>)?.workflows);
  const nodes: Node[] = workflows.slice(0, 12).map((workflow, index) => ({
    id: String(workflow.id || workflow.workflow_id || index),
    position: { x: (index % 4) * 190, y: Math.floor(index / 4) * 120 },
    data: { label: String(workflow.name || workflow.id || `Workflow ${index + 1}`) },
  }));
  const edges: Edge[] = nodes.slice(1).map((node, index) => ({ id: `e-${index}`, source: nodes[index].id, target: node.id }));
  return (
    <div className="grid gap-4 xl:grid-cols-[1.2fr_0.8fr]">
      <DataPanel title="Brain automations" result={recipes.data} className="xl:col-span-2">
        {(data) => {
          const items = asArray<Record<string, unknown>>((data as Record<string, unknown>).recipes);
          return (
            <div className="grid gap-3 lg:grid-cols-3">
              {items.map((recipe) => {
                const id = String(recipe.id || "");
                const consent = (recipe.consent || {}) as Record<string, unknown>;
                const creates = asArray<string>(recipe.creates);
                return (
                  <div key={id} className="rounded-lg border border-border bg-background/70 p-4">
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <div className="flex items-center gap-2 font-medium">
                          <CalendarClock className="h-4 w-4" /> {String(recipe.name || id)}
                        </div>
                        <p className="mt-2 text-sm text-muted-foreground">{String(recipe.summary || "")}</p>
                      </div>
                      <Badge variant="muted">{String(recipe.cadence || "draft")}</Badge>
                    </div>
                    <p className="mt-3 text-sm">{String(recipe.user_value || "")}</p>
                    <div className="mt-3 flex flex-wrap gap-2">
                      <Badge variant="success"><ShieldCheck className="h-3 w-3" /> local only</Badge>
                      {consent.requires_user_enable ? <Badge variant="warning">draft first</Badge> : null}
                      {creates.slice(0, 2).map((item) => <Badge key={item} variant="muted">{item}</Badge>)}
                    </div>
                    <Button
                      className="mt-4 w-full"
                      variant="outline"
                      disabled={!id || installRecipe.isPending}
                      onClick={() => installRecipe.mutate(id)}
                    >
                      Create reviewable draft
                    </Button>
                  </div>
                );
              })}
            </div>
          );
        }}
      </DataPanel>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><GitBranch className="h-4 w-4" /> Workflow graph</CardTitle>
          <CardDescription>See your saved workflows as a simple map.</CardDescription>
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
      <DataPanel title="Definitions" result={defs.data}>
        {() => (
          <div className="space-y-3">
            <div className="grid gap-2 rounded-md border border-border p-3">
              <div className="flex flex-wrap gap-2">
                <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="Workflow name" />
                <Button disabled={create.isPending} onClick={() => create.mutate()}>Create</Button>
              </div>
              <Textarea value={importText} onChange={(event) => setImportText(event.target.value)} placeholder="Paste a workflow export" />
              <Button variant="outline" disabled={!importText.trim() || importWorkflow.isPending} onClick={() => importWorkflow.mutate()}>Import</Button>
              {create.data ? <OperationResult result={create.data} successLabel="Workflow created" /> : null}
              {importWorkflow.data ? <OperationResult result={importWorkflow.data} successLabel="Workflow imported" /> : null}
            </div>
            {workflows.length ? workflows.map((workflow) => {
              const id = String(workflow.id || workflow.workflow_id);
              return (
                <div key={id} className="rounded-md border border-border p-3">
                  <div className="font-medium">{String(workflow.name || id)}</div>
                  <div className="mt-2 flex gap-2">
                    <ActionButton label="Run" action={() => latticeApi.runWorkflow(id)} invalidate={["workflowRuns"]} />
                    <ActionButton label="Export" action={() => latticeApi.exportWorkflow(id)} />
                  </div>
                </div>
              );
            }) : <EntityList items={[]} />}
          </div>
        )}
      </DataPanel>
      <DataPanel title="Automation triggers" result={triggers.data} className="xl:col-span-2">
        {(data) => <StructuredView value={data} />}
      </DataPanel>
    </div>
  );
}

function manualWorkflowNodes(): Array<Record<string, unknown>> {
  return [
    {
      id: "trigger",
      type: "trigger",
      name: "Manual start",
      config: { trigger: "manual" },
      next: "output",
    },
    {
      id: "output",
      type: "output",
      name: "Output",
      config: { value: "Workflow completed" },
      next: null,
    },
  ];
}

function HooksPanel() {
  const mode = useAppStore((state) => state.mode);
  const hooks = useQuery({ queryKey: ["hooks"], queryFn: latticeApi.hooks });
  const runs = useQuery({ queryKey: ["hookRuns"], queryFn: latticeApi.hookRuns });
  if (mode === "basic") {
    return (
      <div className="grid gap-4 xl:grid-cols-2">
        <DataPanel title="Safeguards" result={hooks.data}>
          {(data) => <EntityList items={(data as Record<string, unknown>).hooks} titleKey="name" metaKey="kind" />}
        </DataPanel>
        <ModeGate title="Detailed hook logs" detail="Switch to Advanced when you need hook run logs and manual diagnostic controls." />
      </div>
    );
  }
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <DataPanel title="Hooks" result={hooks.data}>
        {(data) => <EntityList items={(data as Record<string, unknown>).hooks} titleKey="name" metaKey="kind" />}
      </DataPanel>
      <DataPanel title="Hook run log" result={runs.data}>
        {(data) => <EntityList items={(data as Record<string, unknown>).runs} titleKey="hook_id" metaKey="status" />}
      </DataPanel>
      <Card className="xl:col-span-2">
        <CardHeader>
        <CardTitle className="flex items-center gap-2"><PauseCircle className="h-4 w-4" /> Run manual hooks</CardTitle>
          <CardDescription>Trigger hooks deliberately and review the recorded result.</CardDescription>
        </CardHeader>
        <CardContent>
          <ActionButton label="Run all manual hooks" action={() => latticeApi.hookRun({ event: "manual" })} invalidate={["hookRuns"]} />
        </CardContent>
      </Card>
    </div>
  );
}

function ToolsPanel() {
  const tools = useQuery({ queryKey: ["toolPermissions"], queryFn: latticeApi.toolPermissions });
  return (
    <DataPanel title="Action permissions" result={tools.data}>
      {(data) => <EntityList items={(data as Record<string, unknown>).permissions || data} titleKey="tool" metaKey="risk" />}
    </DataPanel>
  );
}
