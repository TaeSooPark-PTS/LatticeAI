import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import cytoscape, { Core, ElementDefinition } from "cytoscape";
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
import { useAppStore } from "@/store/appStore";
import { t, type Language } from "@/i18n";
import { asArray, fmtNumber, pct, shortId, titleize } from "@/lib/utils";

type BrainTab = "graph" | "knowledge" | "memory";
type LabelMode = "important" | "all" | "off";

type GraphNode = {
  id: string;
  label: string;
  type: string;
  group: string;
  summary: string;
  source: string;
  importance: number;
  degree: number;
  searchText: string;
  raw: Record<string, unknown>;
};

type GraphEdge = {
  id: string;
  source: string;
  target: string;
  label: string;
  weight: number;
};

type GraphGroup = {
  id: string;
  label: string;
  color: string;
  count: number;
  visibleCount: number;
  collapsed: boolean;
};

type ParsedGraph = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  groups: GraphGroup[];
};

type ExplorerModel = ParsedGraph & {
  elements: ElementDefinition[];
  visibleNodes: GraphNode[];
  visibleEdges: GraphEdge[];
  totalNodes: number;
  totalEdges: number;
  hiddenByFilters: number;
};

const tabs: Array<{ id: BrainTab; labelKey: string }> = [
  { id: "graph", labelKey: "brain.tab.graph" },
  { id: "knowledge", labelKey: "brain.tab.knowledge" },
  { id: "memory", labelKey: "brain.tab.memory" },
];

const groupDefinitions = [
  { id: "knowledge", labelKey: "brain.group.knowledge", color: "#20c997", types: ["topic", "concept", "entity", "decision", "insight", "claim", "fact"] },
  { id: "source", labelKey: "brain.group.source", color: "#60a5fa", types: ["file", "document", "source", "chunk", "note", "url", "page", "image", "transcript"] },
  { id: "activity", labelKey: "brain.group.activity", color: "#f59e0b", types: ["task", "workflow", "agent", "run", "approval", "hook"] },
  { id: "memory", labelKey: "brain.group.memory", color: "#a78bfa", types: ["memory", "conversation", "message", "chat", "context"] },
  { id: "people", labelKey: "brain.group.people", color: "#f472b6", types: ["person", "user", "team", "organization", "org"] },
  { id: "system", labelKey: "brain.group.system", color: "#94a3b8", types: ["model", "skill", "plugin", "setting", "policy", "device", "storage"] },
  { id: "other", labelKey: "brain.group.other", color: "#f8fafc", types: [] },
] as const;

const groupLookup: Map<string, string> = new Map(groupDefinitions.flatMap((group) => group.types.map((type) => [type, group.id])));

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function field(record: Record<string, unknown>, keys: string[], fallback = "") {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value;
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
  }
  return fallback;
}

function numberField(record: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = Number(record[key]);
    if (Number.isFinite(value)) return value;
  }
  return 0;
}

function nestedRecord(record: Record<string, unknown>, key: string) {
  return isRecord(record[key]) ? record[key] as Record<string, unknown> : {};
}

function groupForType(type: string) {
  return groupLookup.get(type.toLowerCase()) || "other";
}

function groupDefinition(id: string) {
  return groupDefinitions.find((group) => group.id === id) || groupDefinitions[groupDefinitions.length - 1];
}

