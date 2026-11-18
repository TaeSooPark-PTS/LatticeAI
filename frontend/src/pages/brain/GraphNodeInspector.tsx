import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, Copy, FileText, MessageCircleQuestion, Network } from "lucide-react";
import { latticeApi } from "@/api/client";
import { KeyValueList, StructuredView } from "@/components/primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { SourceChunkModal } from "@/features/brain/AnswerProof";
import { MultiHopSynthesisView } from "@/features/brain/MultiHopSynthesisView";
import { navigateHash } from "@/features/brain/navigation";
import { t, type Language } from "@/i18n";
import { asArray, plainText } from "@/lib/utils";
import { useAppStore, type WorkspaceMode } from "@/store/appStore";
import { isConflictRelation, isRecord, type GraphGroup, type GraphNode } from "./graphExplorer";
import { graphTypeLabel, groupLabelFor, sourceCreatedAt, sourceType } from "./sourceMeta";

type NeighborItem = {
  id: string;
  title: string;
  relation: string;
  weight: number;
  isConflict: boolean;
};

function extractNeighbors(seedId: string, payload: Record<string, unknown>): NeighborItem[] {
  const neighborhood = isRecord(payload.neighborhood) ? payload.neighborhood : {};
  const nodes = asArray<Record<string, unknown>>(neighborhood.nodes);
  const edges = asArray<Record<string, unknown>>(neighborhood.edges);
  const nodeMap = new Map<string, Record<string, unknown>>();
  for (const node of nodes) {
    const id = typeof node.id === "string" ? node.id : "";
    if (id) nodeMap.set(id, node);
  }
  const seen = new Set<string>();
  const items: NeighborItem[] = [];
  for (const edge of edges) {
    const from = typeof edge.from === "string" ? edge.from : "";
    const to = typeof edge.to === "string" ? edge.to : "";
    if (from !== seedId && to !== seedId) continue;
    const peerId = from === seedId ? to : from;
    if (!peerId || peerId === seedId || seen.has(peerId)) continue;
    seen.add(peerId);
    const peer = nodeMap.get(peerId) || {};
    const title = plainText(typeof peer.title === "string" ? peer.title : "") || peerId;
    const relation = typeof edge.type === "string" && edge.type ? edge.type : "RELATES_TO";
    const rawWeight = Number(edge.weight);
    const weight = Number.isFinite(rawWeight) ? Math.max(0.05, Math.min(1, rawWeight)) : 0.75;
    items.push({
      id: peerId,
      title,
      relation,
      weight,
      isConflict: isConflictRelation(relation),
    });
  }
  items.sort((a, b) => Number(b.isConflict) - Number(a.isConflict) || b.weight - a.weight);
  return items.slice(0, 8);
}

export function GraphNodeInspector({
  selected,
  groups,
  mode,
  language,
  onSelectNode,
}: {
  selected: GraphNode;
  groups: GraphGroup[];
  mode: WorkspaceMode;
  language: Language;
  onSelectNode: (nodeId: string) => void;
}) {
  const setPendingChatPrompt = useAppStore((state) => state.setPendingChatPrompt);
  const [sourceModalOpen, setSourceModalOpen] = React.useState(false);

  const inspectQ = useQuery({
    queryKey: ["graphNodeInspect", selected.id],
    queryFn: () => latticeApi.graphNodeInspect(selected.id, 2, 40),
  });

  const payload = inspectQ.data?.ok && isRecord(inspectQ.data.data) ? inspectQ.data.data : {};
  const liveNode = isRecord(payload.node) ? payload.node : {};
  const rawConfidence = Number(liveNode.confidence ?? selected.raw.confidence);
  const confidencePct = Number.isFinite(rawConfidence) && rawConfidence > 0
    ? Math.round(Math.min(1, rawConfidence) * 100)
    : Math.round(selected.importance * 100);
  const neighbors = React.useMemo(() => extractNeighbors(selected.id, payload), [selected.id, payload]);
  const reasoning = isRecord(payload.reasoning) ? payload.reasoning : null;

  const handleAskBrain = () => {
    setPendingChatPrompt(t(language, "graph.inspector.askPrompt", { title: selected.label }));
    navigateHash("/brain");
  };

  return (
    <div className="space-y-3" data-testid="graph-node-inspector">
      <div>
        <div className="text-lg font-semibold">{selected.label}</div>
        <div className="mt-1 flex flex-wrap gap-1">
          <Badge variant="muted">{graphTypeLabel(selected.type, language)}</Badge>
          <Badge variant="muted">{groupLabelFor(selected, groups)}</Badge>
          <Badge variant="success">{t(language, "graph.inspector.confidence", { score: confidencePct })}</Badge>
        </div>
      </div>

      {selected.summary ? <p className="text-sm text-muted-foreground">{selected.summary}</p> : null}

      <div className="flex flex-wrap gap-1.5">
        <Button
          type="button"
          variant="outline"
          size="sm"
          data-testid="inspector-open-source"
          onClick={() => setSourceModalOpen(true)}
        >
          <FileText className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
          {t(language, "graph.inspector.openSource")}
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          data-testid="inspector-ask-brain"
          onClick={handleAskBrain}
        >
          <MessageCircleQuestion className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
          {t(language, "graph.inspector.askBrain")}
        </Button>
        {selected.source ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void navigator.clipboard?.writeText(selected.source)}
          >
            <Copy className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
            {t(language, "graph.copySource")}
          </Button>
        ) : null}
      </div>

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

      {inspectQ.isPending ? (
        <p className="text-xs text-muted-foreground">{t(language, "graph.inspector.loading")}</p>
      ) : (
        <>
          <div className="space-y-1.5 rounded-lg border border-border/60 bg-muted/15 p-2.5">
            <div className="flex items-center gap-1.5 text-xs font-semibold text-foreground">
              <Network className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
              <span>{t(language, "graph.inspector.neighbors", { count: neighbors.length })}</span>
            </div>
            {neighbors.length > 0 ? (
              <div className="space-y-1">
                {neighbors.map((neighbor) => (
                  <button
                    key={neighbor.id}
                    type="button"
                    data-testid={`inspector-neighbor-${neighbor.id}`}
                    onClick={() => onSelectNode(neighbor.id)}
                    className="flex w-full items-center justify-between gap-2 rounded-md border border-border/40 bg-background/80 px-2.5 py-1.5 text-left text-xs hover:border-primary/60"
                  >
                    <span className="truncate font-medium text-foreground">{neighbor.title}</span>
                    <span className="inline-flex shrink-0 items-center gap-1">
                      <Badge variant={neighbor.isConflict ? "danger" : "muted"} className="text-[10px]">
                        {neighbor.relation}
                      </Badge>
                      <ArrowUpRight className="h-3 w-3 text-muted-foreground" aria-hidden="true" />
                    </span>
                  </button>
                ))}
              </div>
            ) : (
              <p className="m-0 text-xs text-muted-foreground">{t(language, "graph.inspector.neighborsEmpty")}</p>
            )}
          </div>

          {reasoning ? (
            <MultiHopSynthesisView
              language={language}
              data={reasoning}
              onSelectNode={(nodeId) => onSelectNode(nodeId)}
            />
          ) : null}
        </>
      )}

      {sourceModalOpen ? (
        <SourceChunkModal
          nodeId={selected.id}
          fallbackTitle={selected.label}
          language={language}
          onClose={() => setSourceModalOpen(false)}
        />
      ) : null}
    </div>
  );
}
