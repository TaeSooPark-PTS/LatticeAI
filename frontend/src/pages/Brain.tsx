import * as React from "react";
// Route-scoped copy: importing the namespace registers it into the shared
// table and keeps it inside this lazy chunk instead of the entry bundle.
import "@/i18n/brain";
import "@/i18n/workspace";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, BrainCircuit, DatabaseBackup, Filter, Focus, Layers3, LocateFixed, Search, Share2, Sparkles } from "lucide-react";
import { latticeApi } from "@/api/client";
import { type BrainState } from "@/components/LivingBrain";
import { ActionButton, DataPanel, EmptyState, EntityList, KeyValueList, LoadingPanel, OperationResult, StatGrid, StructuredView, Tabs } from "@/components/primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { BrainHome } from "@/features/brain/BrainHome";
import { StaleEmbedderNotice, VectorFreshnessNotice } from "@/features/brain/BrainSignals";
import { useAppStore } from "@/store/appStore";
import { t, type Language } from "@/i18n";
import { asArray, fmtNumber, titleize } from "@/lib/utils";
import { CytoscapeGraph } from "./brain/CytoscapeGraph";
import { buildExplorerModel, isRecord, parseGraph, type GraphGroup, type GraphNode, type LabelMode } from "./brain/graphExplorer";
import { navigateHash } from "@/features/brain/navigation";

type BrainTab = "knowledge" | "memory";

/**
 * The map is a place you go *into* from the memory screens, not a third thing
 * you choose between. As a peer tab it was the first choice a newcomer saw on
 * this screen — a force-directed node cloud offered with the same weight as
 * "search your memory", which is the one thing most people came here to do. It
 * is now a subview: reached from a named secondary link, and left with a back
 * control. The URL (`#/knowledge-graph`) and everything it can do are unchanged.
 */
type BrainView = BrainTab | "graph";

const tabs: Array<{ id: BrainTab; labelKey: string }> = [
  { id: "knowledge", labelKey: "brain.tab.knowledge" },
  { id: "memory", labelKey: "brain.tab.memory" },
];

const viewPaths: Record<BrainView, string> = {
  knowledge: "hybrid-search",
  memory: "memory",
  graph: "knowledge-graph",
};

export function BrainPage({ initialTab }: { initialTab?: string }) {
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  const [view, setView] = React.useState<BrainView>(() => normalizeBrainView(initialTab));
  React.useEffect(() => {
    setView(normalizeBrainView(initialTab));
  }, [initialTab]);
  const selectView = (next: BrainView) => {
    setView(next);
    navigateHash("/" + viewPaths[next]);
  };
  const graph = useQuery({ queryKey: ["graph"], queryFn: latticeApi.graph });
  const coverage = useQuery({ queryKey: ["coverage"], queryFn: latticeApi.graphCoverage });
  const isGraph = view === "graph";

  return (
    <div className="product-page memory-page space-y-5">
      <header className="brain-layer-header">
        <div>
          <div className="page-kicker"><BrainCircuit className="h-4 w-4" /> {viewLabel(language, view)}</div>
          <h1>{t(language, `brain.headline.${view}`)}</h1>
        </div>
        {/* Coverage meter */}
        <CoverageMeter language={language} data={coverage.data?.data as Record<string, unknown> | undefined} />
      </header>
      {isGraph ? (
        <nav className="memory-subview-bar" aria-label={t(language, "brain.graph.subviewAria")}>
          <button type="button" className="memory-subview-back" onClick={() => selectView("knowledge")}>
            <ArrowLeft className="h-4 w-4" aria-hidden="true" />
            {t(language, "brain.graph.back")}
          </button>
          <span className="memory-subview-title">{t(language, "brain.tab.graph")}</span>
        </nav>
      ) : (
        <Tabs tabs={tabs.map((item) => ({ id: item.id, label: t(language, item.labelKey) }))} value={view} onChange={(id) => selectView(id as BrainTab)} />
      )}
      <StaleEmbedderNotice language={language} />
      <VectorFreshnessNotice language={language} />

      {view === "knowledge" ? <HybridSearch /> : null}
      {view === "memory" ? <UnifiedMemoryPanel /> : null}
      {isGraph ? (
        graph.isLoading ? (
          <LoadingPanel title={t(language, "graph.deep.title")} />
        ) : graph.data?.ok === false ? (
          <OperationResult result={graph.data} />
        ) : (
          <DigitalBrainExplorer data={graph.data?.data ?? graph.data} />
        )
      ) : (
        <button
          type="button"
          className="memory-map-entry"
          data-testid="open-connections-map"
          onClick={() => selectView("graph")}
        >
          <Share2 className="h-4 w-4" aria-hidden="true" />
          <span className="memory-map-entry-copy">
            <strong>{t(language, "brain.graph.open")}</strong>
            <small>{t(language, "brain.graph.openHint")}</small>
          </span>
        </button>
      )}
    </div>
  );
}

