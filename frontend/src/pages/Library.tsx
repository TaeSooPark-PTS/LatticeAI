import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Boxes, Cpu, PackagePlus, Plug, Puzzle } from "lucide-react";
import { latticeApi } from "@/api/client";
import { ActionButton, DataPanel, EntityList, JsonView, Tabs } from "@/components/primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
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
  const models = useQuery({ queryKey: ["models"], queryFn: latticeApi.models });
  const emb = useQuery({ queryKey: ["embeddings"], queryFn: latticeApi.embeddingsStatus });
  const catalog = [
    ...asArray<Record<string, unknown>>((models.data?.data as Record<string, unknown>)?.catalog),
    ...asArray<Record<string, unknown>>((models.data?.data as Record<string, unknown>)?.recommended),
  ];
  return (
    <div className="grid gap-4 xl:grid-cols-[1.2fr_0.8fr]">
      <DataPanel title="Model catalog" result={models.data}>
        {(data) => (
          <div className="grid gap-2">
            {(catalog.length ? catalog : asArray<Record<string, unknown>>((data as Record<string, unknown>).loaded)).slice(0, 14).map((model, index) => {
              const id = String(model.id || model.model_id || model.name || index);
              const loaded = asArray<string>((data as Record<string, unknown>).loaded).includes(id) || (data as Record<string, unknown>).current === id || model.state === "loaded";
              const loadId = String(model.recommended_load_id || id);
              const engine = String(model.recommended_engine || model.engine || "");
              const loadAvailable = Boolean(model.load_available) || loaded;
              const loadStatus = String(model.load_status || (loaded ? "loaded" : "unavailable"));
              const unavailableReason = String(model.unavailable_reason || "Unavailable until the backend reports a local model/runtime ready.");
              return (
                <div key={id} className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border bg-background p-3">
                  <div>
                    <div className="font-medium">{String(model.name || id)}</div>
                    <div className="text-sm text-muted-foreground">{String(model.family || model.engine || model.recommended_engine || "local")}</div>
                    {!loaded && !loadAvailable ? <div className="mt-1 text-xs text-muted-foreground">{unavailableReason}</div> : null}
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge variant={loaded ? "success" : loadAvailable ? "muted" : "warning"}>{loaded ? "loaded" : loadStatus}</Badge>
                    <ActionButton
                      label={loaded ? "Unload" : "Load"}
                      action={() => loaded ? latticeApi.unloadModel(loadId) : latticeApi.loadModel(loadId, engine, false)}
                      invalidate={["models"]}
                      disabled={!loaded && !loadAvailable}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </DataPanel>
      <DataPanel title="Embedding provider" result={emb.data}>
        {(data) => <JsonView value={data} />}
      </DataPanel>
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
          {rec.data ? <JsonView value={rec.data.data || rec.data.error} /> : null}
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
