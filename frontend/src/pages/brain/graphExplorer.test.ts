/**
 * The knowledge-graph explorer is the one screen that renders the Brain's own
 * shape, and all of its logic lives in these two pure functions. It was at 4%
 * coverage — the map could quietly start dropping edges, mis-grouping node
 * types, or showing raw Markdown as labels, and nothing would fail.
 *
 * `parseGraph` normalises whatever the graph API returned (the field names vary
 * by node source: `from`/`source`, `title`/`label`/`name`, …).
 * `buildExplorerModel` turns that into what Cytoscape draws, applying search,
 * group filters, an importance floor, collapsed clusters and a node cap.
 */

import { describe, expect, it } from "vitest";

import { buildExplorerModel, graphGroupColor, graphInkColor, parseGraph, resolveTokenColor, type GraphEdge, type GraphNode, type ParsedGraph } from "./graphExplorer";

const NO_COLLAPSE = new Set<string>();

function model(graph: ParsedGraph, overrides: Partial<Parameters<typeof buildExplorerModel>[0]> = {}) {
  return buildExplorerModel({
    graph,
    search: "",
    groupFilter: "all",
    minImportance: 0,
    collapsedGroups: NO_COLLAPSE,
    selectedId: null,
    labelMode: "important",
    maxNodes: 100,
    ...overrides,
  });
}