function parseGraph(data: unknown, language: Language): ParsedGraph {
  const graph = isRecord(data) ? data : {};
  const rawNodes = asArray<Record<string, unknown>>(graph.nodes);
  const rawEdges = asArray<Record<string, unknown>>(graph.edges);
  const ids = new Set(rawNodes.map((node) => field(node, ["id", "node_id", "title", "label"])).filter(Boolean));
  const edges = rawEdges.flatMap((edge, index): GraphEdge[] => {
    const source = field(edge, ["from", "source", "source_id"]);
    const target = field(edge, ["to", "target", "target_id"]);
    if (!source || !target || !ids.has(source) || !ids.has(target)) return [];
    return [{
      id: field(edge, ["id"], `edge-${index}`),
      source,
      target,
      label: field(edge, ["type", "label", "relationship"], "related"),
      weight: Math.max(0.2, numberField(edge, ["weight", "score", "confidence"]) || 1),
    }];
  });
  const degree = new Map<string, number>();
  edges.forEach((edge) => {
    degree.set(edge.source, (degree.get(edge.source) || 0) + 1);
    degree.set(edge.target, (degree.get(edge.target) || 0) + 1);
  });
  const maxDegree = Math.max(1, ...Array.from(degree.values()));
  const nodes = rawNodes.flatMap((node): GraphNode[] => {
    const id = field(node, ["id", "node_id", "title", "label"]);
    if (!id) return [];
    const metadata = nestedRecord(node, "metadata");
    const metrics = nestedRecord(metadata, "graph_metrics");
    const type = field(node, ["type", "kind", "category"], "Node");
    const label = field(node, ["title", "label", "name"], shortId(id, 38));
    const explicitImportance = numberField(node, ["importance_norm", "importance", "score"]) || numberField(metrics, ["importance_norm", "importance", "centrality"]);
    const nodeDegree = degree.get(id) || 0;
    const importance = Math.max(0.08, Math.min(1, explicitImportance || (nodeDegree / maxDegree) * 0.8 + 0.12));
    const summary = field(node, ["summary", "description", "snippet"]) || field(metadata, ["summary", "description", "relative_path", "filename"]);
    const source = field(node, ["source", "path"]) || field(metadata, ["source", "relative_path", "filename"]);
    const searchText = [id, label, type, summary, source, Object.keys(metadata).join(" ")].join(" ").toLowerCase();
    return [{ id, label, type, group: groupForType(type), summary, source, importance, degree: nodeDegree, searchText, raw: node }];
  });
  const groupCounts = new Map<string, number>();
  nodes.forEach((node) => groupCounts.set(node.group, (groupCounts.get(node.group) || 0) + 1));
  const groups = groupDefinitions.map((group) => ({
    id: group.id,
    label: t(language, group.labelKey),
    color: group.color,
    count: groupCounts.get(group.id) || 0,
    visibleCount: 0,
    collapsed: false,
  })).filter((group) => group.count > 0);
  return { nodes, edges, groups };
}

