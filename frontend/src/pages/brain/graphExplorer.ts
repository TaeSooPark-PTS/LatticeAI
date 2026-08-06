import type { ElementDefinition } from "cytoscape";
import { t, type Language } from "@/i18n";
import { asArray, isRecord as isRecordValue, plainText, shortId } from "@/lib/utils";

export type LabelMode = "important" | "all" | "off";

export type GraphNode = {
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

export type GraphEdge = {
  id: string;
  source: string;
  target: string;
  label: string;
  weight: number;
};

export type GraphGroup = {
  id: string;
  label: string;
  color: string;
  count: number;
  visibleCount: number;
  collapsed: boolean;
};

export type ParsedGraph = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  groups: GraphGroup[];
};

export type ExplorerModel = ParsedGraph & {
  elements: ElementDefinition[];
  visibleNodes: GraphNode[];
  visibleEdges: GraphEdge[];
  totalNodes: number;
  totalEdges: number;
  hiddenByFilters: number;
};

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

export const isRecord = isRecordValue;

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

export function parseGraph(data: unknown, language: Language): ParsedGraph {
  let graph = isRecord(data) ? data : {};
  if (isRecord(graph.data) && (Array.isArray(graph.data.nodes) || isRecord(graph.data.nodes))) {
    graph = graph.data as Record<string, unknown>;
  }
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
    // Node titles come from ingested text, so a Markdown heading a model wrote
    // showed up on the map as "**Enterprise IT 인프라 관점**".
    const label = plainText(field(node, ["title", "label", "name"], "")) || shortId(id, 38);
    const explicitImportance = numberField(node, ["importance_norm", "importance", "score"]) || numberField(metrics, ["importance_norm", "importance", "centrality"]);
    const nodeDegree = degree.get(id) || 0;
    const importance = Math.max(0.08, Math.min(1, explicitImportance || (nodeDegree / maxDegree) * 0.8 + 0.12));
    const summary = plainText(field(node, ["summary", "description", "snippet"]))
      || plainText(field(metadata, ["summary", "description", "relative_path", "filename"]));
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

export function buildExplorerModel({
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
      // No "faded" class here: when a node is selected, the neighbour filter
      // above already removes every non-neighbour, so a visible node can never
      // be outside `neighborIds` — the class could never apply (and nothing
      // styles it).
      classes: [
        selectedId === node.id ? "selected" : "",
        matched ? "match" : "",
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