describe("parseGraph", () => {
  it("returns an empty graph for anything that is not a graph payload", () => {
    for (const input of [null, undefined, "graph", 42, []]) {
      const parsed = parseGraph(input, "en");
      expect(parsed.nodes).toEqual([]);
      expect(parsed.edges).toEqual([]);
      expect(parsed.groups).toEqual([]);
    }
  });

  it("accepts either naming convention for edge endpoints", () => {
    const parsed = parseGraph(
      {
        nodes: [{ id: "a" }, { id: "b" }, { id: "c" }],
        edges: [
          { from: "a", to: "b" },
          { source: "b", target: "c" },
          { source_id: "a", target_id: "c" },
        ],
      },
      "en",
    );
    expect(parsed.edges).toHaveLength(3);
  });

  it("drops an edge whose endpoint is not in the node set", () => {
    const parsed = parseGraph(
      { nodes: [{ id: "a" }], edges: [{ from: "a", to: "ghost" }, { from: "a", to: "a" }] },
      "en",
    );
    expect(parsed.edges.map((edge) => edge.target)).toEqual(["a"]);
  });

  it("names an unlabelled edge 'related' rather than leaving it blank", () => {
    const parsed = parseGraph(
      { nodes: [{ id: "a" }, { id: "b" }], edges: [{ from: "a", to: "b" }] },
      "en",
    );
    expect(parsed.edges[0].label).toBe("related");
    expect(parsed.edges[0].id).toBe("edge-0");
  });

  it("keeps every edge weight positive so nothing renders invisible", () => {
    const parsed = parseGraph(
      {
        nodes: [{ id: "a" }, { id: "b" }],
        edges: [
          { from: "a", to: "b", weight: 0 },
          { from: "a", to: "b", weight: -3 },
          { from: "a", to: "b", weight: 2.5 },
        ],
      },
      "en",
    );
    for (const edge of parsed.edges) expect(edge.weight).toBeGreaterThan(0);
    expect(parsed.edges[2].weight).toBe(2.5);
  });

  it("strips Markdown out of a node title", () => {
    const parsed = parseGraph({ nodes: [{ id: "n1", title: "**Bold heading**" }] }, "en");
    expect(parsed.nodes[0].label).not.toContain("**");
    expect(parsed.nodes[0].label).toContain("Bold heading");
  });

  it("falls back to a shortened id when a node has no title at all", () => {
    const longId = "concept:".padEnd(80, "x");
    const parsed = parseGraph({ nodes: [{ id: longId }] }, "en");
    const { label } = parsed.nodes[0];
    expect(label).not.toBe("");
    // Shortened for the canvas, but still recognisably the same node.
    expect(label.length).toBeLessThan(longId.length);
    expect(longId.startsWith(label.slice(0, 8))).toBe(true);
  });

  it("skips a node with no usable identity", () => {
    const parsed = parseGraph({ nodes: [{ summary: "orphan" }, { id: "real" }] }, "en");
    expect(parsed.nodes.map((node) => node.id)).toEqual(["real"]);
  });

  it.each([
    ["Topic", "knowledge"],
    ["concept", "knowledge"],
    ["File", "source"],
    ["chunk", "source"],
    ["Workflow", "activity"],
    ["Memory", "memory"],
    ["Person", "people"],
    ["Model", "system"],
    ["SomethingNew", "other"],
  ])("puts a %s node in the %s group", (type, group) => {
    const parsed = parseGraph({ nodes: [{ id: "n", type }] }, "en");
    expect(parsed.nodes[0].group).toBe(group);
  });

  it("only reports groups that actually have nodes", () => {
    const parsed = parseGraph(
      { nodes: [{ id: "a", type: "Topic" }, { id: "b", type: "Topic" }] },
      "en",
    );
    expect(parsed.groups).toHaveLength(1);
    expect(parsed.groups[0]).toMatchObject({ id: "knowledge", count: 2 });
  });

  it("counts degree from the edges rather than trusting the payload", () => {
    const parsed = parseGraph(
      {
        nodes: [{ id: "hub" }, { id: "a" }, { id: "b" }],
        edges: [{ from: "hub", to: "a" }, { from: "hub", to: "b" }],
      },
      "en",
    );
    const hub = parsed.nodes.find((node) => node.id === "hub")!;
    expect(hub.degree).toBe(2);
    expect(parsed.nodes.find((node) => node.id === "a")!.degree).toBe(1);
  });

  it("derives importance from degree when the payload does not supply one", () => {
    const parsed = parseGraph(
      {
        nodes: [{ id: "hub" }, { id: "leaf" }, { id: "other" }],
        edges: [{ from: "hub", to: "leaf" }, { from: "hub", to: "other" }],
      },
      "en",
    );
    const byId = Object.fromEntries(parsed.nodes.map((node) => [node.id, node.importance]));
    expect(byId.hub).toBeGreaterThan(byId.leaf);
    for (const value of Object.values(byId)) {
      expect(value).toBeGreaterThanOrEqual(0.08);
      expect(value).toBeLessThanOrEqual(1);
    }
  });

  it("prefers an explicit importance over the degree heuristic", () => {
    const parsed = parseGraph({ nodes: [{ id: "n", importance: 0.9 }] }, "en");
    expect(parsed.nodes[0].importance).toBeCloseTo(0.9);
  });

  it("builds a lowercase search index from id, label, type, summary and source", () => {
    const parsed = parseGraph(
      {
        nodes: [
          {
            id: "n1",
            title: "Quarterly Plan",
            type: "Document",
            summary: "Revenue targets",
            metadata: { relative_path: "docs/Plan.md" },
          },
        ],
      },
      "en",
    );
    const { searchText } = parsed.nodes[0];
    expect(searchText).toBe(searchText.toLowerCase());
    expect(searchText).toContain("quarterly plan");
    expect(searchText).toContain("revenue targets");
  });
});

