import * as React from "react";
// Route-scoped copy: importing the namespace registers it into the shared
// table and keeps it inside this lazy chunk instead of the entry bundle.
import "@/i18n/workspace";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, PauseCircle, Play, ShieldCheck, Workflow } from "lucide-react";
import { latticeApi } from "@/api/client";
import { LazyPanel } from "@/components/ErrorBoundary";
import { ActionButton, DataPanel, EntityList, ModeGate, OperationResult, StructuredView, Tabs } from "@/components/primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { ReviewInbox } from "@/features/review/ReviewInbox";
import { useAppStore } from "@/store/appStore";
import { t } from "@/i18n";
import { navigateHash } from "@/features/brain/navigation";

type ActTab = "runs" | "agents" | "workflows" | "hooks" | "tools";
type RunsSubTab = "review" | "runs";

const runsSubTabs: Array<{ id: RunsSubTab; labelKey: string }> = [
  { id: "review", labelKey: "act.tab.review" },
  { id: "runs", labelKey: "act.tab.runsHistory" },
];

// The hero now names whichever panel is open, so every tab needs its own pair.
// A Record rather than a chain of ternaries: the chain's final `else` quietly
// captioned the permissions panel "가드레일 및 안전 규칙", and adding a tab would
// have kept inheriting that. This way the type checker asks for the copy.
const heroCopy: Record<ActTab, { titleKey: string; copyKey: string }> = {
  runs: { titleKey: "act.title.runs", copyKey: "act.copy.runs" },
  agents: { titleKey: "act.title.goals", copyKey: "act.copy.goals" },
  workflows: { titleKey: "act.title.workflows", copyKey: "act.copy.workflows" },
  hooks: { titleKey: "act.title.safeguards", copyKey: "act.copy.safeguards" },
  tools: { titleKey: "act.title.permissions", copyKey: "act.copy.permissions" },
};

const tabs: Array<{ id: ActTab; labelKey: string; advancedLabelKey?: string }> = [
  { id: "runs", labelKey: "act.tab.runs" },
  { id: "agents", labelKey: "act.tab.goals" },
  { id: "workflows", labelKey: "act.tab.recipes" },
  { id: "hooks", labelKey: "act.tab.safeguards", advancedLabelKey: "act.tab.hooks" },
  { id: "tools", labelKey: "act.tab.permissions", advancedLabelKey: "act.tab.tools" },
];

const RunsListPanel = React.lazy(() =>
  import("@/features/act/RunsListPanel").then((module) => ({ default: module.RunsListPanel })),
);
const WorkflowsPanel = React.lazy(() =>
  import("@/features/act/WorkflowsPanel").then((module) => ({ default: module.WorkflowsPanel })),
);

export function ActPage({ initialTab }: { initialTab?: string }) {
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  const [tab, setTab] = React.useState<ActTab>(() => {
    if (initialTab === "agents" || initialTab === "workflows" || initialTab === "hooks" || initialTab === "tools") return initialTab;
    return "runs";
  });
  const [runsSubTab, setRunsSubTab] = React.useState<RunsSubTab>(initialTab === "runs" ? "runs" : "review");
  React.useEffect(() => {
    if (initialTab === "review") {
      setTab("runs");
      setRunsSubTab("review");
      return;
    }
    if (initialTab === "runs") {
      setTab("runs");
      setRunsSubTab("runs");
      return;
    }
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
  const { titleKey, copyKey } = heroCopy[tab];
  return (
    <div className="product-page act-page space-y-5">
      <header className="page-hero">
        <div className="page-kicker"><Workflow className="h-4 w-4" /> {t(language, "act.kicker")}</div>
        <h1 className="page-title">{t(language, titleKey)}</h1>
        <p className="page-copy">{t(language, copyKey)}</p>
      </header>
      <Tabs
        tabs={(mode === "basic" ? tabs.filter((item) => item.id === "runs" || item.id === "agents" || item.id === "workflows") : tabs).map((item) => ({
          id: item.id,
          label: t(language, mode === "basic" || !item.advancedLabelKey ? item.labelKey : item.advancedLabelKey),
        }))}
        value={tab}
        onChange={(id) => selectTab(id as ActTab)}
      />
      {tab === "runs" ? <RunsPanel subTab={runsSubTab} onSubTabChange={selectRunsSubTab} /> : null}
      {tab === "agents" ? <AgentsPanel /> : null}
      {tab === "workflows" ? (
        <LazyPanel language={language} resetKey="act-workflows">
          <WorkflowsPanel />
        </LazyPanel>
      ) : null}
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
    <div className="work-start-flow flex flex-col gap-4">
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
      {subTab === "runs" ? (
        <LazyPanel language={language} resetKey="act-runs">
          <RunsListPanel />
        </LazyPanel>
      ) : <ReviewInbox />}
    </div>
  );
}

function HooksPanel() {
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  const hooks = useQuery({ queryKey: ["hooks"], queryFn: latticeApi.hooks });
  const runs = useQuery({ queryKey: ["hookRuns"], queryFn: latticeApi.hookRuns });
  if (mode === "basic") {
    return (
      <div className="flex flex-col gap-4">
        <DataPanel title={t(language, "act.panel.safeguards")} result={hooks.data}>
          {(data) => <EntityList items={(data as Record<string, unknown>).hooks} titleKey="name" metaKey="kind" />}
        </DataPanel>
        <ModeGate title={t(language, "act.hooks.detailed")} detail={t(language, "act.hooks.detailedHint")} />
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-4">
      <DataPanel title={t(language, "act.panel.hooks")} result={hooks.data}>
        {(data) => <EntityList items={(data as Record<string, unknown>).hooks} titleKey="name" metaKey="kind" />}
      </DataPanel>
      <DataPanel title={t(language, "act.panel.hookRuns")} result={runs.data}>
        {(data) => <EntityList items={(data as Record<string, unknown>).runs} titleKey="hook_id" metaKey="status" />}
      </DataPanel>
      <Card>
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
