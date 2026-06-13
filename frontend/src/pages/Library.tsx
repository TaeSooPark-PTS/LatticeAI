import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Boxes, CheckCircle2, Cpu, Download, PackagePlus, PlayCircle, Plug, ShieldAlert } from "lucide-react";
import { latticeApi } from "@/api/client";
import { ActionButton, DataPanel, EmptyState, EntityList, OperationResult, StatGrid, StructuredView, Tabs, ValuePreview } from "@/components/primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useAppStore } from "@/store/appStore";
import { asArray } from "@/lib/utils";

type LibraryTab = "models" | "skills" | "mcp" | "marketplace";

const tabs: Array<{ id: LibraryTab; label: string }> = [
  { id: "models", label: "Models" },
  { id: "skills", label: "Skills" },
  { id: "mcp", label: "MCP" },
  { id: "marketplace", label: "Marketplace" },
];

export function LibraryPage({ initialTab }: { initialTab?: string }) {
  const [tab, setTab] = React.useState<LibraryTab>((initialTab as LibraryTab) || "models");
  React.useEffect(() => {
    if (tabs.some((item) => item.id === initialTab)) setTab(initialTab as LibraryTab);
  }, [initialTab]);
  return (
    <div className="space-y-4">
      <header>
        <div className="flex items-center gap-2 text-sm text-primary"><Boxes className="h-4 w-4" /> Replaceable runtime assets</div>
        <h1 className="mt-2 text-3xl font-semibold">Library</h1>
        <p className="mt-2 max-w-3xl text-sm text-muted-foreground">Models, skills, MCP servers, plugins, and templates are managed by local backend registries.</p>
      </header>
      <Tabs tabs={tabs} value={tab} onChange={(id) => setTab(id as LibraryTab)} />
      {tab === "models" ? <ModelsPanel /> : null}
      {tab === "skills" ? <SkillsPanel /> : null}
      {tab === "mcp" ? <McpPanel /> : null}
      {tab === "marketplace" ? <MarketplacePanel /> : null}
    </div>
  );
}

