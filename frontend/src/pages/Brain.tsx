import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BrainCircuit, DatabaseBackup, Filter, Focus, Layers3, LocateFixed, Search, Sparkles } from "lucide-react";
import { latticeApi } from "@/api/client";
import { type BrainState } from "@/components/LivingBrain";
import { ActionButton, DataPanel, EmptyState, EntityList, KeyValueList, LoadingPanel, OperationResult, StatGrid, StructuredView, Tabs } from "@/components/primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { BrainHome } from "@/features/brain/BrainHome";
import { VectorFreshnessNotice } from "@/features/brain/BrainSignals";
import { useAppStore } from "@/store/appStore";
import { t, type Language } from "@/i18n";
import { asArray, fmtNumber, pct, titleize } from "@/lib/utils";
import { CytoscapeGraph } from "./brain/CytoscapeGraph";
import { buildExplorerModel, isRecord, parseGraph, type LabelMode } from "./brain/graphExplorer";
import { navigateHash } from "@/features/brain/navigation";

type BrainTab = "graph" | "knowledge" | "memory";

const tabs: Array<{ id: BrainTab; labelKey: string }> = [
  { id: "knowledge", labelKey: "brain.tab.knowledge" },
  { id: "memory", labelKey: "brain.tab.memory" },
  { id: "graph", labelKey: "brain.tab.graph" },
];

export function BrainPage({ initialTab }: { initialTab?: string }) {
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  const normalizedInitialTab = normalizeBrainTab(initialTab);
  const [tab, setTab] = React.useState<BrainTab>(normalizedInitialTab);
  React.useEffect(() => {
    setTab(normalizeBrainTab(initialTab));
  }, [initialTab]);
  const selectTab = (next: BrainTab) => {
    setTab(next);
    navigateHash("/" + ({ knowledge: "hybrid-search", memory: "memory", graph: "knowledge-graph" } as const)[next]);
  };
  const graph = useQuery({ queryKey: ["graph"], queryFn: latticeApi.graph });
  const coverage = useQuery({ queryKey: ["coverage"], queryFn: latticeApi.graphCoverage });

  return (
    <div className="product-page memory-page space-y-5">
      <header className="brain-layer-header">
        <div>
          <div className="page-kicker"><BrainCircuit className="h-4 w-4" /> {tabLabel(language, tab)}</div>
          <h1>{tabHeadline(language, tab)}</h1>
        </div>
        <div className="brain-layer-meter">
          <span>{t(language, "graph.coverage")}</span>
          <strong>{pct((coverage.data?.data as Record<string, unknown>)?.coverage_ratio)}</strong>
        </div>
      </header>
      <Tabs tabs={tabs.map((item) => ({ id: item.id, label: t(language, item.labelKey) }))} value={tab} onChange={(id) => selectTab(id as BrainTab)} />
      <VectorFreshnessNotice language={language} />

      {tab === "graph" ? (
        graph.isLoading ? <LoadingPanel title={t(language, "graph.deep.title")} /> : (
          <DataPanel title={t(language, "graph.advanced.title")} description={mode === "basic" ? t(language, "graph.advanced.desc.basic") : t(language, "graph.advanced.desc.other")} result={graph.data}>
            {(data) => <DigitalBrainExplorer data={data} />}
          </DataPanel>
        )
      ) : null}
      {tab === "knowledge" ? <HybridSearch /> : null}
      {tab === "memory" ? <UnifiedMemoryPanel /> : null}
    </div>
  );
}

function normalizeBrainTab(tab?: string): BrainTab {
  if (tab === "knowledge" || tab === "search") return "knowledge";
  if (tab === "memory" || tab === "relationships" || tab === "provenance" || tab === "sources" || tab === "portability" || tab === "care") return "memory";
  return "graph";
}

function tabLabel(language: Language, tab: BrainTab) {
  const labelKey = tabs.find((item) => item.id === tab)?.labelKey;
  return labelKey ? t(language, labelKey) : t(language, "brain.title");
}

function tabHeadline(language: Language, tab: BrainTab) {
  return t(language, `brain.headline.${tab}`);
}

