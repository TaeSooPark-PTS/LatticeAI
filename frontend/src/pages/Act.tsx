import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import ReactFlow, { Background, Controls, Edge, Node } from "reactflow";
import { Bot, GitBranch, PauseCircle, Play, Workflow } from "lucide-react";
import { latticeApi } from "@/api/client";
import { ActionButton, DataPanel, EntityList, JsonView, Tabs } from "@/components/primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { asArray, shortId } from "@/lib/utils";

type ActTab = "agents" | "runs" | "workflows" | "hooks" | "tools";

const tabs: Array<{ id: ActTab; label: string }> = [
  { id: "agents", label: "Agents" },
  { id: "runs", label: "Runs" },
  { id: "workflows", label: "Workflows" },
  { id: "hooks", label: "Hooks" },
  { id: "tools", label: "Tools" },
];

export function ActPage({ initialTab }: { initialTab?: string }) {
  const [tab, setTab] = React.useState<ActTab>((initialTab as ActTab) || "agents");
  React.useEffect(() => {
    if (tabs.some((item) => item.id === initialTab)) setTab(initialTab as ActTab);
  }, [initialTab]);
  return (
    <div className="space-y-4">
      <header>
        <div className="flex items-center gap-2 text-sm text-primary"><Workflow className="h-4 w-4" /> Durable execution</div>
        <h1 className="mt-2 text-3xl font-semibold">Act</h1>
        <p className="mt-2 max-w-3xl text-sm text-muted-foreground">Agents, workflows, approvals, hooks, and governed tools. Pauses and unavailable states are surfaced honestly.</p>
      </header>
      <Tabs tabs={tabs} value={tab} onChange={(id) => setTab(id as ActTab)} />
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
  return (
    <div className="grid gap-4 xl:grid-cols-[0.9fr_1.1fr]">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Bot className="h-4 w-4" /> Run agent pipeline</CardTitle>
          <CardDescription>POST `/agents/api/run` creates a durable run; mode is determined by backend model availability.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Textarea value={goal} onChange={(e) => setGoal(e.target.value)} placeholder="Describe the objective..." />
          <Button disabled={!goal.trim() || run.isPending} onClick={() => run.mutate()}><Play className="h-4 w-4" /> Run planner/executor/reviewer</Button>
          {run.data ? <JsonView value={run.data.data || run.data.error} /> : null}
        </CardContent>
      </Card>
      <DataPanel title="Runtime status" result={runtime.data}>
        {(data) => <JsonView value={data} />}
      </DataPanel>
      <DataPanel title="Agent registry" result={registry.data}>
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
      <DataPanel title="Agent capabilities" result={caps.data}>
        {(data) => <JsonView value={data} />}
      </DataPanel>
    </div>
  );
}

function RunsPanel() {
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
              {rows.map(([token, value]) => (
                <div key={token} className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border p-3">
                  <div>
                    <div className="font-medium">{shortId(token, 16)}</div>
                    <div className="text-sm text-muted-foreground">{JSON.stringify(value)}</div>
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
  const defs = useQuery({ queryKey: ["workflowDefinitions"], queryFn: latticeApi.workflowDefinitions });
  const triggers = useQuery({ queryKey: ["workflowTriggers"], queryFn: latticeApi.workflowTriggers });
  const workflows = asArray<Record<string, unknown>>((defs.data?.data as Record<string, unknown>)?.workflows);
  const nodes: Node[] = workflows.slice(0, 12).map((workflow, index) => ({
    id: String(workflow.id || workflow.workflow_id || index),
    position: { x: (index % 4) * 190, y: Math.floor(index / 4) * 120 },
    data: { label: String(workflow.name || workflow.id || `Workflow ${index + 1}`) },
  }));
  const edges: Edge[] = nodes.slice(1).map((node, index) => ({ id: `e-${index}`, source: nodes[index].id, target: node.id }));
  return (
    <div className="grid gap-4 xl:grid-cols-[1.2fr_0.8fr]">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><GitBranch className="h-4 w-4" /> Workflow graph</CardTitle>
          <CardDescription>React Flow view of workflow definitions. Running a workflow calls its backend run endpoint.</CardDescription>
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
          <div className="space-y-2">
            {workflows.length ? workflows.map((workflow) => {
              const id = String(workflow.id || workflow.workflow_id);
              return (
                <div key={id} className="rounded-md border border-border p-3">
                  <div className="font-medium">{String(workflow.name || id)}</div>
                  <div className="mt-2 flex gap-2">
                    <ActionButton label="Run" action={() => latticeApi.runWorkflow(id)} invalidate={["workflowRuns"]} />
                  </div>
                </div>
              );
            }) : <EntityList items={[]} />}
          </div>
        )}
      </DataPanel>
      <DataPanel title="Trigger configuration" result={triggers.data} className="xl:col-span-2">
        {(data) => <JsonView value={data} />}
      </DataPanel>
    </div>
  );
}

function HooksPanel() {
  const hooks = useQuery({ queryKey: ["hooks"], queryFn: latticeApi.hooks });
  const runs = useQuery({ queryKey: ["hookRuns"], queryFn: latticeApi.hookRuns });
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
          <CardTitle className="flex items-center gap-2"><PauseCircle className="h-4 w-4" /> Manual hook fire</CardTitle>
          <CardDescription>Uses `/api/hooks/run`; no hook is treated as successful unless the backend records it.</CardDescription>
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
    <DataPanel title="Tool governance" result={tools.data}>
      {(data) => <JsonView value={data} />}
    </DataPanel>
  );
}
