import "@/i18n/brain";

import { EmptyState } from "@/components/primitives";
import { Badge } from "@/components/ui/badge";
import { SourceFeedback } from "@/features/evidence/SourceFeedback";
import { t, type Language } from "@/i18n";
import { asArray, isRecord, plainText } from "@/lib/utils";

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
            const title = plainText(item.title) || plainText(item.name) || `#${index + 1}`;
            const detail = plainText(item.summary) || plainText(item.snippet) || plainText(item.path);
            const type = plainText(item.type);
            return (
              <li key={id || index} className="entity-list-row space-y-1 rounded-lg border border-border bg-background/55 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="font-medium">{title}</div>
                  {type ? <Badge variant="muted">{type}</Badge> : null}
                </div>
                {detail && detail !== title ? <p className="text-sm text-muted-foreground">{detail}</p> : null}
                {id ? <SourceFeedback language={language} nodeId={id} title={title} query={query} /> : null}
              </li>
            );
          })}
        </ul>
      ) : (
        <EmptyState title={t(language, "brain.filters.empty")} detail={t(language, "brain.filters.emptyDetail")} />
      )}
    </div>
  );
}
