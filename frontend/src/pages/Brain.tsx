import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import cytoscape, { Core } from "cytoscape";
import { BrainCircuit, DatabaseBackup, Network, Search, Sparkles } from "lucide-react";
import { latticeApi } from "@/api/client";
import { ActionButton, DataPanel, EntityList, JsonView, LoadingPanel, StatGrid, Tabs } from "@/components/primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { asArray, pct } from "@/lib/utils";

type BrainTab = "overview" | "graph" | "search" | "memory" | "provenance" | "portability";

const tabs: Array<{ id: BrainTab; label: string }> = [
  { id: "overview", label: "Overview" },
  { id: "graph", label: "Graph" },
  { id: "search", label: "Search" },
  { id: "memory", label: "Memory" },
  { id: "provenance", label: "Provenance" },
  { id: "portability", label: "Portability" },
];

function graphElements(data: unknown) {
  const graph = data as { nodes?: Array<Record<string, unknown>>; edges?: Array<Record<string, unknown>> };
  const nodes = asArray<Record<string, unknown>>(graph.nodes).slice(0, 160).map((node) => ({
    data: {
      id: String(node.id || node.node_id || node.title),
      label: String(node.title || node.label || node.id || "Node"),
      type: String(node.type || "Node"),
    },
  }));
  const nodeIds = new Set(nodes.map((node) => node.data.id));
  const edges = asArray<Record<string, unknown>>(graph.edges).slice(0, 260).flatMap((edge, index) => {
    const source = String(edge.from || edge.source || edge.source_id || "");
    const target = String(edge.to || edge.target || edge.target_id || "");
    if (!nodeIds.has(source) || !nodeIds.has(target)) return [];
    return [{
      data: {
        id: String(edge.id || `edge-${index}`),
        source,
        target,
        label: String(edge.type || edge.label || ""),
      },
    }];
  });
  return [...nodes, ...edges];
}

function CytoscapeGraph({ data }: { data: unknown }) {
  const hostRef = React.useRef<HTMLDivElement | null>(null);
  const cyRef = React.useRef<Core | null>(null);
  React.useEffect(() => {
    if (!hostRef.current) return;
    const elements = graphElements(data);
    cyRef.current?.destroy();
    cyRef.current = cytoscape({
      container: hostRef.current,
      elements,
      style: [
        {
          selector: "node",
          style: {
            "background-color": "#21c7bd",
            "border-color": "#88fff5",
            "border-width": 1,
            color: "#f7ffff",
            label: "data(label)",
            "font-size": 9,
            "text-outline-color": "#071012",
            "text-outline-width": 2,
            width: 22,
            height: 22,
          },
        },
        {
          selector: "edge",
          style: {
            width: 1.2,
            "line-color": "#6b7893",
            "target-arrow-shape": "triangle",
            "target-arrow-color": "#6b7893",
            "curve-style": "bezier",
          },
        },
      ],
      layout: { name: "cose", animate: false, idealEdgeLength: 110, nodeRepulsion: 4500 },
      wheelSensitivity: 0.25,
    });
    return () => cyRef.current?.destroy();
  }, [data]);
  return <div ref={hostRef} data-testid="brain-cytoscape" className="h-[520px] w-full overflow-hidden rounded-lg border border-border bg-background brain-grid" />;
}