function ModelsPanel() {
  const qc = useQueryClient();
  const mode = useAppStore((state) => state.mode);
  const models = useQuery({ queryKey: ["models"], queryFn: latticeApi.models });
  const recs = useQuery({ queryKey: ["modelRecommendations", "local_mlx"], queryFn: () => latticeApi.modelRecommendations("local_mlx") });
  const emb = useQuery({ queryKey: ["embeddings"], queryFn: latticeApi.embeddingsStatus });
  const [consent, setConsent] = React.useState(false);
  const [activeModel, setActiveModel] = React.useState<string | null>(null);
  const [progress, setProgress] = React.useState<Record<string, unknown>[]>([]);
  const [lastResult, setLastResult] = React.useState<Record<string, unknown> | null>(null);
  const [lastError, setLastError] = React.useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = React.useState(false);
  const catalog = [
    ...asArray<Record<string, unknown>>((models.data?.data as Record<string, unknown>)?.catalog),
    ...asArray<Record<string, unknown>>((models.data?.data as Record<string, unknown>)?.recommended),
  ];
  const recommendationRows = asArray<Record<string, unknown>>(
    ((recs.data?.data as Record<string, unknown>)?.recommendations as Record<string, unknown> | undefined)?.models,
  );
  const recommendationById = new Map(recommendationRows.map((item) => [String(item.id), item]));
  const loadedIds = asArray<string>((models.data?.data as Record<string, unknown> | undefined)?.loaded);
  const currentId = String((models.data?.data as Record<string, unknown> | undefined)?.current || "");
  const current = catalog.find((model) => loadedIds.includes(String(model.id)) || String(model.id) === currentId);
  const topPick = (((recs.data?.data as Record<string, unknown> | undefined)?.recommendations as Record<string, unknown> | undefined)?.top_pick || null) as Record<string, unknown> | null;
  const latestProgress = progress[progress.length - 1] || null;

  async function prepareModel(loadId: string, engine: string, allowDownload: boolean) {
    setBusy(true);
    setActiveModel(loadId);
    setProgress([]);
    setLastResult(null);
    setLastError(null);
    const result = await latticeApi.streamModelPrepare(
      { model: loadId, engine: engine || "local_mlx", allow_download: allowDownload },
      {
        onProgress: (event) => setProgress((items) => [...items.slice(-8), event]),
        onDone: (event) => setLastResult(event),
        onError: (event) => setLastError(event),
      },
    );
    setBusy(false);
    await qc.invalidateQueries({ queryKey: ["models"] });
    await qc.invalidateQueries({ queryKey: ["modelRecommendations", "local_mlx"] });
    if (!result.ok && !lastError) setLastError(result.data as Record<string, unknown>);
  }

  return (
    <div className="grid gap-4 xl:grid-cols-[1.2fr_0.8fr]">
      <div className="space-y-4">
        <DataPanel title="Model setup flow" description="Environment Analysis -> Recommended Models -> Install -> Download Progress -> Validate -> Load -> Ready" result={recs.data}>
          {(data) => {
            const recommendation = (data as Record<string, unknown>).recommendations as Record<string, unknown> | undefined;
            const profile = (data as Record<string, unknown>).profile as Record<string, unknown> | undefined;
            return (
              <div className="space-y-4">
                <StatGrid stats={[
                  { label: "Computer", value: profile?.os ? `${String(profile.os)} ${String(profile.arch || "")}` : "detected" },
                  { label: "Memory", value: recommendation?.ram_gb ? `${String(recommendation.ram_gb)} GB` : "checking" },
                  { label: "Top pick", value: topPick?.name || topPick?.id || "choose below" },
                  { label: "Current", value: current?.name || currentId || "none" },
                ]} />
                <div className="grid gap-2 md:grid-cols-3 xl:grid-cols-6">
                  {[
                    ["Environment Analysis", true, Cpu],
                    ["Recommended Models", Boolean(topPick || catalog.length), CheckCircle2],
                    ["Install", Boolean(current || latestProgress?.stage === "engine"), PackagePlus],
                    ["Download Progress", Boolean(current || latestProgress?.stage === "download"), Download],
                    ["Validate", Boolean(current || latestProgress?.stage === "smoke_test"), ShieldAlert],
                    ["Load / Ready", Boolean(current || lastResult), PlayCircle],
                  ].map(([label, done, Icon]) => (
                    <div key={String(label)} className="rounded-md border border-border bg-background p-3">
                      {React.createElement(Icon as typeof Cpu, { className: "h-4 w-4 text-primary" })}
                      <div className="mt-2 text-sm font-medium">{String(label)}</div>
                      <Badge variant={done ? "success" : "muted"}>{done ? "ready" : "pending"}</Badge>
                    </div>
                  ))}
                </div>
                <label className="flex items-start gap-2 rounded-md border border-border bg-background p-3 text-sm">
                  <input className="mt-1" type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />
                  <span>
                    Allow this model action to install a missing local runtime or download model files. Lattice AI will not start external downloads without this consent.
                  </span>
                </label>
                {latestProgress ? (
                  <div className="rounded-md border border-border bg-background p-3 text-sm">
                    <div className="font-medium">{String(latestProgress.message || "Preparing model")}</div>
                    <div className="mt-2 h-2 overflow-hidden rounded-full bg-muted">
                      <div className="h-full bg-primary" style={{ width: `${Number(latestProgress.percent || 8)}%` }} />
                    </div>
                    {latestProgress.detail ? <div className="mt-2 text-xs text-muted-foreground">{String(latestProgress.detail)}</div> : null}
                  </div>
                ) : null}
                {lastError ? <ModelRecovery error={lastError} /> : null}
              </div>
            );
          }}
        </DataPanel>
        <DataPanel title="Recommended models" result={models.data}>
        {(data) => (
          <div className="grid gap-2">
            {(catalog.length ? catalog : asArray<Record<string, unknown>>((data as Record<string, unknown>).loaded)).slice(0, 14).map((model, index) => {
              const id = String(model.id || model.model_id || model.name || index);
              const loaded = asArray<string>((data as Record<string, unknown>).loaded).includes(id) || (data as Record<string, unknown>).current === id || model.state === "loaded";
              const loadId = String(model.recommended_load_id || id);
              const engine = String(model.recommended_engine || model.engine || "");
              const recommendation = recommendationById.get(id) || recommendationById.get(loadId) || {};
              const compatibility = (model.runtime_compatibility || recommendation.runtime_compatibility || {}) as Record<string, unknown>;
              const fallbackAvailable = String(compatibility.status || "") === "fallback_available";
              const unsupported = model.load_status === "unsupported" || compatibility.supported === false;
              const downloadRequired = Boolean(model.download_required);
              const loadAvailable = (Boolean(model.load_available) || loaded) && !unsupported;
              const loadStatus = String(model.load_status || (loaded ? "loaded" : "unavailable"));
              const unavailableReason = String(model.unavailable_reason || "Unavailable until the backend reports a local model/runtime ready.");
              const runtimeLabel = String(model.runtime_label || compatibility.preferred_runtime || engine || "local_mlx");
              const canPrepare = loadAvailable || downloadRequired;
              return (
                <div key={id} className="grid gap-3 rounded-md border border-border bg-background p-3 md:grid-cols-[1fr_auto]">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <div className="font-medium">{String(model.name || id)}</div>
                      {topPick?.id === id ? <Badge variant="success">recommended</Badge> : null}
                    </div>
                    <div className="mt-1 text-sm text-muted-foreground">
                      {[model.family || recommendation.family || "local", model.size || recommendation.size].filter(Boolean).map(String).join(" · ")}
                    </div>
                    {unsupported ? (
                      <div className="mt-2 rounded-md border border-amber-500/30 bg-amber-500/10 p-2 text-sm">
                        <div className="font-medium">Runtime component missing</div>
                        <div className="text-muted-foreground">{String(compatibility.user_message || unavailableReason)}</div>
                      </div>
                    ) : fallbackAvailable ? (
                      <div className="mt-2 rounded-md border border-amber-500/30 bg-amber-500/10 p-2 text-sm">
                        <div className="font-medium">Runtime fallback available</div>
                        <div className="text-muted-foreground">{String(compatibility.user_message || "Lattice will try the compatible local runtime path before showing this model as unsupported.")}</div>
                      </div>
                    ) : !loaded && !loadAvailable ? <div className="mt-1 text-xs text-muted-foreground">{unavailableReason}</div> : null}
                    {mode !== "basic" ? (
                      <div className="mt-2 text-xs text-muted-foreground">
                        {runtimeLabel} · {loadId}
                      </div>
                    ) : null}
                    {unsupported || fallbackAvailable ? <AlternativeModels compatibility={compatibility} /> : null}
                  </div>
                  <div className="flex flex-wrap items-center gap-2 md:justify-end">
                    <Badge variant={loaded ? "success" : loadAvailable ? "muted" : "warning"}>{loaded ? "loaded" : loadStatus}</Badge>
                    {loaded ? (
                      <ActionButton label="Unload" action={() => latticeApi.unloadModel(loadId)} invalidate={["models"]} />
                    ) : (
                      <Button
                        variant="outline"
                        disabled={busy || unsupported || !canPrepare || (downloadRequired && !consent)}
                        onClick={() => prepareModel(loadId, engine || "local_mlx", consent)}
                      >
                        {activeModel === loadId && busy ? "Preparing" : downloadRequired ? "Install & Load" : "Validate & Load"}
                      </Button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
        </DataPanel>
      </div>
      <div className="space-y-4">
        <DataPanel title="Embedding provider" result={emb.data}>
          {(data) => mode === "basic" ? <ValuePreview value={(data as Record<string, unknown>).state || (data as Record<string, unknown>).provider || "ready"} /> : <StructuredView value={data} />}
        </DataPanel>
        <DataPanel title="Model validation" result={models.data}>
          {(data) => {
            const profiles = asArray<Record<string, unknown>>((data as Record<string, unknown>).compat_profiles);
            return profiles.length ? (
              <EntityList items={profiles} titleKey="model_id" metaKey="quality_status" limit={6} />
            ) : (
              <EmptyState title="No validation yet" detail="Load a model to run compatibility validation before using it." />
            );
          }}
        </DataPanel>
      </div>
    </div>
  );
}

function AlternativeModels({ compatibility }: { compatibility: Record<string, unknown> }) {
  const alternatives = asArray<Record<string, unknown>>(compatibility.alternatives);
  if (!alternatives.length) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-1">
      {alternatives.slice(0, 3).map((item) => (
        <Badge key={String(item.id || item.name)} variant="muted">{String(item.name || item.id)}</Badge>
      ))}
    </div>
  );
}

function ModelRecovery({ error }: { error: Record<string, unknown> }) {
  const guidance = asArray<string>(error.recovery_guidance);
  return (
    <div className="rounded-md border border-amber-500/30 bg-amber-500/10 p-3 text-sm">
      <div className="font-medium">{String(error.user_message || "Model setup needs attention.")}</div>
      {guidance.length ? (
        <ul className="mt-2 list-inside list-disc text-muted-foreground">
          {guidance.slice(0, 3).map((item) => <li key={item}>{item}</li>)}
        </ul>
      ) : null}
    </div>
  );
}

function SkillsPanel() {
  const qc = useQueryClient();
  const skills = useQuery({ queryKey: ["skills"], queryFn: latticeApi.skills });
  const market = useQuery({ queryKey: ["skillsMarketplace"], queryFn: latticeApi.skillsMarketplace });
  const install = useMutation({
    mutationFn: (skill: Record<string, unknown>) => latticeApi.skillInstall(String(skill.name || skill.id), String(skill.plugin || "")),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["skills"] }),
  });
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <DataPanel title="Installed skills" result={skills.data}>
        {(data) => (
          <div className="grid gap-2">
            {asArray<Record<string, unknown>>((data as Record<string, unknown>).skills).map((skill) => (
              <div key={String(skill.name || skill.id)} className="flex items-center justify-between gap-3 rounded-md border border-border p-3">
                <div>
                  <div className="font-medium">{String(skill.name || skill.id)}</div>
                  <div className="text-sm text-muted-foreground">{String(skill.plugin || skill.description || "")}</div>
                </div>
                <ActionButton label={skill.enabled ? "Disable" : "Enable"} action={() => latticeApi.skillToggle(String(skill.name || skill.id), Boolean(skill.enabled))} invalidate={["skills"]} />
              </div>
            ))}
          </div>
        )}
      </DataPanel>
      <DataPanel title="Skill marketplace" result={market.data}>
        {(data) => (
          <div className="grid gap-2">
            {asArray<Record<string, unknown>>((data as Record<string, unknown>).skills).slice(0, 10).map((skill) => (
              <div key={String(skill.name || skill.id)} className="flex items-center justify-between gap-3 rounded-md border border-border p-3">
                <div>
                  <div className="font-medium">{String(skill.name || skill.id)}</div>
                  <div className="text-sm text-muted-foreground">{String(skill.description || skill.category || "")}</div>
                </div>
                <Button variant="outline" disabled={install.isPending} onClick={() => install.mutate(skill)}>Install</Button>
              </div>
            ))}
          </div>
        )}
      </DataPanel>
    </div>
  );
}

function McpPanel() {
  const [query, setQuery] = React.useState("github");
  const tools = useQuery({ queryKey: ["mcpTools"], queryFn: latticeApi.mcpTools });
  const rec = useMutation({ mutationFn: () => latticeApi.mcpRecommend(query) });
  return (
    <div className="grid gap-4 xl:grid-cols-[1fr_1fr]">
      <DataPanel title="MCP tools" result={tools.data}>
        {(data) => <EntityList items={(data as Record<string, unknown>).tools || (data as Record<string, unknown>).installed_mcps} titleKey="name" metaKey="status" />}
      </DataPanel>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Plug className="h-4 w-4" /> Recommend connector</CardTitle>
          <CardDescription>Calls `/mcp/recommend`; returned installability depends on available connectors.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex gap-2">
            <Input value={query} onChange={(e) => setQuery(e.target.value)} />
            <Button onClick={() => rec.mutate()} disabled={!query.trim() || rec.isPending}>Recommend</Button>
          </div>
          {rec.data ? <OperationResult result={rec.data} successLabel="Recommendation completed" /> : null}
        </CardContent>
      </Card>
    </div>
  );
}

function MarketplacePanel() {
  const templates = useQuery({ queryKey: ["templates"], queryFn: latticeApi.templates });
  const plugins = useQuery({ queryKey: ["plugins"], queryFn: latticeApi.pluginsRegistry });
  const dir = useQuery({ queryKey: ["pluginsDirectory"], queryFn: latticeApi.pluginsDirectory });
  return (
    <div className="grid gap-4 xl:grid-cols-3">
      <DataPanel title="Templates" result={templates.data}>
        {(data) => <EntityList items={(data as Record<string, unknown>).templates} titleKey="name" metaKey="kind" />}
      </DataPanel>
      <DataPanel title="Installed plugins" result={plugins.data}>
        {(data) => <EntityList items={(data as Record<string, unknown>).plugins} titleKey="name" metaKey="status" />}
      </DataPanel>
      <DataPanel title="Plugin directory" result={dir.data}>
        {(data) => <EntityList items={(data as Record<string, unknown>).plugins} titleKey="name" metaKey="category" />}
      </DataPanel>
      <Card className="xl:col-span-3">
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><PackagePlus className="h-4 w-4" /> Template install</CardTitle>
          <CardDescription>Install controls are enabled only for template records returned by the backend.</CardDescription>
        </CardHeader>
        <CardContent>
          {asArray<Record<string, unknown>>((templates.data?.data as Record<string, unknown>)?.templates).length ? (
            <div className="flex flex-wrap gap-2">
              {asArray<Record<string, unknown>>((templates.data?.data as Record<string, unknown>)?.templates).slice(0, 6).map((template) => (
                <ActionButton key={String(template.id || template.name)} label={`Install ${String(template.name || template.id)}`} action={() => latticeApi.installTemplate(template)} />
              ))}
            </div>
          ) : <EntityList items={[]} />}
        </CardContent>
      </Card>
    </div>
  );
}