describe("buildExplorerModel", () => {
  const graph = parseGraph(
    {
      nodes: [
        { id: "hub", title: "Hub", type: "Topic", importance: 0.9 },
        { id: "leaf", title: "Leaf", type: "Topic", importance: 0.2 },
        { id: "doc", title: "Report", type: "File", importance: 0.5 },
        { id: "lonely", title: "Lonely", type: "Person", importance: 0.1 },
      ],
      edges: [
        { from: "hub", to: "leaf" },
        { from: "hub", to: "doc" },
      ],
    },
    "en",
  );

  it("reports the untruncated totals alongside what it drew", () => {
    const result = model(graph);
    expect(result.totalNodes).toBe(4);
    expect(result.totalEdges).toBe(2);
    expect(result.hiddenByFilters).toBe(0);
  });

  it("filters by group", () => {
    const result = model(graph, { groupFilter: "source" });
    expect(result.visibleNodes.map((node) => node.id)).toEqual(["doc"]);
  });

  it("filters by an exact node type as well as a group", () => {
    const result = model(graph, { groupFilter: "Person" });
    expect(result.visibleNodes.map((node) => node.id)).toEqual(["lonely"]);
  });

  it("applies the importance floor when there is no search", () => {
    const result = model(graph, { minImportance: 0.6 });
    expect(result.visibleNodes.map((node) => node.id)).toEqual(["hub"]);
  });

  it("lets a search override the importance floor", () => {
    // "lonely" is below any sensible floor; searching for it must still find it.
    const result = model(graph, { search: "lonely", minImportance: 0.9 });
    expect(result.visibleNodes.map((node) => node.id)).toEqual(["lonely"]);
  });

  it("narrows to a selected node and its neighbours", () => {
    const result = model(graph, { selectedId: "hub" });
    expect(new Set(result.visibleNodes.map((node) => node.id))).toEqual(
      new Set(["hub", "leaf", "doc"]),
    );
    expect(result.visibleNodes.map((node) => node.id)).not.toContain("lonely");
  });

  it("caps the node count and reports how many it hid", () => {
    const result = model(graph, { maxNodes: 2 });
    expect(result.visibleNodes).toHaveLength(2);
    expect(result.hiddenByFilters).toBe(2);
    // The cap keeps the most important nodes, not an arbitrary two.
    expect(result.visibleNodes[0].id).toBe("hub");
  });

  it("only draws an edge when both endpoints survived the filters", () => {
    const result = model(graph, { groupFilter: "source" });
    expect(result.visibleEdges).toEqual([]);
  });

  it("replaces a collapsed group with one cluster node", () => {
    const result = model(graph, { collapsedGroups: new Set(["knowledge"]) });
    const ids = result.elements.map((element) => element.data.id);
    expect(ids).toContain("group:knowledge");
    expect(result.visibleNodes.map((node) => node.id)).not.toContain("hub");
    const cluster = result.elements.find((element) => element.data.id === "group:knowledge");
    expect(cluster?.classes).toBe("cluster");
    expect(String(cluster?.data.displayLabel)).toContain("(2)");
  });

  it("reroutes an edge into the cluster when one end collapsed", () => {
    const result = model(graph, { collapsedGroups: new Set(["knowledge"]) });
    const edges = result.elements.filter((element) => element.data.source);
    expect(edges.some((edge) => edge.data.source === "group:knowledge")).toBe(true);
    // hub→leaf is entirely inside the cluster, so it collapses to nothing
    // rather than becoming a self-loop.
    expect(edges.every((edge) => edge.data.source !== edge.data.target)).toBe(true);
  });

  it("counts collapsed members in the group's visible count", () => {
    const result = model(graph, { collapsedGroups: new Set(["knowledge"]) });
    const knowledge = result.groups.find((group) => group.id === "knowledge")!;
    expect(knowledge.collapsed).toBe(true);
    expect(knowledge.visibleCount).toBe(2);
  });

  it.each([
    ["off", false],
    ["all", true],
  ])("labelMode %s controls whether unimportant nodes are labelled", (mode, labelled) => {
    const result = model(graph, { labelMode: mode as "off" | "all" });
    const leaf = result.elements.find((element) => element.data.id === "leaf");
    expect(Boolean(leaf?.data.displayLabel)).toBe(labelled);
  });

  it("labels important nodes even in the default mode", () => {
    const result = model(graph);
    const hub = result.elements.find((element) => element.data.id === "hub");
    expect(hub?.data.displayLabel).toBe("Hub");
  });

  it("marks the selected node and fades the rest", () => {
    const result = model(graph, { selectedId: "hub" });
    const hub = result.elements.find((element) => element.data.id === "hub");
    expect(String(hub?.classes)).toContain("selected");
    expect(hub?.data.borderColor).toBe(graphInkColor());
  });

  it("marks search matches", () => {
    const result = model(graph, { search: "report" });
    const doc = result.elements.find((element) => element.data.id === "doc");
    expect(String(doc?.classes)).toContain("match");
  });

  it("sizes a node from its importance and degree", () => {
    const result = model(graph);
    const hub = result.elements.find((element) => element.data.id === "hub");
    const lonely = result.elements.find((element) => element.data.id === "lonely");
    expect(Number(hub?.data.size)).toBeGreaterThan(Number(lonely?.data.size));
  });

  it("returns an empty model for an empty graph without throwing", () => {
    const result = model({ nodes: [], edges: [], groups: [] });
    expect(result.elements).toEqual([]);
    expect(result.visibleNodes).toEqual([]);
    expect(result.hiddenByFilters).toBe(0);
  });
});

