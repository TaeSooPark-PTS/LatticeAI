import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { latticeApi } from "@/api/client";
import { computeBrainGrowth } from "@/features/brain3d/brainGrowth";
import { t, type Language } from "@/i18n";
import { fmtNumber } from "@/lib/utils";

function sumRecordValues(value: unknown): number {
  if (typeof value === "number" && Number.isFinite(value)) return Math.max(0, Math.round(value));
  if (!value || typeof value !== "object") return 0;
  return Object.values(value as Record<string, unknown>).reduce<number>((total, entry) => {
    const num = Number(entry);
    return Number.isFinite(num) && num > 0 ? total + Math.round(num) : total;
  }, 0);
}

export function extractGraphCounts(payload: unknown): { nodes: number; edges: number } {
  const data = payload && typeof payload === "object" ? (payload as Record<string, unknown>) : {};
  const explicitNodes = Number(data.total_nodes);
  const explicitEdges = Number(data.total_edges);
  const nodes = Number.isFinite(explicitNodes) && explicitNodes >= 0 ? Math.round(explicitNodes) : sumRecordValues(data.nodes);
  const edges = Number.isFinite(explicitEdges) && explicitEdges >= 0 ? Math.round(explicitEdges) : sumRecordValues(data.edges);
  return { nodes, edges };
}

export function BrainVitalPill({ language }: { language: Language }) {
  const statsQ = useQuery({
    queryKey: ["graphStats"],
    queryFn: latticeApi.graphStats,
    staleTime: 30_000,
  });
  const counts = React.useMemo(() => extractGraphCounts(statsQ.data?.data), [statsQ.data]);
  const growth = React.useMemo(() => computeBrainGrowth(counts.nodes, counts.edges), [counts.nodes, counts.edges]);
  const stageLabel = t(language, `shell.vital.stage.${growth.stage}`);
  const ariaLabel = t(language, "shell.vital.aria", {
    stage: stageLabel,
    nodes: fmtNumber(counts.nodes),
    edges: fmtNumber(counts.edges),
  });

  return (
    <a
      href="#/knowledge-graph"
      className="brain-vital-pill"
      data-testid="brain-vital-pill"
      data-stage={growth.stage}
      aria-label={ariaLabel}
      title={ariaLabel}
    >
      <span className="brain-vital-pill-dot" aria-hidden="true" />
      <span className="brain-vital-pill-stage">{stageLabel}</span>
      <span className="brain-vital-pill-counts" aria-hidden="true">
        {fmtNumber(counts.nodes)} · {fmtNumber(counts.edges)}
      </span>
    </a>
  );
}