function GraphStatus({ data }: { data: Record<string, unknown> }) {
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  const nodeTypes = Object.keys((data.nodes as Record<string, unknown>) || {});
  const edgeTypes = Object.keys((data.edges as Record<string, unknown>) || {});
  return (
    <div className="space-y-3">
      <StatGrid stats={[
        { label: t(language, "brain.stats.memories"), value: data.total_nodes ?? nodeTypes.reduce((sum, key) => sum + Number(((data.nodes as Record<string, unknown>) || {})[key] || 0), 0) },
        { label: t(language, "brain.stats.links"), value: data.total_edges ?? edgeTypes.reduce((sum, key) => sum + Number(((data.edges as Record<string, unknown>) || {})[key] || 0), 0) },
        { label: t(language, "brain.stats.memoryKinds"), value: nodeTypes.length },
        { label: t(language, "brain.stats.linkKinds"), value: edgeTypes.length },
      ]} />
      {mode === "basic" ? (
        <div className="flex flex-wrap gap-1">
          {[...nodeTypes, ...edgeTypes].slice(0, 10).map((item) => <Badge key={item} variant="muted">{titleize(item)}</Badge>)}
        </div>
      ) : <StructuredView value={{ memory_kinds: nodeTypes, link_kinds: edgeTypes }} />}
    </div>
  );
}

function RetrievalStatus({ data }: { data: Record<string, unknown> }) {
  const language = useAppStore((state) => state.language);
  const pipelines = isRecord(data.pipelines) ? data.pipelines : {};
  const rows = Object.entries(pipelines).map(([name, value]) => ({
    name: titleize(name),
    status: isRecord(value) ? String(value.state || value.status || t(language, "brain.value.reported")) : t(language, "brain.value.reported"),
    description: isRecord(value) ? Object.entries(value).filter(([key]) => key !== "state" && key !== "status").slice(0, 3).map(([key, item]) => `${titleize(key)}: ${String(item)}`).join(" · ") : String(value),
  }));
  return rows.length ? <EntityList items={rows} titleKey="name" metaKey="status" /> : <StructuredView value={data} />;
}

function MemoryStatus({ data }: { data: Record<string, unknown> }) {
  const language = useAppStore((state) => state.language);
  const usage = isRecord(data.usage) ? data.usage : {};
  const sources = asArray<Record<string, unknown>>(data.sources || data.tiers);
  return (
    <div className="space-y-3">
      <StatGrid stats={[
        { label: t(language, "brain.stats.sources"), value: usage.sources ?? asArray(data.sources).length },
        { label: t(language, "brain.stats.items"), value: usage.total_items ?? asArray(data.sources).reduce((sum, item) => sum + Number(isRecord(item) ? item.count || 0 : 0), 0) },
        { label: t(language, "brain.stats.bytes"), value: usage.total_bytes ?? 0 },
        { label: t(language, "brain.stats.health"), value: data.health || t(language, "brain.value.reported") },
      ]} />
      <SourceProvenanceList items={sources} limit={6} />
    </div>
  );
}