describe("parseGraph payload variants", () => {
  it("unwraps a `{ data: … }` envelope when it carries the graph", () => {
    const parsed = parseGraph({ data: { nodes: [{ id: "a" }], edges: [] } }, "en");
    expect(parsed.nodes.map((node) => node.id)).toEqual(["a"]);
  });

  it("accepts an envelope whose nodes come as a record rather than a list", () => {
    // The unwrap must trigger for this shape too; a record of nodes then reads
    // as an empty list rather than crashing.
    const parsed = parseGraph({ data: { nodes: { a: { id: "a" } } } }, "en");
    expect(parsed.nodes).toEqual([]);
  });

  it("leaves a `data` field alone when it does not look like a graph", () => {
    const parsed = parseGraph({ data: { message: "no graph here" }, nodes: [{ id: "outer" }] }, "en");
    expect(parsed.nodes.map((node) => node.id)).toEqual(["outer"]);
  });

  it("accepts a numeric node id and stringifies it", () => {
    const parsed = parseGraph({ nodes: [{ id: 7 }, { id: NaN }] }, "en");
    expect(parsed.nodes.map((node) => node.id)).toEqual(["7"]);
  });

  it("drops an edge that has no endpoints at all", () => {
    const parsed = parseGraph(
      {
        nodes: [{ id: "a" }, { id: "b" }],
        edges: [{ to: "b" }, { from: "a" }, { from: "ghost", to: "b" }, { from: "a", to: "b" }],
      },
      "en",
    );
    expect(parsed.edges).toHaveLength(1);
  });

  it("reads importance from metadata graph metrics when the node has none", () => {
    const parsed = parseGraph(
      { nodes: [{ id: "m", metadata: { graph_metrics: { importance_norm: 0.7 } } }] },
      "en",
    );
    expect(parsed.nodes[0].importance).toBeCloseTo(0.7);
  });

  it("falls back to metadata for the summary and prefers a top-level source", () => {
    const parsed = parseGraph(
      {
        nodes: [
          { id: "s", source: "s3://bucket/file", metadata: { summary: "메타 요약" } },
        ],
      },
      "en",
    );
    expect(parsed.nodes[0].summary).toBe("메타 요약");
    expect(parsed.nodes[0].source).toBe("s3://bucket/file");
  });
});