function buildExplorerModel({
  graph,
  search,
  groupFilter,
  minImportance,
  collapsedGroups,
  selectedId,
  labelMode,
  maxNodes,
}: {
  graph: ParsedGraph;
  search: string;
  groupFilter: string;
  minImportance: number;
  collapsedGroups: Set<string>;
  selectedId: string | null;
  labelMode: LabelMode;
  maxNodes: number;
}): ExplorerModel {
  const query = search.trim().toLowerCase();
  const neighborIds = new Set<string>();
  if (selectedId && !selectedId.startsWith("group:")) {
    neighborIds.add(selectedId);
    graph.edges.forEach((edge) => {
      if (edge.source === selectedId) neighborIds.add(edge.target);
      if (edge.target === selectedId) neighborIds.add(edge.source);
    });
  }
  const filtered = graph.nodes
    .filter((node) => groupFilter === "all" || node.group === groupFilter || node.type === groupFilter)
    .filter((node) => query ? node.searchText.includes(query) : node.importance >= minImportance)
    .filter((node) => !neighborIds.size || neighborIds.has(node.id))
    .sort((a, b) => (b.importance + b.degree / 25) - (a.importance + a.degree / 25));
  const capped = filtered.slice(0, maxNodes);
  const visibleCandidateIds = new Set(capped.map((node) => node.id));
  const aggregateNodes = new Map<string, { id: string; group: GraphGroup; count: number; maxImportance: number }>();
  const visibleNodes = capped.filter((node) => {
    if (!collapsedGroups.has(node.group)) return true;
    const definition = groupDefinition(node.group);
    const group = graph.groups.find((item) => item.id === node.group);
    const aggregateId = `group:${node.group}`;
    const current = aggregateNodes.get(aggregateId);
    aggregateNodes.set(aggregateId, {
      id: aggregateId,
      group: { id: definition.id, label: group?.label || definition.id, color: definition.color, count: 0, visibleCount: 0, collapsed: true },
      count: (current?.count || 0) + 1,
      maxImportance: Math.max(current?.maxImportance || 0, node.importance),
    });
    return false;
  });
  const visibleNodeIds = new Set(visibleNodes.map((node) => node.id));
  const aggregateIds = new Set(aggregateNodes.keys());
  const visibleEdges = graph.edges.filter((edge) => visibleCandidateIds.has(edge.source) && visibleCandidateIds.has(edge.target));
  const mappedEdges = visibleEdges.flatMap((edge, index): ElementDefinition[] => {
    const sourceNode = graph.nodes.find((node) => node.id === edge.source);
    const targetNode = graph.nodes.find((node) => node.id === edge.target);
    const source = visibleNodeIds.has(edge.source) ? edge.source : sourceNode && aggregateIds.has(`group:${sourceNode.group}`) ? `group:${sourceNode.group}` : "";
    const target = visibleNodeIds.has(edge.target) ? edge.target : targetNode && aggregateIds.has(`group:${targetNode.group}`) ? `group:${targetNode.group}` : "";
    if (!source || !target || source === target) return [];
    return [{
      data: {
        id: `${edge.id}-${index}-${source}-${target}`,
        source,
        target,
        label: edge.label,
        width: Math.max(1, Math.min(4, edge.weight)),
      },
      classes: selectedId && (edge.source === selectedId || edge.target === selectedId) ? "connected" : "",
    }];
  });
  const nodeElements: ElementDefinition[] = visibleNodes.map((node) => {
    const definition = groupDefinition(node.group);
    const group = graph.groups.find((item) => item.id === node.group);
    const matched = query && node.searchText.includes(query);
    const label = labelMode === "off" ? "" : labelMode === "all" || node.importance > 0.55 || node.degree > 1 || matched || selectedId === node.id ? node.label : "";
    return {
      data: {
        id: node.id,
        label: node.label,
        displayLabel: label,
        type: node.type,
        group: group?.label || definition.id,
        color: definition.color,
        borderColor: selectedId === node.id ? "#ffffff" : definition.color,
        size: Math.round(20 + node.importance * 34 + Math.min(node.degree, 10) * 2),
      },
      classes: [
        selectedId === node.id ? "selected" : "",
        matched ? "match" : "",
        neighborIds.size && !neighborIds.has(node.id) ? "faded" : "",
      ].filter(Boolean).join(" "),
    };
  });
  const aggregateElements: ElementDefinition[] = Array.from(aggregateNodes.values()).map((aggregate) => ({
    data: {
      id: aggregate.id,
      label: aggregate.group.label,
      displayLabel: `${aggregate.group.label} (${aggregate.count})`,
      type: "Cluster",
      group: aggregate.group.label,
      color: aggregate.group.color,
      borderColor: "#ffffff",
      size: Math.round(34 + Math.min(aggregate.count, 28) * 2 + aggregate.maxImportance * 16),
    },
    classes: "cluster",
  }));
  const visibleCounts = new Map<string, number>();
  visibleNodes.forEach((node) => visibleCounts.set(node.group, (visibleCounts.get(node.group) || 0) + 1));
  const groups = graph.groups.map((group) => ({
    ...group,
    visibleCount: (visibleCounts.get(group.id) || 0) + (aggregateNodes.get(`group:${group.id}`)?.count || 0),
    collapsed: collapsedGroups.has(group.id),
  }));
  return {
    ...graph,
    groups,
    elements: [...aggregateElements, ...nodeElements, ...mappedEdges],
    visibleNodes,
    visibleEdges,
    totalNodes: graph.nodes.length,
    totalEdges: graph.edges.length,
    hiddenByFilters: Math.max(0, graph.nodes.length - capped.length),
  };
}

