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

export function MultiHopSynthesisView({
  language,
  data,
}: {
  language: Language;
  data: Record<string, unknown>;
}) {
  const paths = asArray<ReasoningPathItem>(data.reasoning_paths);
  const synthesis = isRecord(data.synthesis) ? data.synthesis : {};
  const bridges = asArray<BridgeConceptItem>(synthesis.bridge_concepts);
  const conflicts = asArray<NeighborhoodConflictItem>(synthesis.contradictions);
  const directHits = Number(synthesis.direct_hits || 0);
  const multiHopHits = Number(synthesis.multi_hop_hits || 0);

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
            {bridges.map((bridge) => (
              <Badge key={bridge.id} variant="muted" className="text-xs">
                {bridge.title} · {bridge.bridged_targets}
              </Badge>
            ))}
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
              <li key={`${conflict.source_id}-${conflict.target_id}-${idx}`} className="text-foreground">
                <span className="font-medium">{conflict.source_title}</span>
                {" — "}
                <Badge variant="danger" className="text-[10px]">
                  {conflict.relation}
                </Badge>
                {" → "}
                <span className="font-medium">{conflict.target_title}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {paths.length > 0 ? (
        <div className="space-y-1.5">
          <div className="flex items-center gap-1 text-[11px] font-medium text-muted-foreground">
            <GitBranch className="h-3 w-3" aria-hidden="true" />
            <span>{t(language, "brain.synthesis.paths")}</span>
          </div>
          <ul className="m-0 list-none space-y-1.5 p-0 text-xs">
            {paths.slice(0, 6).map((path) => (
              <li
                key={`${path.seed_id}-${path.bridge_id || "direct"}-${path.target_id}`}
                className="flex flex-wrap items-center gap-1.5 rounded-md border border-border/50 bg-background/70 px-2.5 py-1.5"
              >
                <Badge variant="muted" className="text-[10px]">
                  {t(language, "brain.synthesis.hopBadge", { hops: path.hops })}
                </Badge>
                <span className="font-medium text-foreground">{path.seed_title}</span>
                <span className="text-muted-foreground">
                  —[{path.relations[0] || t(language, "brain.synthesis.defaultRelation")}]→
                </span>
                {path.bridge_title ? (
                  <>
                    <span className="font-medium text-primary">{path.bridge_title}</span>
                    <span className="text-muted-foreground">
                      —[{path.relations[1] || t(language, "brain.synthesis.defaultRelation")}]→
                    </span>
                  </>
                ) : null}
                <span className="font-semibold text-foreground">{path.target_title}</span>
              </li>
            ))}
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