function DigitalBrainExplorer({ data }: { data: unknown }) {
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  const parsed = React.useMemo(() => parseGraph(data, language), [data, language]);
  const [search, setSearch] = React.useState("");
  const [groupFilter, setGroupFilter] = React.useState("all");
  const [minImportance, setMinImportance] = React.useState(mode === "basic" ? 0.1 : 0);
  const [labelMode, setLabelMode] = React.useState<LabelMode>("important");
  const [collapsedGroups, setCollapsedGroups] = React.useState<Set<string>>(new Set());
  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  const [fitSignal, setFitSignal] = React.useState(0);
  const backendSearch = useMutation({ mutationFn: () => latticeApi.hybridSearch(search.trim()) });
  const model = React.useMemo(() => buildExplorerModel({
    graph: parsed,
    search,
    groupFilter,
    minImportance,
    collapsedGroups,
    selectedId,
    labelMode,
    maxNodes: 220,
  }), [parsed, search, groupFilter, minImportance, collapsedGroups, selectedId, labelMode]);
  const selected = parsed.nodes.find((node) => node.id === selectedId);
  const selectedGroup = selectedId?.startsWith("group:") ? model.groups.find((group) => group.id === selectedId.replace("group:", "")) : null;
  const toggleGroup = (id: string) => {
    setCollapsedGroups((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };
  React.useEffect(() => {
    if (mode === "basic" && minImportance < 0.1) setMinImportance(0.1);
  }, [mode, minImportance]);
  if (!parsed.nodes.length) {
    return (
      <EmptyState
        title={t(language, "graph.empty.title")}
        detail={t(language, "graph.empty.detail")}
      />
    );
  }
  return (
    <div className="space-y-4">
      <div className="grid gap-3 xl:grid-cols-[1fr_220px_180px_170px]">
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input className="pl-9" value={search} onChange={(event) => setSearch(event.target.value)} placeholder={mode === "basic" ? t(language, "graph.search.basic") : t(language, "graph.search.advanced")} />
        </div>
        <select className="h-9 rounded-md border border-border bg-background px-3 text-sm" value={groupFilter} onChange={(event) => setGroupFilter(event.target.value)}>
          <option value="all">{t(language, "graph.group.all")}</option>
          {model.groups.map((group) => <option key={group.id} value={group.id}>{group.label}</option>)}
        </select>
        <select className="h-9 rounded-md border border-border bg-background px-3 text-sm" value={labelMode} onChange={(event) => setLabelMode(event.target.value as LabelMode)}>
          <option value="important">{t(language, "graph.labels.important")}</option>
          <option value="all">{t(language, "graph.labels.all")}</option>
          <option value="off">{t(language, "graph.labels.off")}</option>
        </select>
        <Button variant="outline" onClick={() => setFitSignal((value) => value + 1)}><LocateFixed className="h-4 w-4" /> {t(language, "graph.fit")}</Button>
      </div>
      <div className="grid gap-3 lg:grid-cols-[1fr_18rem]">
        <Card>
          <CardHeader className="flex-row items-start justify-between gap-3">
            <div>
              <CardTitle className="flex items-center gap-2"><Layers3 className="h-4 w-4" /> {t(language, "graph.deep.title")}</CardTitle>
              <CardDescription>
                {t(language, "graph.deep.summary", { nodes: fmtNumber(model.visibleNodes.length), edges: fmtNumber(model.visibleEdges.length), total: fmtNumber(model.totalNodes) })}
              </CardDescription>
            </div>
            <Badge variant={model.hiddenByFilters ? "warning" : "success"}>{model.hiddenByFilters ? t(language, "graph.filtered", { count: fmtNumber(model.hiddenByFilters) }) : t(language, "graph.allInView")}</Badge>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <Filter className="h-4 w-4 text-muted-foreground" />
              <label className="text-sm text-muted-foreground" htmlFor="importance">{t(language, "graph.importance")}</label>
              <input
                id="importance"
                type="range"
                min="0"
                max="0.9"
                step="0.05"
                value={minImportance}
                onChange={(event) => setMinImportance(Number(event.target.value))}
                className="w-44"
                aria-label={t(language, "graph.minImportance.aria")}
              />
              <Badge variant="muted">{Math.round(minImportance * 100)}%+</Badge>
              {selectedId ? <Button variant="outline" size="sm" onClick={() => setSelectedId(null)}>{t(language, "graph.clearFocus")}</Button> : null}
              {search.trim() ? <Button variant="outline" size="sm" onClick={() => backendSearch.mutate()} disabled={backendSearch.isPending}>{t(language, "graph.searchAll")}</Button> : null}
            </div>
            <div className="flex flex-wrap gap-2">
              {model.groups.map((group) => (
                <button
                  key={group.id}
                  onClick={() => toggleGroup(group.id)}
                  className="inline-flex items-center gap-2 rounded-md border border-border bg-background px-2.5 py-1.5 text-xs hover:bg-muted"
                >
                  <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: group.color }} />
                  <span>{group.label}</span>
                  <Badge variant={group.collapsed ? "warning" : "muted"}>{group.collapsed ? t(language, "graph.collapsed") : fmtNumber(group.count)}</Badge>
                </button>
              ))}
            </div>
            <CytoscapeGraph
              model={model}
              selectedId={selectedId}
              onSelect={setSelectedId}
              fitSignal={fitSignal}
              ariaLabel={t(language, "graph.canvas.aria")}
            />
          </CardContent>
        </Card>
        <aside className="space-y-3">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2"><Focus className="h-4 w-4" /> {t(language, "graph.focus.title")}</CardTitle>
              <CardDescription>{t(language, "graph.focus.desc")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {selected ? (
                <>
                  <div>
                    <div className="text-lg font-semibold">{selected.label}</div>
                    <div className="mt-1 flex flex-wrap gap-1">
                      <Badge variant="muted">{selected.type}</Badge>
                      <Badge variant="muted">{model.groups.find((group) => group.id === selected.group)?.label || selected.group}</Badge>
                      <Badge variant="success">{t(language, "graph.importanceBadge", { n: Math.round(selected.importance * 100) })}</Badge>
                    </div>
                  </div>
                  {selected.summary ? <p className="text-sm text-muted-foreground">{selected.summary}</p> : null}
                  {mode === "basic" ? (
                    <KeyValueList data={{
                      connections: selected.degree,
                      source: selected.source || t(language, "graph.source.none"),
                      source_type: sourceType(selected.raw, language),
                      created_at: sourceCreatedAt(selected.raw) || t(language, "graph.created.none"),
                    }} />
                  ) : (
                    <StructuredView value={{
                      id: selected.id,
                      degree: selected.degree,
                      source: selected.source || t(language, "graph.source.none"),
                      source_type: sourceType(selected.raw, language),
                      created_at: sourceCreatedAt(selected.raw) || t(language, "graph.created.none"),
                    }} />
                  )}
                  {selected.source ? <Button variant="outline" size="sm" onClick={() => void navigator.clipboard?.writeText(selected.source)}>{t(language, "graph.copySource")}</Button> : null}
                </>
              ) : selectedGroup ? (
                <div className="space-y-2">
                  <Badge variant="warning">{t(language, "graph.collapsedGroup")}</Badge>
                  <div className="text-lg font-semibold">{selectedGroup.label}</div>
                  <Button variant="outline" onClick={() => toggleGroup(selectedGroup.id)}>{t(language, "graph.expandGroup")}</Button>
                </div>
              ) : <EmptyState title={t(language, "graph.nothingSelected.title")} detail={t(language, "graph.nothingSelected.detail")} />}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>{t(language, "graph.important.title")}</CardTitle>
              <CardDescription>{t(language, "graph.important.desc")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2">
              {model.visibleNodes.slice(0, 8).map((node) => (
                <button
                  key={node.id}
                  onClick={() => setSelectedId(node.id)}
                  className="block w-full rounded-md border border-border bg-background p-2 text-left text-sm hover:bg-muted"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium">{node.label}</span>
                    <Badge variant="muted">{node.type}</Badge>
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground">{t(language, "brain.graph.nodeStats", { importance: Math.round(node.importance * 100), links: fmtNumber(node.degree) })}</div>
                </button>
              ))}
            </CardContent>
          </Card>
        </aside>
      </div>
      {backendSearch.data ? (
        <DataPanel title={t(language, "brain.search.results")} result={backendSearch.data}>
          {(result) => <EntityList items={(result as Record<string, unknown>).matches || result} titleKey="title" metaKey="type" limit={8} />}
        </DataPanel>
      ) : null}
    </div>
  );
}

function HybridSearch() {
  const language = useAppStore((state) => state.language);
  const [query, setQuery] = React.useState("");
  const search = useMutation({ mutationFn: () => latticeApi.hybridSearch(query) });
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Search className="h-4 w-4" /> {t(language, "brain.search.title")}</CardTitle>
        <CardDescription>{t(language, "brain.search.detail")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input placeholder={t(language, "brain.search.placeholder")} value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => e.key === "Enter" && !e.nativeEvent.isComposing && search.mutate()} />
          <Button onClick={() => search.mutate()} disabled={!query.trim() || search.isPending}>{t(language, "brain.search.cta")}</Button>
        </div>
        {search.data ? (
          <DataPanel title={t(language, "brain.search.results")} result={search.data}>
            {(data) => <EntityList items={(data as Record<string, unknown>).matches || data} titleKey="title" metaKey="type" limit={12} />}
          </DataPanel>
        ) : null}
      </CardContent>
    </Card>
  );
}

function UnifiedMemoryPanel() {
  const qc = useQueryClient();
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  const [recallQuery, setRecallQuery] = React.useState("");
  const [importArtifact, setImportArtifact] = React.useState("");
  const [expandedSection, setExpandedSection] = React.useState<"recall" | "sources" | "backup" | null>("recall");

  const manager = useQuery({ queryKey: ["memoryManager"], queryFn: latticeApi.memoryManager });
  const provenance = useQuery({ queryKey: ["provenance"], queryFn: () => latticeApi.graphProvenance(80) });
  const port = useQuery({ queryKey: ["portability"], queryFn: latticeApi.graphPortability });
  const recall = useMutation({ mutationFn: () => latticeApi.memoryRecall(recallQuery, 25) });
  const importMutation = useMutation({
    mutationFn: async () => {
      try {
        return await latticeApi.graphImport(JSON.parse(importArtifact), true);
      } catch (err) {
        return { ok: false, status: 0, data: {}, source: "unavailable" as const, error: err instanceof Error ? err.message : String(err) };
      }
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["portability"] }),
  });

  const managerData = isRecord((manager.data as Record<string, unknown>)?.data) ? (manager.data as Record<string, unknown>).data as Record<string, unknown> : {};
  const portData = isRecord((port.data as Record<string, unknown>)?.data) ? (port.data as Record<string, unknown>).data as Record<string, unknown> : {};
  const portStats = isRecord(portData.stats) ? portData.stats : {};
  const portStorage = isRecord(portData.storage) ? portData.storage : {};
  const usage = isRecord(managerData.usage) ? managerData.usage : {};

  const toggle = (section: "recall" | "sources" | "backup") =>
    setExpandedSection((prev) => (prev === section ? null : section));

  return (
    <div className="space-y-4">
      {/* Unified summary bar */}
      <Card>
        <CardContent className="py-4">
          <StatGrid stats={[
            { label: t(language, "brain.stats.sources"), value: usage.sources ?? 0 },
            { label: t(language, "brain.stats.items"), value: usage.total_items ?? 0 },
            { label: t(language, "brain.stats.format"), value: portData.graph_schema_version || portData.schema_version || "–" },
            { label: t(language, "brain.stats.storage"), value: portStorage.engine || "–" },
          ]} />
        </CardContent>
      </Card>

      {/* Section 1 — Memory Recall */}
      <Card>
        <CardHeader
          className="cursor-pointer select-none"
          onClick={() => toggle("recall")}
        >
          <CardTitle className="flex items-center gap-2">
            <Sparkles className="h-4 w-4" />
            {t(language, "brain.memory.recall.title")}
            <span className="ml-auto text-xs text-muted-foreground">{expandedSection === "recall" ? "▲" : "▼"}</span>
          </CardTitle>
          <CardDescription>{t(language, "brain.memory.recall.detail")}</CardDescription>
        </CardHeader>
        {expandedSection === "recall" && (
          <CardContent className="space-y-3">
            <div className="flex flex-col gap-2 sm:flex-row">
              <Input
                value={recallQuery}
                onChange={(e) => setRecallQuery(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && !e.nativeEvent.isComposing && recallQuery.trim() && recall.mutate()}
                placeholder={t(language, "brain.memory.recall.placeholder")}
              />
              <Button disabled={!recallQuery.trim() || recall.isPending} onClick={() => recall.mutate()}>{t(language, "brain.memory.recall.action")}</Button>
            </div>
            {mode !== "basic" && (
              <div className="flex flex-wrap gap-2">
                <ActionButton label={t(language, "brain.memory.compact")} action={() => latticeApi.memoryCompact()} />
                <ActionButton label={t(language, "brain.memory.rebuildVector")} action={() => latticeApi.memoryRebuild()} />
              </div>
            )}
            {recall.data ? <OperationResult result={recall.data} successLabel={t(language, "brain.memory.recall.completed")} /> : null}
          </CardContent>
        )}
      </Card>

      {/* Section 2 — Sources & Provenance */}
      <Card>
        <CardHeader
          className="cursor-pointer select-none"
          onClick={() => toggle("sources")}
        >
          <CardTitle className="flex items-center gap-2">
            <Layers3 className="h-4 w-4" />
            {t(language, "brain.sources.title")}
            <span className="ml-auto text-xs text-muted-foreground">{expandedSection === "sources" ? "▲" : "▼"}</span>
          </CardTitle>
          <CardDescription>{t(language, "brain.sources.detail")}</CardDescription>
        </CardHeader>
        {expandedSection === "sources" && (
          <CardContent className="space-y-3">
            {provenance.isLoading ? (
              <LoadingPanel title={t(language, "brain.sources.loading")} />
            ) : (
              <SourceProvenanceList
                items={
                  isRecord((provenance.data as Record<string, unknown>)?.data)
                    ? ((provenance.data as Record<string, unknown>).data as Record<string, unknown>).items || (provenance.data as Record<string, unknown>).data
                    : provenance.data
                }
                limit={10}
              />
            )}
          </CardContent>
        )}
      </Card>

      {/* Section 3 — Export & Backup */}
      <Card>
        <CardHeader
          className="cursor-pointer select-none"
          onClick={() => toggle("backup")}
        >
          <CardTitle className="flex items-center gap-2">
            <DatabaseBackup className="h-4 w-4" />
            {t(language, "brain.portability.title")}
            <span className="ml-auto text-xs text-muted-foreground">{expandedSection === "backup" ? "▲" : "▼"}</span>
          </CardTitle>
          <CardDescription>{t(language, "brain.portability.detail")}</CardDescription>
        </CardHeader>
        {expandedSection === "backup" && (
          <CardContent className="space-y-3">
            <div className="flex flex-wrap gap-2">
              <ActionButton label={t(language, "brain.portability.export")} action={() => latticeApi.graphExport()} />
              <ActionButton label={t(language, "brain.portability.backup")} action={() => latticeApi.graphBackup()} />
            </div>
            <Textarea
              value={importArtifact}
              onChange={(e) => setImportArtifact(e.target.value)}
              placeholder={t(language, "brain.portability.importPlaceholder")}
            />
            <Button
              variant="outline"
              disabled={!importArtifact.trim() || importMutation.isPending}
              onClick={() => importMutation.mutate()}
            >
              {t(language, "brain.portability.previewImport")}
            </Button>
            {importMutation.data ? <OperationResult result={importMutation.data} successLabel={t(language, "brain.portability.importCompleted")} /> : null}
          </CardContent>
        )}
      </Card>
    </div>
  );
}

function SourceProvenanceList({ items, limit = 8 }: { items: unknown; limit?: number }) {
  const language = useAppStore((state) => state.language);
  const rows = asArray<Record<string, unknown>>(items).slice(0, limit);
  if (!rows.length) return <EmptyState title={t(language, "brain.sources.empty")} detail={t(language, "brain.sources.emptyDetail")} />;
  return (
    <div className="grid gap-2">
      {rows.map((item, index) => {
        const title = String(item.title || item.label || item.source_title || item.filename || item.path || item.source || t(language, "brain.sources.fallback", { index: index + 1 }));
        const path = String(item.path || item.source_path || item.source || item.conversation_id || "");
        const type = sourceType(item, language);
        const created = sourceCreatedAt(item);
        return (
          <div key={String(item.id || item.source_id || title)} className="rounded-lg border border-border bg-background/55 p-3">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <div className="font-medium">{title}</div>
                <div className="mt-1 text-xs text-muted-foreground">
                  {type} · {created || t(language, "brain.sources.createdUnknown")}
                </div>
              </div>
              <Badge variant="muted">{t(language, path ? "brain.sources.inspectable" : "brain.sources.missingProvenance")}</Badge>
            </div>
            {path ? (
              <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <span className="break-all">{path}</span>
                <Button variant="outline" size="sm" onClick={() => void navigator.clipboard?.writeText(path)}>{t(language, "brain.sources.copy")}</Button>
              </div>
            ) : (
              <p className="mt-2 text-xs text-muted-foreground">{t(language, "brain.sources.legacyMissing")}</p>
            )}
          </div>
        );
      })}
    </div>
  );
}

function sourceType(item: Record<string, unknown>, language: Language) {
  const metadata = isRecord(item.metadata) ? item.metadata : {};
  const raw = String(item.source_type || item.type || item.kind || metadata.source_type || metadata.role || "").toLowerCase();
  if (/chat|conversation|message/.test(raw)) return t(language, "brain.sources.type.chat");
  if (/document|upload|file|pdf|markdown|text/.test(raw)) return t(language, "brain.sources.type.document");
  if (/import|archive|restore/.test(raw)) return t(language, "brain.sources.type.import");
  if (/manual|note/.test(raw)) return t(language, "brain.sources.type.manual");
  return t(language, "brain.sources.type.unknown");
}

function sourceCreatedAt(item: Record<string, unknown>) {
  const metadata = isRecord(item.metadata) ? item.metadata : {};
  const value = item.created_at || item.timestamp || item.updated_at || metadata.created_at || metadata.timestamp;
  return value ? String(value) : "";
}