function CytoscapeGraph({
  model,
  selectedId,
  onSelect,
  fitSignal,
}: {
  model: ExplorerModel;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  fitSignal: number;
}) {
  const hostRef = React.useRef<HTMLDivElement | null>(null);
  const cyRef = React.useRef<Core | null>(null);
  React.useEffect(() => {
    if (!hostRef.current) return;
    cyRef.current?.destroy();
    cyRef.current = cytoscape({
      container: hostRef.current,
      elements: model.elements,
      style: [
        {
          selector: "node",
          style: {
            "background-color": "data(color)",
            "border-color": "data(borderColor)",
            "border-width": 1.5,
            color: "#f8fafc",
            label: "data(displayLabel)",
            "font-size": 10,
            "font-weight": 600,
            "text-outline-color": "#071012",
            "text-outline-width": 2.5,
            width: "data(size)",
            height: "data(size)",
            "overlay-opacity": 0,
          },
        },
        {
          selector: "node.cluster",
          style: {
            shape: "round-rectangle",
            "background-opacity": 0.46,
            "border-width": 2,
            "font-size": 12,
            "text-valign": "center",
            "text-halign": "center",
          },
        },
        {
          selector: "node.selected",
          style: {
            "border-width": 4,
          },
        },
        {
          selector: "node.match",
          style: {
            "border-width": 4,
            "border-color": "#fef08a",
          },
        },
        {
          selector: "edge",
          style: {
            width: "data(width)",
            "line-color": "#64748b",
            "target-arrow-shape": "triangle",
            "target-arrow-color": "#64748b",
            "curve-style": "bezier",
            "arrow-scale": 0.7,
            opacity: 0.72,
          },
        },
        {
          selector: "edge.connected",
          style: {
            width: 2.6,
            "line-color": "#fef08a",
            "target-arrow-color": "#fef08a",
            opacity: 1,
          },
        },
      ],
      layout: {
        name: "cose",
        animate: false,
        idealEdgeLength: 120,
        nodeRepulsion: 5800,
        gravity: 0.28,
        numIter: 1200,
        fit: true,
        padding: 32,
      },
    });
    cyRef.current.on("tap", "node", (event) => onSelect(String(event.target.id())));
    cyRef.current.on("tap", (event) => {
      if (event.target === cyRef.current) onSelect(null);
    });
    return () => cyRef.current?.destroy();
  }, [model.elements, onSelect]);

  React.useEffect(() => {
    cyRef.current?.fit(undefined, 32);
  }, [fitSignal]);

  React.useEffect(() => {
    if (!cyRef.current || !selectedId) return;
    const node = cyRef.current.getElementById(selectedId);
    if (node.length) {
      cyRef.current.animate({ center: { eles: node }, zoom: Math.max(cyRef.current.zoom(), 1.15) }, { duration: 180 });
    }
  }, [selectedId]);

  return (
    <div
      ref={hostRef}
      data-testid="brain-cytoscape"
      className="brain-grid h-[620px] min-h-[32rem] w-full overflow-hidden rounded-lg border border-border bg-background/80"
    />
  );
}