describe("buildExplorerModel hand-built graphs", () => {
  const gnode = (over: Partial<GraphNode> & { id: string }): GraphNode => ({
    label: over.id,
    type: "Node",
    group: "other",
    summary: "",
    source: "",
    importance: 0.5,
    degree: 0,
    searchText: over.id.toLowerCase(),
    raw: {},
    ...over,
  });
  const gedge = (id: string, source: string, target: string): GraphEdge => ({
    id,
    source,
    target,
    label: "related",
    weight: 1,
  });

  it("selecting a collapsed cluster does not narrow to a neighbourhood", () => {
    const graph = parseGraph(
      {
        nodes: [{ id: "a", type: "Topic" }, { id: "b", type: "Person" }],
        edges: [],
      },
      "en",
    );
    const result = model(graph, { selectedId: "group:knowledge" });
    expect(result.visibleNodes).toHaveLength(2);
  });

  it("marks an edge as connected when the selected node is its target", () => {
    const graph = parseGraph(
      { nodes: [{ id: "hub" }, { id: "leaf" }], edges: [{ from: "hub", to: "leaf" }] },
      "en",
    );
    const result = model(graph, { selectedId: "leaf" });
    const edge = result.elements.find((element) => element.data.source);
    expect(edge?.classes).toBe("connected");
  });

  it("falls back to the definition id when the groups list does not know the group", () => {
    const graph: ParsedGraph = {
      nodes: [gnode({ id: "k1", group: "knowledge" }), gnode({ id: "s1", group: "source" })],
      edges: [],
      groups: [],
    };
    const result = model(graph, { collapsedGroups: new Set(["knowledge"]) });
    const cluster = result.elements.find((element) => element.data.id === "group:knowledge");
    expect(cluster?.data.displayLabel).toBe("knowledge (1)");
    const plain = result.elements.find((element) => element.data.id === "s1");
    expect(plain?.data.group).toBe("source");
  });

  it("puts a node with an unknown group into the catch-all bucket", () => {
    const graph: ParsedGraph = {
      nodes: [gnode({ id: "x", group: "martian" })],
      edges: [],
      groups: [],
    };
    const result = model(graph);
    const element = result.elements.find((item) => item.data.id === "x");
    // The last definition ("other") supplies colour and grouping.
    expect(element?.data.color).toBe(graphGroupColor("other"));
    expect(element?.data.group).toBe("other");
  });

  it("drops an edge whose duplicate-id endpoint resolves to a group that never collapsed", () => {
    // Two nodes share the id "dup" in different groups. The low-importance
    // twin ("people", listed first) falls outside the node cap, the
    // high-importance twin ("knowledge") is collapsed into a cluster. The edge
    // lookup finds the first twin, whose group has no cluster — the edge must
    // vanish rather than point at a cluster that is not on the canvas.
    const graph: ParsedGraph = {
      nodes: [
        gnode({ id: "dup", group: "people", importance: 0.1 }),
        gnode({ id: "dup", group: "knowledge", importance: 0.9 }),
        gnode({ id: "anchor", group: "source", importance: 0.8 }),
      ],
      edges: [gedge("e1", "dup", "anchor"), gedge("e2", "anchor", "dup")],
      groups: [],
    };
    const result = model(graph, { collapsedGroups: new Set(["knowledge"]), maxNodes: 2 });
    expect(result.visibleNodes.map((node) => node.id)).toEqual(["anchor"]);
    expect(result.elements.filter((element) => element.data.source)).toEqual([]);
  });
});

describe("resolveTokenColor", () => {
  it("wraps a live CSS token as comma-separated hsl() Cytoscape can parse", () => {
    document.documentElement.style.setProperty("--knowledge", "205 55% 66%");
    expect(resolveTokenColor("--knowledge", "0 0% 50%")).toBe("hsl(205, 55%, 66%)");
    document.documentElement.style.removeProperty("--knowledge");
  });

  it("uses the fallback triple when the token is empty", () => {
    expect(resolveTokenColor("--no-such-graph-token", "43 26% 94%")).toBe("hsl(43, 26%, 94%)");
  });

  it("rewrites an already-wrapped space hsl() into comma form", () => {
    document.documentElement.style.setProperty("--knowledge", "hsl(205 55% 66%)");
    expect(resolveTokenColor("--knowledge", "0 0% 50%")).toBe("hsl(205, 55%, 66%)");
    document.documentElement.style.removeProperty("--knowledge");
  });

  it("keeps a comma hsl() wrapper and passes hex or rgb through", () => {
    document.documentElement.style.setProperty("--knowledge", "hsl(205, 55%, 66%)");
    expect(resolveTokenColor("--knowledge", "0 0% 50%")).toBe("hsl(205, 55%, 66%)");
    document.documentElement.style.setProperty("--knowledge", "#3488c4");
    expect(resolveTokenColor("--knowledge", "0 0% 50%")).toBe("#3488c4");
    document.documentElement.style.setProperty("--knowledge", "rgb(52, 136, 196)");
    expect(resolveTokenColor("--knowledge", "0 0% 50%")).toBe("rgb(52, 136, 196)");
    document.documentElement.style.removeProperty("--knowledge");
  });

  it("falls back when the token is not a color, then last-resorts off gray", () => {
    document.documentElement.style.setProperty("--knowledge", "not-a-color");
    expect(resolveTokenColor("--knowledge", "168 48% 58%")).toBe("hsl(168, 48%, 58%)");
    expect(resolveTokenColor("--knowledge", "also-bad")).toBe("hsl(205, 55%, 66%)");
    document.documentElement.style.removeProperty("--knowledge");
  });
});
