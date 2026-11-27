import "@/i18n/brain";

import * as React from "react";
import { FileText, MessageCircleQuestion } from "lucide-react";
import { EmptyState } from "@/components/primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { SourceChunkModal } from "@/features/brain/AnswerProof";
import { navigateHash } from "@/features/brain/navigation";
import { SourceFeedback } from "@/features/evidence/SourceFeedback";
import { t, type Language } from "@/i18n";
import { asArray, isRecord, plainText } from "@/lib/utils";
import { useAppStore } from "@/store/appStore";

function record(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {};
}

export function SearchResults({
  language,
  data,
  query,
}: {
  language: Language;
  data: Record<string, unknown>;
  query: string;
}) {
  const setPendingChatPrompt = useAppStore((state) => state.setPendingChatPrompt);
  const [source, setSource] = React.useState<{ nodeId: string; title: string } | null>(null);
  const ask = (title: string) => {
    setPendingChatPrompt(t(language, "brain.searchResult.askPrompt", { title }));
    navigateHash("/brain");
  };
  const rows = asArray<Record<string, unknown>>(data.matches || data).slice(0, 12);
  const temporal = record(data.temporal);
  const windowFallback = record(temporal.prefilter).fallback;
  const hidden = Number(record(temporal.superseded).excluded || 0);
  const sortedBy = data.sort_by === "newest" || data.sort_by === "relevance" ? data.sort_by : "";
  return (
    <div className="space-y-2">
      {windowFallback ? (
        <p role="status" className="rounded-md border border-border p-2 text-sm">
          {t(language, "brain.filters.windowFallback")}
        </p>
      ) : null}
      {sortedBy || hidden ? (
        <p className="text-xs tabular-nums text-muted-foreground">
          {[
            sortedBy ? t(language, `brain.filters.sorted.${sortedBy}`) : "",
            hidden ? t(language, "brain.filters.hiddenSuperseded", { count: hidden }) : "",
          ].filter(Boolean).join(" · ")}
        </p>
      ) : null}
      {rows.length ? (
        <ul className="entity-list m-0 grid list-none gap-2 p-0">
          {rows.map((item, index) => {
            const id = String(item.node_id || item.id || "");
            const name = plainText(item.title) || plainText(item.name);
            const title = name || `#${index + 1}`;
            const isNode = !item.item_type || item.item_type === "node";
            const nodeId = String(item.node_id || (isNode && item.id) || "");
            const detail = plainText(item.summary) || plainText(item.snippet) || plainText(item.path);
            const type = plainText(item.type);
            return (
              <li key={id || index} className="entity-list-row space-y-1 rounded-lg border border-border bg-background/55 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="font-medium">{title}</div>
                  {type ? <Badge variant="muted">{type}</Badge> : null}
                </div>
                {detail && detail !== title ? <p className="text-sm text-muted-foreground">{detail}</p> : null}
                {nodeId || name ? (
                  <div className="flex flex-wrap gap-1.5">
                    {nodeId ? (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        aria-label={t(language, "brain.searchResult.openAria", { title })}
                        onClick={() => setSource({ nodeId, title })}
                      >
                        <FileText className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
                        {t(language, "brain.searchResult.open")}
                      </Button>
                    ) : null}
                    {name ? (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        aria-label={t(language, "brain.searchResult.askAria", { title })}
                        onClick={() => ask(name)}
                      >
                        <MessageCircleQuestion className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
                        {t(language, "brain.searchResult.ask")}
                      </Button>
                    ) : null}
                  </div>
                ) : null}
                {id ? <SourceFeedback language={language} nodeId={id} title={title} query={query} /> : null}
              </li>
            );
          })}
        </ul>
      ) : (
        <EmptyState title={t(language, "brain.filters.empty")} detail={t(language, "brain.filters.emptyDetail")} />
      )}
      {source ? (
        <SourceChunkModal
          language={language}
          nodeId={source.nodeId}
          fallbackTitle={source.title}
          onClose={() => setSource(null)}
        />
      ) : null}
    </div>
  );
}
