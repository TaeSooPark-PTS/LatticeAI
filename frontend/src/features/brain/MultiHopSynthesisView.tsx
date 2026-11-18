import * as React from "react";
import { AlertTriangle, GitBranch, Sparkles } from "lucide-react";
import type {
  BridgeConceptItem,
  NeighborhoodConflictItem,
  ReasoningPathItem,
} from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { t, type Language } from "@/i18n";
import { asArray } from "@/lib/utils";
import { isRecord } from "@/pages/brain/graphExplorer";

type HopFilter = "all" | "multiHop" | "conflicts";

function NodeChip({
  id,
  title,
  language,
  className,
  onSelectNode,
}: {
  id: string;
  title: string;
  language: Language;
  className: string;
  onSelectNode?: (nodeId: string, title: string) => void;
}) {
  if (!onSelectNode) {
    return <span className={className}>{title}</span>;
  }
  return (
    <button
      type="button"
      onClick={() => onSelectNode(id, title)}
      aria-label={t(language, "brain.synthesis.inspectNode", { title })}
      className={`${className} rounded px-1 py-0.5 underline-offset-2 hover:bg-muted/80 hover:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary`}
    >
      {title}
    </button>
  );
}

export function MultiHopSynthesisView({
  language,
  data,
  onSelectNode,
}: {
  language: Language;
  data: Record<string, unknown>;
  onSelectNode?: (nodeId: string, title: string) => void;
}) {
  const [filter, setFilter] = React.useState<HopFilter>("all");
  const paths = asArray<ReasoningPathItem>(data.reasoning_paths);
  const synthesis = isRecord(data.synthesis) ? data.synthesis : {};
  const bridges = asArray<BridgeConceptItem>(synthesis.bridge_concepts);
  const conflicts = asArray<NeighborhoodConflictItem>(synthesis.contradictions);
  const directHits = Number(synthesis.direct_hits || 0);
  const multiHopHits = Number(synthesis.multi_hop_hits || 0);
  const hasMultiHop = paths.some((path) => path.hops >= 2);
  const hasConflictPaths = paths.some((path) => Boolean(path.is_contradiction));

  const visiblePaths = paths.filter((path) => {
    if (filter === "multiHop") return path.hops >= 2;
    if (filter === "conflicts") return Boolean(path.is_contradiction);
    return true;
  });

  return (
    <section
      data-testid="multihop-synthesis-panel"
      className="rounded-xl border border-border/70 bg-muted/20 p-3.5 space-y-3"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 text-xs font-semibold text-foreground">
          <Sparkles className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
          <span>{t(language, "brain.synthesis.title")}</span>
        </div>
        <span className="text-xs text-muted-foreground">
          {t(language, "brain.synthesis.stats", {
            direct: directHits,
            expanded: multiHopHits,
            paths: paths.length,
          })}
        </span>
      </div>

      {bridges.length > 0 ? (
        <div className="space-y-1.5">
          <div className="text-[11px] font-medium text-muted-foreground">
            {t(language, "brain.synthesis.bridges")}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {bridges.map((bridge) =>
              onSelectNode ? (
                <button
                  key={bridge.id}
                  type="button"
                  data-testid={`bridge-chip-${bridge.id}`}
                  onClick={() => onSelectNode(bridge.id, bridge.title)}
                  aria-label={t(language, "brain.synthesis.inspectNode", { title: bridge.title })}
                  className="inline-flex items-center rounded-full border border-border bg-background/80 px-2.5 py-0.5 text-xs font-medium text-foreground hover:border-primary/60 hover:bg-primary/5"
                >
                  {bridge.title} · {bridge.bridged_targets}
                </button>
              ) : (
                <Badge key={bridge.id} variant="muted" className="text-xs">
                  {bridge.title} · {bridge.bridged_targets}
                </Badge>
              ),
            )}
          </div>
        </div>
      ) : null}

      {conflicts.length > 0 ? (
        <div className="space-y-1.5 rounded-lg border border-destructive/40 bg-destructive/5 p-2.5">
          <div className="flex items-center gap-1.5 text-[11px] font-semibold text-destructive">
            <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
            <span>{t(language, "brain.synthesis.conflicts")}</span>
          </div>
          <ul className="m-0 list-none space-y-1 p-0 text-xs">
            {conflicts.map((conflict, idx) => (
              <li key={`${conflict.source_id}-${conflict.target_id}-${idx}`} className="flex flex-wrap items-center gap-1 text-foreground">
                <NodeChip
                  id={conflict.source_id}
                  title={conflict.source_title}
                  language={language}
                  className="font-medium"
                  onSelectNode={onSelectNode}
                />
                <span className="text-muted-foreground">—</span>
                <Badge variant="danger" className="text-[10px]">
                  {conflict.relation}
                </Badge>
                <span className="text-muted-foreground">→</span>
                <NodeChip
                  id={conflict.target_id}
                  title={conflict.target_title}
                  language={language}
                  className="font-medium"
                  onSelectNode={onSelectNode}
                />
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {paths.length > 0 ? (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-1 text-[11px] font-medium text-muted-foreground">
              <GitBranch className="h-3 w-3" aria-hidden="true" />
              <span>{t(language, "brain.synthesis.paths")}</span>
            </div>
            {hasMultiHop || hasConflictPaths ? (
              <div className="flex flex-wrap gap-1" role="group" aria-label={t(language, "brain.synthesis.paths")}>
                {(["all", ...(hasMultiHop ? (["multiHop"] as const) : []), ...(hasConflictPaths ? (["conflicts"] as const) : [])] as HopFilter[]).map((key) => (
                  <button
                    key={key}
                    type="button"
                    data-testid={`synthesis-filter-${key}`}
                    aria-pressed={filter === key}
                    onClick={() => setFilter(key)}
                    className={`rounded-full px-2 py-0.5 text-[11px] font-medium transition-colors ${
                      filter === key
                        ? "bg-primary text-primary-foreground"
                        : "bg-background/80 text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    {t(language, `brain.synthesis.filter.${key}`)}
                  </button>
                ))}
              </div>
            ) : null}
          </div>
          <ul className="m-0 list-none space-y-1.5 p-0 text-xs">
            {visiblePaths.slice(0, 6).map((path) => {
              const pct = Math.max(5, Math.min(100, Math.round(Number(path.path_score || 0) * 100)));
              return (
                <li
                  key={`${path.seed_id}-${path.bridge_id || "direct"}-${path.target_id}`}
                  className={`flex flex-wrap items-center justify-between gap-2 rounded-md border px-2.5 py-1.5 ${
                    path.is_contradiction
                      ? "border-destructive/50 bg-destructive/5"
                      : "border-border/50 bg-background/70"
                  }`}
                >
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Badge variant={path.is_contradiction ? "danger" : "muted"} className="text-[10px]">
                      {t(language, "brain.synthesis.hopBadge", { hops: path.hops })}
                    </Badge>
                    <NodeChip
                      id={path.seed_id}
                      title={path.seed_title}
                      language={language}
                      className="font-medium text-foreground"
                      onSelectNode={onSelectNode}
                    />
                    <span className="text-muted-foreground">
                      —[{path.relations[0] || t(language, "brain.synthesis.defaultRelation")}]→
                    </span>
                    {path.bridge_title ? (
                      <>
                        <NodeChip
                          id={path.bridge_id || ""}
                          title={path.bridge_title}
                          language={language}
                          className="font-medium text-primary"
                          onSelectNode={path.bridge_id ? onSelectNode : undefined}
                        />
                        <span className="text-muted-foreground">
                          —[{path.relations[1] || t(language, "brain.synthesis.defaultRelation")}]→
                        </span>
                      </>
                    ) : null}
                    <NodeChip
                      id={path.target_id}
                      title={path.target_title}
                      language={language}
                      className="font-semibold text-foreground"
                      onSelectNode={onSelectNode}
                    />
                  </div>
                  <span className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground">
                    <span className="h-1.5 w-10 overflow-hidden rounded-full bg-muted" aria-hidden="true">
                      <span
                        className={`block h-full rounded-full ${path.is_contradiction ? "bg-destructive" : "bg-primary"}`}
                        style={{ width: `${pct}%` }}
                      />
                    </span>
                    <span>{t(language, "brain.synthesis.scoreBadge", { score: pct })}</span>
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      ) : (
        <p className="m-0 text-xs text-muted-foreground">
          {t(language, "brain.synthesis.empty")}
        </p>
      )}
    </section>
  );
}