function normalizeBrainView(tab?: string): BrainView {
  if (tab === "graph" || tab === "knowledge-graph") return "graph";
  if (tab === "memory" || tab === "relationships" || tab === "provenance" || tab === "sources" || tab === "portability" || tab === "care") return "memory";
  return "knowledge";
}

/**
 * Provenance coverage as a sentence, not a ratio. The payload counts nodes that
 * record where they came from; a reader needs the two numbers to judge it, and
 * the reason some memories have no source at all.
 */
/**
 * The selected node's group, by name. `groups` is `ExplorerModel.groups` —
 * every id any node was assigned to during parsing, `node.group` included —
 * so the lookup miss this defends against cannot happen for a real node.
 */
function groupLabelFor(node: GraphNode, groups: GraphGroup[]): string {
  const found = groups.find((group) => group.id === node.group);
  /* v8 ignore next -- unreachable: see the function doc comment above. Kept
     as defense-in-depth. */
  return found?.label || node.group;
}

/** Graph node classes are schema words; show the reader's language. */
function graphTypeLabel(type: string, language: Language) {
  /* v8 ignore next -- unreachable: both call sites pass a `GraphNode.type`,
     which `parseGraph`'s `field()` helper always resolves to a non-empty
     string (falling back to the literal "Node"). Kept as defense-in-depth. */
  const raw = String(type || "");
  const canonical = raw.charAt(0).toUpperCase() + raw.slice(1).toLowerCase();
  for (const candidate of [raw, canonical]) {
    const key = `ui.entity.${candidate}`;
    const label = t(language, key);
    if (label !== key) return label;
  }
  return titleize(raw);
}

function CoverageMeter({ language, data }: { language: Language; data: Record<string, unknown> | undefined }) {
  const total = Math.max(0, Math.round(Number(data?.total_nodes) || 0));
  const covered = Math.max(0, Math.round(Number(data?.nodes_with_provenance) || 0));
  if (!total) {
    return (
      <div className="brain-layer-meter">
        <span>{t(language, "graph.coverage")}</span>
        <strong>{t(language, "graph.coverage.empty")}</strong>
      </div>
    );
  }
  const detail = t(language, "graph.coverage.detail", { covered: fmtNumber(covered), total: fmtNumber(total) });
  return (
    <div className="brain-layer-meter" title={detail}>
      <span>{t(language, "graph.coverage")}</span>
      <strong>{t(language, "graph.coverage.value", { covered: fmtNumber(covered), total: fmtNumber(total) })}</strong>
      <small>{detail}</small>
    </div>
  );
}

export function viewLabel(language: Language, view: BrainView) {
  if (view === "graph") return t(language, "brain.tab.graph");
  const labelKey = tabs.find((item) => item.id === view)?.labelKey;
  return labelKey ? t(language, labelKey) : t(language, "brain.title");
}