export function BrainPage({ initialTab }: { initialTab?: string }) {
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  const normalizedInitialTab = normalizeBrainTab(initialTab);
  const [tab, setTab] = React.useState<BrainTab>(normalizedInitialTab);
  React.useEffect(() => {
    setTab(normalizeBrainTab(initialTab));
  }, [initialTab]);
  const graph = useQuery({ queryKey: ["graph"], queryFn: latticeApi.graph });
  const coverage = useQuery({ queryKey: ["coverage"], queryFn: latticeApi.graphCoverage });

  return (
    <div className="space-y-5">
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
      <Tabs tabs={tabs.map((item) => ({ id: item.id, label: t(language, item.labelKey) }))} value={tab} onChange={(id) => setTab(id as BrainTab)} />

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
  return labelKey ? t(language, labelKey) : "Brain";
}

function tabHeadline(language: Language, tab: BrainTab) {
  return t(language, `brain.headline.${tab}`);
}

function GraphStatus({ data }: { data: Record<string, unknown> }) {
  const mode = useAppStore((state) => state.mode);
  const nodeTypes = Object.keys((data.nodes as Record<string, unknown>) || {});
  const edgeTypes = Object.keys((data.edges as Record<string, unknown>) || {});
  return (
    <div className="space-y-3">
      <StatGrid stats={[
        { label: "Memories", value: data.total_nodes ?? nodeTypes.reduce((sum, key) => sum + Number(((data.nodes as Record<string, unknown>) || {})[key] || 0), 0) },
        { label: "Links", value: data.total_edges ?? edgeTypes.reduce((sum, key) => sum + Number(((data.edges as Record<string, unknown>) || {})[key] || 0), 0) },
        { label: "Memory kinds", value: nodeTypes.length },
        { label: "Link kinds", value: edgeTypes.length },
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
  const pipelines = isRecord(data.pipelines) ? data.pipelines : {};
  const rows = Object.entries(pipelines).map(([name, value]) => ({
    name: titleize(name),
    status: isRecord(value) ? String(value.state || value.status || "reported") : "reported",
    description: isRecord(value) ? Object.entries(value).filter(([key]) => key !== "state" && key !== "status").slice(0, 3).map(([key, item]) => `${titleize(key)}: ${String(item)}`).join(" · ") : String(value),
  }));
  return rows.length ? <EntityList items={rows} titleKey="name" metaKey="status" /> : <StructuredView value={data} />;
}

function MemoryStatus({ data }: { data: Record<string, unknown> }) {
  const usage = isRecord(data.usage) ? data.usage : {};
  const sources = asArray<Record<string, unknown>>(data.sources || data.tiers);
  return (
    <div className="space-y-3">
      <StatGrid stats={[
        { label: "Sources", value: usage.sources ?? asArray(data.sources).length },
        { label: "Items", value: usage.total_items ?? asArray(data.sources).reduce((sum, item) => sum + Number(isRecord(item) ? item.count || 0 : 0), 0) },
        { label: "Bytes", value: usage.total_bytes ?? 0 },
        { label: "Health", value: data.health || "reported" },
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
            <CytoscapeGraph model={model} selectedId={selectedId} onSelect={setSelectedId} fitSignal={fitSignal} />
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
                      source_type: sourceType(selected.raw),
                      created_at: sourceCreatedAt(selected.raw) || t(language, "graph.created.none"),
                    }} />
                  ) : (
                    <StructuredView value={{
                      id: selected.id,
                      degree: selected.degree,
                      source: selected.source || t(language, "graph.source.none"),
                      source_type: sourceType(selected.raw),
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
                  <div className="mt-1 text-xs text-muted-foreground">{Math.round(node.importance * 100)} importance · {fmtNumber(node.degree)} links</div>
                </button>
              ))}
            </CardContent>
          </Card>
        </aside>
      </div>
      {backendSearch.data ? (
        <DataPanel title="Brain search results" result={backendSearch.data}>
          {(result) => <EntityList items={(result as Record<string, unknown>).matches || result} titleKey="title" metaKey="type" limit={8} />}
        </DataPanel>
      ) : null}
    </div>
  );
}

function HybridSearch() {
  const [query, setQuery] = React.useState("");
  const search = useMutation({ mutationFn: () => latticeApi.hybridSearch(query) });
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Search className="h-4 w-4" /> Brain search</CardTitle>
        <CardDescription>Find ideas across memories, documents, and connections.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input placeholder="Search memories, indexed documents, and relationships" value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => e.key === "Enter" && search.mutate()} />
          <Button onClick={() => search.mutate()} disabled={!query.trim() || search.isPending}>Search</Button>
        </div>
        {search.data ? (
          <DataPanel title="Results" result={search.data}>
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
            { label: "Sources", value: usage.sources ?? 0 },
            { label: "Items", value: usage.total_items ?? 0 },
            { label: "Brain format", value: portData.graph_schema_version || portData.schema_version || "–" },
            { label: "Storage", value: portStorage.engine || "–" },
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
            Memory Recall
            <span className="ml-auto text-xs text-muted-foreground">{expandedSection === "recall" ? "▲" : "▼"}</span>
          </CardTitle>
          <CardDescription>Search and manage your saved memories.</CardDescription>
        </CardHeader>
        {expandedSection === "recall" && (
          <CardContent className="space-y-3">
            <div className="flex flex-col gap-2 sm:flex-row">
              <Input
                value={recallQuery}
                onChange={(e) => setRecallQuery(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && recallQuery.trim() && recall.mutate()}
                placeholder="Recall memories about..."
              />
              <Button disabled={!recallQuery.trim() || recall.isPending} onClick={() => recall.mutate()}>Recall</Button>
            </div>
            {mode !== "basic" && (
              <div className="flex flex-wrap gap-2">
                <ActionButton label="Compact" action={() => latticeApi.memoryCompact()} />
                <ActionButton label="Rebuild vector" action={() => latticeApi.memoryRebuild()} />
              </div>
            )}
            {recall.data ? <OperationResult result={recall.data} successLabel="Recall completed" /> : null}
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
            Sources & Provenance
            <span className="ml-auto text-xs text-muted-foreground">{expandedSection === "sources" ? "▲" : "▼"}</span>
          </CardTitle>
          <CardDescription>Where your memories came from.</CardDescription>
        </CardHeader>
        {expandedSection === "sources" && (
          <CardContent className="space-y-3">
            {provenance.isLoading ? (
              <LoadingPanel title="Loading sources" />
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
            Export & Backup
            <span className="ml-auto text-xs text-muted-foreground">{expandedSection === "backup" ? "▲" : "▼"}</span>
          </CardTitle>
          <CardDescription>Export, back up, or import your Brain data.</CardDescription>
        </CardHeader>
        {expandedSection === "backup" && (
          <CardContent className="space-y-3">
            <div className="flex flex-wrap gap-2">
              <ActionButton label="Export Brain" action={() => latticeApi.graphExport()} />
              <ActionButton label="Create backup" action={() => latticeApi.graphBackup()} />
            </div>
            <Textarea
              value={importArtifact}
              onChange={(e) => setImportArtifact(e.target.value)}
              placeholder="Paste an exported Brain artifact to preview import"
            />
            <Button
              variant="outline"
              disabled={!importArtifact.trim() || importMutation.isPending}
              onClick={() => importMutation.mutate()}
            >
              Preview import
            </Button>
            {importMutation.data ? <OperationResult result={importMutation.data} successLabel="Import preview completed" /> : null}
          </CardContent>
        )}
      </Card>
    </div>
  );
}

function SourceProvenanceList({ items, limit = 8 }: { items: unknown; limit?: number }) {
  const rows = asArray<Record<string, unknown>>(items).slice(0, limit);
  if (!rows.length) return <EmptyState title="No sources yet" detail="New memories will show their chat, manual, document, or import origin here." />;
  return (
    <div className="grid gap-2">
      {rows.map((item, index) => {
        const title = String(item.title || item.label || item.source_title || item.filename || item.path || item.source || `Source ${index + 1}`);
        const path = String(item.path || item.source_path || item.source || item.conversation_id || "");
        const type = sourceType(item);
        const created = sourceCreatedAt(item);
        return (
          <div key={String(item.id || item.source_id || title)} className="rounded-lg border border-border bg-background/55 p-3">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <div className="font-medium">{title}</div>
                <div className="mt-1 text-xs text-muted-foreground">
                  {type} · {created || "Created time not recorded"}
                </div>
              </div>
              <Badge variant="muted">{path ? "inspectable" : "missing provenance"}</Badge>
            </div>
            {path ? (
              <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <span className="break-all">{path}</span>
                <Button variant="outline" size="sm" onClick={() => void navigator.clipboard?.writeText(path)}>Copy source</Button>
              </div>
            ) : (
              <p className="mt-2 text-xs text-muted-foreground">This older memory did not record a source path or conversation. It remains searchable, but provenance is incomplete.</p>
            )}
          </div>
        );
      })}
    </div>
  );
}

function sourceType(item: Record<string, unknown>) {
  const metadata = isRecord(item.metadata) ? item.metadata : {};
  const raw = String(item.source_type || item.type || item.kind || metadata.source_type || metadata.role || "").toLowerCase();
  if (/chat|conversation|message/.test(raw)) return "chat";
  if (/document|upload|file|pdf|markdown|text/.test(raw)) return "document";
  if (/import|archive|restore/.test(raw)) return "import";
  if (/manual|note/.test(raw)) return "manual";
  return "source unknown";
}

function sourceCreatedAt(item: Record<string, unknown>) {
  const metadata = isRecord(item.metadata) ? item.metadata : {};
  const value = item.created_at || item.timestamp || item.updated_at || metadata.created_at || metadata.timestamp;
  return value ? String(value) : "";
}
