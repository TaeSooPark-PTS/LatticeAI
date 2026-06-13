import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import cytoscape, { Core, ElementDefinition } from "cytoscape";
import { BrainCircuit, DatabaseBackup, Filter, Focus, Layers3, LocateFixed, Search, Sparkles } from "lucide-react";
import { latticeApi } from "@/api/client";
import { ActionButton, DataPanel, EmptyState, EntityList, KeyValueList, LoadingPanel, OperationResult, StatGrid, StructuredView, Tabs } from "@/components/primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useAppStore } from "@/store/appStore";
import { asArray, fmtNumber, pct, shortId, titleize } from "@/lib/utils";

type BrainTab = "overview" | "graph" | "search" | "memory" | "provenance" | "portability";
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

const tabs: Array<{ id: BrainTab; label: string }> = [
  { id: "overview", label: "Overview" },
  { id: "graph", label: "Graph" },
  { id: "search", label: "Search" },
  { id: "memory", label: "Memory" },
  { id: "provenance", label: "Provenance" },
  { id: "portability", label: "Portability" },
];

const groupDefinitions = [
  { id: "knowledge", label: "Knowledge", color: "#20c997", types: ["topic", "concept", "entity", "decision", "insight", "claim", "fact"] },
  { id: "source", label: "Sources", color: "#60a5fa", types: ["file", "document", "source", "chunk", "note", "url", "page", "image", "transcript"] },
  { id: "activity", label: "Activity", color: "#f59e0b", types: ["task", "workflow", "agent", "run", "approval", "hook"] },
  { id: "memory", label: "Memory", color: "#a78bfa", types: ["memory", "conversation", "message", "chat", "context"] },
  { id: "people", label: "People", color: "#f472b6", types: ["person", "user", "team", "organization", "org"] },
  { id: "system", label: "System", color: "#94a3b8", types: ["model", "skill", "plugin", "setting", "policy", "device", "storage"] },
  { id: "other", label: "Other", color: "#f8fafc", types: [] },
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

function parseGraph(data: unknown): ParsedGraph {
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
    label: group.label,
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
    const aggregateId = `group:${node.group}`;
    const current = aggregateNodes.get(aggregateId);
    aggregateNodes.set(aggregateId, {
      id: aggregateId,
      group: { id: definition.id, label: definition.label, color: definition.color, count: 0, visibleCount: 0, collapsed: true },
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
    const matched = query && node.searchText.includes(query);
    const label = labelMode === "off" ? "" : labelMode === "all" || node.importance > 0.55 || node.degree > 1 || matched || selectedId === node.id ? node.label : "";
    return {
      data: {
        id: node.id,
        label: node.label,
        displayLabel: label,
        type: node.type,
        group: definition.label,
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
      wheelSensitivity: 0.18,
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
      className="h-[620px] min-h-[32rem] w-full overflow-hidden rounded-md border border-border bg-background brain-grid"
    />
  );
}

export function BrainPage({ initialTab }: { initialTab?: string }) {
  const mode = useAppStore((state) => state.mode);
  const [tab, setTab] = React.useState<BrainTab>((initialTab as BrainTab) || "graph");
  React.useEffect(() => {
    if (initialTab && tabs.some((item) => item.id === initialTab)) setTab(initialTab as BrainTab);
  }, [initialTab]);
  const graph = useQuery({ queryKey: ["graph"], queryFn: latticeApi.graph });
  const stats = useQuery({ queryKey: ["graphStats"], queryFn: latticeApi.graphStats });
  const index = useQuery({ queryKey: ["index"], queryFn: latticeApi.indexStatus });
  const coverage = useQuery({ queryKey: ["coverage"], queryFn: latticeApi.graphCoverage });
  const provenance = useQuery({ queryKey: ["provenance"], queryFn: () => latticeApi.graphProvenance(50) });
  const memory = useQuery({ queryKey: ["memoryManager"], queryFn: latticeApi.memoryManager });

  return (
    <div className="space-y-4">
      <header className="grid gap-4 xl:grid-cols-[1.4fr_0.6fr]">
        <div>
          <div className="flex items-center gap-2 text-sm text-primary"><BrainCircuit className="h-4 w-4" /> Graph-first Digital Brain</div>
          <h1 className="mt-2 text-3xl font-semibold tracking-normal">Brain</h1>
          <p className="mt-2 max-w-3xl text-sm text-muted-foreground">
            Explore what Lattice remembers, where it came from, and how ideas connect across your workspace.
          </p>
        </div>
        <div className="rounded-md border border-border bg-card p-4">
          <div className="text-xs uppercase text-muted-foreground">Provenance coverage</div>
          <div className="mt-2 text-3xl font-semibold">{pct((coverage.data?.data as Record<string, unknown>)?.coverage_ratio)}</div>
          <div className="mt-2 text-sm text-muted-foreground">Source: {coverage.data?.source || "loading"}</div>
        </div>
      </header>
      <Tabs tabs={tabs} value={tab} onChange={(id) => setTab(id as BrainTab)} />

      {tab === "overview" ? (
        <div className="grid gap-4 xl:grid-cols-2">
          <DataPanel title="Brain status" result={stats.data}>
            {(data) => <GraphStatus data={data as Record<string, unknown>} />}
          </DataPanel>
          <DataPanel title="Retrieval index" result={index.data}>
            {(data) => <RetrievalStatus data={data as Record<string, unknown>} />}
          </DataPanel>
          <DataPanel title="Memory tiers" result={memory.data}>
            {(data) => <MemoryStatus data={data as Record<string, unknown>} />}
          </DataPanel>
          <DataPanel title="Recent provenance" result={provenance.data}>
            {(data) => <EntityList items={(data as Record<string, unknown>).items || data} titleKey="source" metaKey="source_type" />}
          </DataPanel>
        </div>
      ) : null}

      {tab === "graph" ? (
        graph.isLoading ? <LoadingPanel title="Knowledge graph" /> : (
          <DataPanel title="Digital Brain explorer" description={mode === "basic" ? "Search, focus, and filter the ideas Lattice has learned from your workspace." : "Interactive graph explorer with source-backed relationships and advanced inspection."} result={graph.data}>
            {(data) => <DigitalBrainExplorer data={data} />}
          </DataPanel>
        )
      ) : null}

      {tab === "search" ? <HybridSearch /> : null}
      {tab === "memory" ? <MemoryPanel /> : null}
      {tab === "provenance" ? <ProvenancePanel /> : null}
      {tab === "portability" ? <PortabilityPanel /> : null}
    </div>
  );
}

function GraphStatus({ data }: { data: Record<string, unknown> }) {
  const mode = useAppStore((state) => state.mode);
  const nodeTypes = Object.keys((data.nodes as Record<string, unknown>) || {});
  const edgeTypes = Object.keys((data.edges as Record<string, unknown>) || {});
  return (
    <div className="space-y-3">
      <StatGrid stats={[
        { label: "Nodes", value: data.total_nodes ?? nodeTypes.reduce((sum, key) => sum + Number(((data.nodes as Record<string, unknown>) || {})[key] || 0), 0) },
        { label: "Edges", value: data.total_edges ?? edgeTypes.reduce((sum, key) => sum + Number(((data.edges as Record<string, unknown>) || {})[key] || 0), 0) },
        { label: "Node types", value: nodeTypes.length },
        { label: "Edge types", value: edgeTypes.length },
      ]} />
      {mode === "basic" ? (
        <div className="flex flex-wrap gap-1">
          {[...nodeTypes, ...edgeTypes].slice(0, 10).map((item) => <Badge key={item} variant="muted">{titleize(item)}</Badge>)}
        </div>
      ) : <StructuredView value={{ node_types: nodeTypes, edge_types: edgeTypes }} />}
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
  return (
    <div className="space-y-3">
      <StatGrid stats={[
        { label: "Sources", value: usage.sources ?? asArray(data.sources).length },
        { label: "Items", value: usage.total_items ?? asArray(data.sources).reduce((sum, item) => sum + Number(isRecord(item) ? item.count || 0 : 0), 0) },
        { label: "Bytes", value: usage.total_bytes ?? 0 },
        { label: "Health", value: data.health || "reported" },
      ]} />
      <EntityList items={data.sources || data.tiers} titleKey="label" metaKey="health" />
    </div>
  );
}

function DigitalBrainExplorer({ data }: { data: unknown }) {
  const mode = useAppStore((state) => state.mode);
  const parsed = React.useMemo(() => parseGraph(data), [data]);
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
  const selectedGroup = selectedId?.startsWith("group:") ? groupDefinition(selectedId.replace("group:", "")) : null;
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
        title="No graph records yet"
        detail="Capture a document, note, or local folder to create graph nodes with provenance."
      />
    );
  }
  return (
    <div className="space-y-4">
      <div className="grid gap-3 xl:grid-cols-[1fr_220px_180px_170px]">
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input className="pl-9" value={search} onChange={(event) => setSearch(event.target.value)} placeholder={mode === "basic" ? "Search ideas, files, people, and notes..." : "Search graph labels, types, provenance..."} />
        </div>
        <select className="h-9 rounded-md border border-border bg-background px-3 text-sm" value={groupFilter} onChange={(event) => setGroupFilter(event.target.value)}>
          <option value="all">All semantic groups</option>
          {model.groups.map((group) => <option key={group.id} value={group.id}>{group.label}</option>)}
        </select>
        <select className="h-9 rounded-md border border-border bg-background px-3 text-sm" value={labelMode} onChange={(event) => setLabelMode(event.target.value as LabelMode)}>
          <option value="important">Important labels</option>
          <option value="all">All labels</option>
          <option value="off">Hide labels</option>
        </select>
        <Button variant="outline" onClick={() => setFitSignal((value) => value + 1)}><LocateFixed className="h-4 w-4" /> Fit</Button>
      </div>
      <div className="grid gap-3 lg:grid-cols-[1fr_18rem]">
        <Card>
          <CardHeader className="flex-row items-start justify-between gap-3">
            <div>
              <CardTitle className="flex items-center gap-2"><Layers3 className="h-4 w-4" /> Semantic map</CardTitle>
              <CardDescription>
                Showing {fmtNumber(model.visibleNodes.length)} ideas and {fmtNumber(model.visibleEdges.length)} relationships from {fmtNumber(model.totalNodes)} saved items.
              </CardDescription>
            </div>
            <Badge variant={model.hiddenByFilters ? "warning" : "success"}>{model.hiddenByFilters ? `${fmtNumber(model.hiddenByFilters)} filtered` : "all in view"}</Badge>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <Filter className="h-4 w-4 text-muted-foreground" />
              <label className="text-sm text-muted-foreground" htmlFor="importance">Importance</label>
              <input
                id="importance"
                type="range"
                min="0"
                max="0.9"
                step="0.05"
                value={minImportance}
                onChange={(event) => setMinImportance(Number(event.target.value))}
                className="w-44"
                aria-label="Minimum graph importance"
              />
              <Badge variant="muted">{Math.round(minImportance * 100)}%+</Badge>
              {selectedId ? <Button variant="outline" size="sm" onClick={() => setSelectedId(null)}>Clear focus</Button> : null}
              {search.trim() ? <Button variant="outline" size="sm" onClick={() => backendSearch.mutate()} disabled={backendSearch.isPending}>Search brain</Button> : null}
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
                  <Badge variant={group.collapsed ? "warning" : "muted"}>{group.collapsed ? "collapsed" : fmtNumber(group.count)}</Badge>
                </button>
              ))}
            </div>
            <CytoscapeGraph model={model} selectedId={selectedId} onSelect={setSelectedId} fitSignal={fitSignal} />
          </CardContent>
        </Card>
        <aside className="space-y-3">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2"><Focus className="h-4 w-4" /> Focus</CardTitle>
              <CardDescription>Click a node to inspect its neighborhood.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {selected ? (
                <>
                  <div>
                    <div className="text-lg font-semibold">{selected.label}</div>
                    <div className="mt-1 flex flex-wrap gap-1">
                      <Badge variant="muted">{selected.type}</Badge>
                      <Badge variant="muted">{groupDefinition(selected.group).label}</Badge>
                      <Badge variant="success">{Math.round(selected.importance * 100)} importance</Badge>
                    </div>
                  </div>
                  {selected.summary ? <p className="text-sm text-muted-foreground">{selected.summary}</p> : null}
                  {mode === "basic" ? (
                    <KeyValueList data={{
                      connections: selected.degree,
                      source: selected.source || "not reported",
                    }} />
                  ) : (
                    <StructuredView value={{
                      id: selected.id,
                      degree: selected.degree,
                      source: selected.source || "not reported",
                    }} />
                  )}
                </>
              ) : selectedGroup ? (
                <div className="space-y-2">
                  <Badge variant="warning">Collapsed group</Badge>
                  <div className="text-lg font-semibold">{selectedGroup.label}</div>
                  <Button variant="outline" onClick={() => toggleGroup(selectedGroup.id)}>Expand group</Button>
                </div>
              ) : <EmptyState title="No node selected" detail="Select a node or collapsed group in the graph." />}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Important nodes</CardTitle>
              <CardDescription>Highest-ranked visible graph records.</CardDescription>
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
        <CardDescription>Searches memories, graph connections, and indexed documents together.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input placeholder="Search memories, graph nodes, and indexed documents" value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => e.key === "Enter" && search.mutate()} />
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

function MemoryPanel() {
  const [query, setQuery] = React.useState("");
  const manager = useQuery({ queryKey: ["memoryManager"], queryFn: latticeApi.memoryManager });
  const recall = useMutation({ mutationFn: () => latticeApi.memoryRecall(query, 25) });
  return (
    <div className="grid gap-4 xl:grid-cols-[0.9fr_1.1fr]">
      <DataPanel title="Memory manager" result={manager.data}>
        {(data) => <MemoryStatus data={data as Record<string, unknown>} />}
      </DataPanel>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Sparkles className="h-4 w-4" /> Recall</CardTitle>
          <CardDescription>Searches the real memory recall endpoint.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Recall memories about..." />
          <div className="flex flex-wrap gap-2">
            <Button disabled={!query.trim() || recall.isPending} onClick={() => recall.mutate()}>Recall</Button>
            <ActionButton label="Compact" action={() => latticeApi.memoryCompact()} />
            <ActionButton label="Rebuild vector" action={() => latticeApi.memoryRebuild()} />
          </div>
          {recall.data ? <OperationResult result={recall.data} successLabel="Recall completed" /> : null}
        </CardContent>
      </Card>
    </div>
  );
}

function ProvenancePanel() {
  const provenance = useQuery({ queryKey: ["provenance"], queryFn: () => latticeApi.graphProvenance(80) });
  const coverage = useQuery({ queryKey: ["coverage"], queryFn: latticeApi.graphCoverage });
  return (
    <div className="grid gap-4 xl:grid-cols-[0.8fr_1.2fr]">
      <DataPanel title="Coverage" result={coverage.data}>
        {(data) => <StructuredView value={data} />}
      </DataPanel>
      <DataPanel title="Recent ingestion provenance" result={provenance.data}>
        {(data) => <EntityList items={(data as Record<string, unknown>).items || data} titleKey="source" metaKey="source_type" limit={14} />}
      </DataPanel>
    </div>
  );
}

function PortabilityPanel() {
  const qc = useQueryClient();
  const [artifact, setArtifact] = React.useState("");
  const port = useQuery({ queryKey: ["portability"], queryFn: latticeApi.graphPortability });
  const importMutation = useMutation({
    mutationFn: async () => {
      try {
        return await latticeApi.graphImport(JSON.parse(artifact), true);
      } catch (err) {
        return { ok: false, status: 0, data: {}, source: "unavailable" as const, error: err instanceof Error ? err.message : String(err) };
      }
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["portability"] }),
  });
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <DataPanel title="Portability status" result={port.data}>
        {(data) => <PortabilityStatus data={data as Record<string, unknown>} />}
      </DataPanel>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><DatabaseBackup className="h-4 w-4" /> Export, backup, import</CardTitle>
          <CardDescription>Every control calls a real portability endpoint. Import is dry-run by default from a pasted export artifact.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap gap-2">
            <ActionButton label="Export graph artifact" action={() => latticeApi.graphExport()} />
            <ActionButton label="Create backup" action={() => latticeApi.graphBackup()} />
          </div>
          <Textarea value={artifact} onChange={(e) => setArtifact(e.target.value)} placeholder="Paste an exported graph artifact for dry-run import" />
          <Button
            variant="outline"
            disabled={!artifact.trim() || importMutation.isPending}
            onClick={() => importMutation.mutate()}
          >
            Dry-run import
          </Button>
          {importMutation.data ? <OperationResult result={importMutation.data} successLabel="Dry run completed" /> : null}
        </CardContent>
      </Card>
    </div>
  );
}

function PortabilityStatus({ data }: { data: Record<string, unknown> }) {
  const stats = isRecord(data.stats) ? data.stats : {};
  const storage = isRecord(data.storage) ? data.storage : {};
  return (
    <div className="space-y-3">
      <StatGrid stats={[
        { label: "Schema", value: data.graph_schema_version || data.schema_version || "reported" },
        { label: "Nodes", value: (stats.total_nodes as number) || Object.values((stats.nodes as Record<string, unknown>) || {}).reduce((sum: number, value) => sum + Number(value || 0), 0) },
        { label: "Edges", value: (stats.total_edges as number) || Object.values((stats.edges as Record<string, unknown>) || {}).reduce((sum: number, value) => sum + Number(value || 0), 0) },
        { label: "Storage", value: storage.engine || "reported" },
      ]} />
      <StructuredView value={data} />
    </div>
  );
}