// Currently unreferenced by the page (kept for the advanced status panels);
// exported so tests exercise it until a product decision retires it.
export function GraphStatus({ data }: { data: Record<string, unknown> }) {
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  // Computed once and reused below: re-deriving `(data.nodes || {})` a second
  // time inside the reduce made its own `|| {}` fallback unreachable (the
  // `nodeTypes`/`edgeTypes` keys it iterates already prove the map is real).
  const nodeCounts = (data.nodes as Record<string, unknown>) || {};
  const edgeCounts = (data.edges as Record<string, unknown>) || {};
  const nodeTypes = Object.keys(nodeCounts);
  const edgeTypes = Object.keys(edgeCounts);
  return (
    <div className="space-y-3">
      <StatGrid stats={[
        { label: t(language, "brain.stats.memories"), value: data.total_nodes ?? nodeTypes.reduce((sum, key) => sum + Number(nodeCounts[key] || 0), 0) },
        { label: t(language, "brain.stats.links"), value: data.total_edges ?? edgeTypes.reduce((sum, key) => sum + Number(edgeCounts[key] || 0), 0) },
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

// Currently unreferenced by the page — see note on GraphStatus.
export function RetrievalStatus({ data }: { data: Record<string, unknown> }) {
  const language = useAppStore((state) => state.language);
  const pipelines = isRecord(data.pipelines) ? data.pipelines : {};
  const rows = Object.entries(pipelines).map(([name, value]) => ({
    name: titleize(name),
    status: isRecord(value) ? String(value.state || value.status || t(language, "brain.value.reported")) : t(language, "brain.value.reported"),
    description: isRecord(value) ? Object.entries(value).filter(([key]) => key !== "state" && key !== "status").slice(0, 3).map(([key, item]) => `${titleize(key)}: ${String(item)}`).join(" · ") : String(value),
  }));
  return rows.length ? <EntityList items={rows} titleKey="name" metaKey="status" /> : <StructuredView value={data} />;
}

// Currently unreferenced by the page — see note on GraphStatus.
export function MemoryStatus({ data }: { data: Record<string, unknown> }) {
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
      {/* Floating control bar */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[240px]">
          <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            className="pl-9 bg-background/90"
            aria-label={t(language, "graph.search.aria")}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={mode === "basic" ? t(language, "graph.search.basic") : t(language, "graph.search.advanced")}
          />
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
        <Button variant="outline" onClick={() => setFitSignal((value) => value + 1)}>
          <LocateFixed className="h-4 w-4" /> {t(language, "graph.fit")}
        </Button>
      </div>

      {/* Main Canvas + Conditional Side Panel */}
      <div className="flex flex-col lg:flex-row gap-4 items-start">
        <div className="flex-1 w-full min-w-0 space-y-3">
          <CytoscapeGraph
            model={model}
            selectedId={selectedId}
            onSelect={setSelectedId}
            fitSignal={fitSignal}
            ariaLabel={t(language, "graph.canvas.aria")}
          />

          {/* Quiet bottom bar for stats & filters */}
          <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-muted-foreground bg-card/60 p-2.5 rounded-lg border border-border">
            <div className="flex items-center gap-3">
              <span>{t(language, "graph.deep.summary", { nodes: fmtNumber(model.visibleNodes.length), edges: fmtNumber(model.visibleEdges.length), total: fmtNumber(model.totalNodes) })}</span>
              <Badge variant={model.hiddenByFilters ? "warning" : "success"}>
                {model.hiddenByFilters ? t(language, "graph.filtered", { count: fmtNumber(model.hiddenByFilters) }) : t(language, "graph.allInView")}
              </Badge>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Filter className="h-3.5 w-3.5" />
              <label htmlFor="importance">{t(language, "graph.importance")}</label>
              <input
                id="importance"
                type="range"
                min="0"
                max="0.9"
                step="0.05"
                value={minImportance}
                onChange={(event) => setMinImportance(Number(event.target.value))}
                className="w-28"
                aria-label={t(language, "graph.minImportance.aria")}
              />
              <Badge variant="muted">{Math.round(minImportance * 100)}%+</Badge>
              {selectedId ? <Button variant="outline" size="sm" onClick={() => setSelectedId(null)}>{t(language, "graph.clearFocus")}</Button> : null}
              {search.trim() ? <Button variant="outline" size="sm" onClick={() => backendSearch.mutate()} disabled={backendSearch.isPending}>{t(language, "graph.searchAll")}</Button> : null}
            </div>
          </div>

          <div className="flex flex-wrap gap-2 pt-1">
            {model.groups.map((group) => (
              <button
                key={group.id}
                onClick={() => toggleGroup(group.id)}
                className="inline-flex items-center gap-2 rounded-md border border-border bg-background px-2.5 py-1 text-xs hover:bg-muted"
              >
                <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: group.color }} />
                <span>{group.label}</span>
                <Badge variant={group.collapsed ? "warning" : "muted"}>{group.collapsed ? t(language, "graph.collapsed") : fmtNumber(group.count)}</Badge>
              </button>
            ))}
          </div>
        </div>

        <aside className="w-full lg:w-80 space-y-3 flex-shrink-0">
          {selectedId ? (
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
                        <Badge variant="muted">{graphTypeLabel(selected.type, language)}</Badge>
                        <Badge variant="muted">{groupLabelFor(selected, model.groups)}</Badge>
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
          ) : (
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2"><Sparkles className="h-4 w-4" /> {t(language, "graph.important.title")}</CardTitle>
                <CardDescription>{t(language, "graph.important.desc")}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-2">
                {model.visibleNodes.slice(0, 8).map((node) => (
                  <button
                    key={node.id}
                    type="button"
                    onClick={() => setSelectedId(node.id)}
                    className="w-full text-left p-2 rounded-md hover:bg-muted border border-transparent hover:border-border transition-colors flex items-center justify-between gap-2"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium truncate">{node.label}</div>
                      <div className="text-xs text-muted-foreground truncate">{graphTypeLabel(node.type, language)}</div>
                    </div>
                    <Badge variant="muted">{Math.round(node.importance * 100)}%</Badge>
                  </button>
                ))}
              </CardContent>
            </Card>
          )}
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
          {/* A schema number ("2") and a storage engine ("sqlite") are true but
              unreadable. The plain view answers the two questions those fields
              exist to answer — where does this live, and can I take it away. */}
          <StatGrid stats={mode === "basic" ? [
            { label: t(language, "brain.stats.sources"), value: usage.sources ?? 0 },
            { label: t(language, "brain.stats.items"), value: usage.total_items ?? 0 },
            { label: t(language, "brain.stats.savedWhere"), value: t(language, "brain.stats.savedWhere.local") },
            {
              label: t(language, "brain.stats.exportable"),
              value: t(language, port.data?.ok ? "brain.stats.exportable.yes" : "brain.stats.exportable.unknown"),
            },
          ] : [
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

export function SourceProvenanceList({ items, limit = 8 }: { items: unknown; limit?: number }) {
  const language = useAppStore((state) => state.language);
  const rows = asArray<Record<string, unknown>>(items).slice(0, limit);
  if (!rows.length) return <EmptyState title={t(language, "brain.sources.empty")} detail={t(language, "brain.sources.emptyDetail")} />;
  return (
    <div className="grid gap-2">
      {rows.map((item, index) => {
        // The backend names memory tiers in English ("Workspace Memory"). The
        // id is the contract; the label is display, so localize by id and fall
        // back to whatever the server sent for tiers we do not know.
        const tierKey = `brain.memoryTier.${String(item.id || "")}`;
        const tierLabel = t(language, tierKey);
        const title = tierLabel !== tierKey
          ? tierLabel
          : String(item.title || item.label || item.source_title || item.filename || item.path || item.source || t(language, "brain.sources.fallback", { index: index + 1 }));
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
