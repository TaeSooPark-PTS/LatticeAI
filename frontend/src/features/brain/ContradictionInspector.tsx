import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Share2 } from "lucide-react";
import { latticeApi } from "@/api/client";
import { Button } from "@/components/ui/button";
import { t, type Language } from "@/i18n";
import { asArray } from "@/lib/utils";
import { useAppStore } from "@/store/appStore";
import { isRecord } from "./brainData";
import { navigateHash } from "./navigation";

type ResolutionChoice = "keep_old" | "replace" | "keep_both_temporal";

type StagedItem = {
  id: string;
  title: string;
  summary: string;
};

function summarizeConflictItem(item: Record<string, unknown>): {
  key: string;
  focusNodeId: string | null;
  summary: string;
} {
  const leftId = typeof item.left_id === "string" ? item.left_id : "";
  const rightId = typeof item.right_id === "string" ? item.right_id : "";
  const sourceId = typeof item.source === "string" ? item.source : "";
  const nodeId = typeof item.id === "string" ? item.id : "";
  const leftText = typeof item.left_content === "string" && item.left_content
    ? item.left_content
    : typeof item.left_title === "string" && item.left_title
      ? item.left_title
      : leftId || sourceId;
  const rightText = typeof item.right_content === "string" && item.right_content
    ? item.right_content
    : typeof item.right_title === "string" && item.right_title
      ? item.right_title
      : typeof item.target === "string"
        ? item.target
        : rightId;
  const singleText = typeof item.content === "string" ? item.content : "";
  const focusNodeId = leftId || sourceId || nodeId || null;
  const summary = leftText && rightText ? `${leftText} ↔ ${rightText}` : singleText || leftText || nodeId;
  return {
    key: `${leftId || sourceId || nodeId}-${rightId || "item"}`,
    focusNodeId,
    summary,
  };
}

export function ContradictionInspector({
  language,
  items,
}: {
  language: Language;
  items: Array<Record<string, unknown>>;
}) {
  const qc = useQueryClient();
  const setFocusedGraphNodeId = useAppStore((state) => state.setFocusedGraphNodeId);
  const [staged, setStaged] = React.useState<StagedItem[]>([]);
  const [resolvedNote, setResolvedNote] = React.useState<string | null>(null);

  const stageMut = useMutation({
    mutationFn: () => latticeApi.brainProposeContradictions(),
    onSuccess: (res) => {
      if (res.ok && isRecord(res.data)) {
        const proposed = asArray<Record<string, unknown>>(res.data.proposed).flatMap((entry): StagedItem[] => {
          const id = typeof entry.id === "string" ? entry.id : "";
          if (!id) return [];
          return [{
            id,
            title: typeof entry.title === "string" && entry.title ? entry.title : id,
            summary: typeof entry.summary === "string" ? entry.summary : "",
          }];
        });
        setStaged(proposed);
      }
    },
  });

  const resolveMut = useMutation({
    mutationFn: ({ itemId, resolution }: { itemId: string; resolution: ResolutionChoice }) =>
      latticeApi.brainResolveContradiction(itemId, resolution),
    onSuccess: (res, vars) => {
      if (res.ok) {
        setStaged((prev) => prev.filter((item) => item.id !== vars.itemId));
        const labelKey = vars.resolution === "keep_old"
          ? "intelligence.contradictions.keepOld"
          : vars.resolution === "replace"
            ? "intelligence.contradictions.replace"
            : "intelligence.contradictions.keepBoth";
        setResolvedNote(t(language, "intelligence.contradictions.resolved", { resolution: t(language, labelKey) }));
        void qc.invalidateQueries({ queryKey: ["brainContradictions"] });
        void qc.invalidateQueries({ queryKey: ["brainHealth"] });
        void qc.invalidateQueries({ queryKey: ["graph"] });
      }
    },
  });

  const openInGraph = (nodeId: string) => {
    setFocusedGraphNodeId(nodeId);
    navigateHash("/knowledge-graph");
  };

  return (
    <div
      data-testid="contradiction-inspector"
      className="mt-2 space-y-2 rounded-lg border border-destructive/30 bg-destructive/5 p-3"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-destructive">
          <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
          {t(language, "intelligence.contradictions.panel")}
        </span>
        <Button
          type="button"
          variant="outline"
          size="sm"
          data-testid="contradiction-stage-btn"
          disabled={stageMut.isPending}
          onClick={() => stageMut.mutate()}
        >
          {stageMut.isPending
            ? t(language, "intelligence.contradictions.staging")
            : t(language, "intelligence.contradictions.stage")}
        </Button>
      </div>

      <ul className="m-0 list-none space-y-1.5 p-0 text-xs">
        {items.slice(0, 4).map((raw, idx) => {
          const parsed = summarizeConflictItem(raw);
          return (
            <li
              key={`${parsed.key}-${idx}`}
              className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border/50 bg-background/80 px-2.5 py-1.5"
            >
              <span className="text-foreground">{parsed.summary}</span>
              {parsed.focusNodeId ? (
                <button
                  type="button"
                  data-testid={`contradiction-open-graph-${parsed.focusNodeId}`}
                  onClick={() => openInGraph(parsed.focusNodeId!)}
                  className="inline-flex items-center gap-1 text-[11px] font-medium text-primary hover:underline"
                >
                  <Share2 className="h-3 w-3" aria-hidden="true" />
                  {t(language, "intelligence.contradictions.openGraph")}
                </button>
              ) : null}
            </li>
          );
        })}
      </ul>

      {staged.length > 0 ? (
        <div className="space-y-2 pt-1">
          {staged.map((item) => (
            <div
              key={item.id}
              data-testid={`staged-contradiction-${item.id}`}
              className="space-y-1.5 rounded-md border border-border/60 bg-background p-2.5 text-xs"
            >
              <div className="font-semibold text-foreground">{item.title}</div>
              {item.summary ? <p className="m-0 text-muted-foreground">{item.summary}</p> : null}
              <div className="flex flex-wrap gap-1.5">
                {(["keep_old", "replace", "keep_both_temporal"] as const).map((choice) => {
                  const key = choice === "keep_old"
                    ? "intelligence.contradictions.keepOld"
                    : choice === "replace"
                      ? "intelligence.contradictions.replace"
                      : "intelligence.contradictions.keepBoth";
                  return (
                    <Button
                      key={choice}
                      type="button"
                      variant="outline"
                      size="sm"
                      data-testid={`resolve-contradiction-${item.id}-${choice}`}
                      disabled={resolveMut.isPending}
                      onClick={() => resolveMut.mutate({ itemId: item.id, resolution: choice })}
                    >
                      {t(language, key)}
                    </Button>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      ) : null}

      {resolvedNote ? (
        <p className="m-0 text-xs font-medium text-foreground" role="status">
          {resolvedNote}
        </p>
      ) : null}
    </div>
  );
}