export function BrainPage({ initialTab }: { initialTab?: string }) {
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
            The visible knowledge substrate: graph, memory, provenance, retrieval, and local portability. Empty states come from API availability, not canned data.
          </p>
        </div>
        <div className="rounded-lg border border-border bg-card p-4">
          <div className="text-xs uppercase text-muted-foreground">Provenance coverage</div>
          <div className="mt-2 text-3xl font-semibold">{pct((coverage.data?.data as Record<string, unknown>)?.coverage_ratio)}</div>
          <div className="mt-2 text-sm text-muted-foreground">Source: {coverage.data?.source || "loading"}</div>
        </div>
      </header>
      <Tabs tabs={tabs} value={tab} onChange={(id) => setTab(id as BrainTab)} />

      {tab === "overview" ? (
        <div className="grid gap-4 xl:grid-cols-2">
          <DataPanel title="Brain status" result={stats.data}>
            {(data) => <StatGrid stats={[
              { label: "Nodes", value: (data as Record<string, unknown>).total_nodes ?? 0 },
              { label: "Edges", value: (data as Record<string, unknown>).total_edges ?? 0 },
              { label: "Node types", value: Object.keys(((data as Record<string, unknown>).nodes as Record<string, unknown>) || {}).length },
              { label: "Edge types", value: Object.keys(((data as Record<string, unknown>).edges as Record<string, unknown>) || {}).length },
            ]} />}
          </DataPanel>
          <DataPanel title="Retrieval index" result={index.data}>
            {(data) => <JsonView value={data} />}
          </DataPanel>
          <DataPanel title="Memory tiers" result={memory.data}>
            {(data) => <EntityList items={(data as Record<string, unknown>).tiers || (data as Record<string, unknown>).sources} titleKey="name" metaKey="health" />}
          </DataPanel>
          <DataPanel title="Recent provenance" result={provenance.data}>
            {(data) => <EntityList items={(data as Record<string, unknown>).items || data} titleKey="source" metaKey="source_type" />}
          </DataPanel>
        </div>
      ) : null}

      {tab === "graph" ? (
        graph.isLoading ? <LoadingPanel title="Knowledge graph" /> : (
          <DataPanel title="Knowledge graph" description="Cytoscape.js explorer backed by /knowledge-graph/graph." result={graph.data}>
            {(data) => <CytoscapeGraph data={data} />}
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

function HybridSearch() {
  const [query, setQuery] = React.useState("lattice brain");
  const search = useMutation({ mutationFn: () => latticeApi.hybridSearch(query) });
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Search className="h-4 w-4" /> Hybrid search</CardTitle>
        <CardDescription>Calls the backend fused search endpoint and renders per-result source scores when returned.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input placeholder="lattice brain" value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => e.key === "Enter" && search.mutate()} />
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
        {(data) => <JsonView value={data} />}
      </DataPanel>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Sparkles className="h-4 w-4" /> Recall</CardTitle>
          <CardDescription>Searches the real memory recall endpoint.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Recall memories about..." />
          <div className="flex gap-2">
            <Button disabled={!query.trim() || recall.isPending} onClick={() => recall.mutate()}>Recall</Button>
            <ActionButton label="Compact" action={() => latticeApi.memoryCompact()} />
            <ActionButton label="Rebuild vector" action={() => latticeApi.memoryRebuild()} />
          </div>
          {recall.data ? <JsonView value={recall.data.data} /> : null}
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
        {(data) => <JsonView value={data} />}
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
    mutationFn: () => latticeApi.graphImport(JSON.parse(artifact), true),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["portability"] }),
  });
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <DataPanel title="Portability status" result={port.data}>
        {(data) => <JsonView value={data} />}
      </DataPanel>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><DatabaseBackup className="h-4 w-4" /> Export, backup, import</CardTitle>
          <CardDescription>Every control calls a real portability endpoint. Import is dry-run by default from pasted JSON.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap gap-2">
            <ActionButton label="Export graph JSON" action={() => latticeApi.graphExport()} />
            <ActionButton label="Create backup" action={() => latticeApi.graphBackup()} />
          </div>
          <Textarea value={artifact} onChange={(e) => setArtifact(e.target.value)} placeholder="Paste an export artifact JSON for dry-run import" />
          <Button
            variant="outline"
            disabled={!artifact.trim() || importMutation.isPending}
            onClick={() => importMutation.mutate()}
          >
            Dry-run import
          </Button>
          {importMutation.data ? <JsonView value={importMutation.data.data} /> : null}
        </CardContent>
      </Card>
    </div>
  );
}
