import * as React from "react";
import "@/i18n/workspace";
import type { ApiResult } from "@/api/base";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { DOCUMENT_FETCH_LIMIT } from "@/features/capture/captureDomain";
import { t } from "@/i18n";
import { asArray } from "@/lib/utils";
import { useAppStore } from "@/store/appStore";

const SourceChunkModal = React.lazy(() =>
  import("@/features/brain/AnswerProof").then((module) => ({ default: module.SourceChunkModal })),
);

const PAGE_SIZE = 12;

const searchable = (value: string) => value.normalize("NFC").toLowerCase();

export function DocumentLibrary({ result, onRetry }: { result?: ApiResult<unknown>; onRetry: () => void }) {
  const language = useAppStore((state) => state.language);
  const [filter, setFilter] = React.useState("");
  const [visible, setVisible] = React.useState(PAGE_SIZE);
  const [opened, setOpened] = React.useState<{ id: string; name: string } | null>(null);
  const data = result?.data as { documents?: unknown } | undefined;
  const documents = asArray<Record<string, unknown>>(data?.documents).map((item) => ({
    id: String(item.id || ""),
    name: String(item.filename || item.title || t(language, "capture.library.untitled")),
    indexed: item.ingest_state === "indexed",
  }));
  const needle = searchable(filter.trim());
  const matching = documents.filter((item) => searchable(item.name).includes(needle));

  const body = () => {
    if (!result) return <p role="status" className="text-sm text-muted-foreground">{t(language, "ui.loading")}</p>;
    if (!result.ok) {
      return (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-3 text-sm">
          <div>
            <p className="font-medium">{t(language, "capture.library.loadError")}</p>
            {result.error ? <p className="text-xs text-muted-foreground">{result.error}</p> : null}
          </div>
          <Button size="sm" variant="outline" onClick={onRetry}>{t(language, "capture.library.retry")}</Button>
        </div>
      );
    }
    if (!documents.length) return <p className="text-sm text-muted-foreground">{t(language, "capture.library.empty")}</p>;
    return (
      <>
        <Input
          type="search"
          value={filter}
          aria-label={t(language, "capture.library.filter")}
          placeholder={t(language, "capture.library.filter")}
          onChange={(event) => {
            setFilter(event.target.value);
            setVisible(PAGE_SIZE);
          }}
        />
        <p role="status" className="text-xs tabular-nums text-muted-foreground">
          {t(language, "capture.library.count", { shown: Math.min(visible, matching.length), total: matching.length })}
        </p>
        {matching.length ? (
          <ul className="m-0 list-none divide-y divide-border rounded-md border border-border p-0">
            {matching.slice(0, visible).map((item, index) => (
              <li key={item.id || index} className="flex flex-wrap items-center justify-between gap-2 p-2.5">
                <span className="min-w-0 flex-1 break-all text-sm font-medium">{item.name}</span>
                <Badge variant="muted">{t(language, item.indexed ? "capture.library.indexed" : "capture.library.ingested")}</Badge>
                {item.id ? (
                  <Button
                    size="sm"
                    variant="outline"
                    aria-label={t(language, "capture.library.open", { name: item.name })}
                    onClick={() => setOpened({ id: item.id, name: item.name })}
                  >
                    {t(language, "capture.library.preview")}
                  </Button>
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">{t(language, "capture.library.noMatch")}</p>
        )}
        {matching.length > visible ? (
          <Button variant="outline" className="w-full" onClick={() => setVisible(visible + PAGE_SIZE)}>
            {t(language, "capture.library.more")}
          </Button>
        ) : null}
        {documents.length >= DOCUMENT_FETCH_LIMIT ? (
          <p className="text-xs text-muted-foreground">{t(language, "capture.library.capped", { count: DOCUMENT_FETCH_LIMIT })}</p>
        ) : null}
        <p className="text-sm text-muted-foreground">{t(language, "capture.files.completed")}</p>
      </>
    );
  };

  return (
    <Card className="data-panel overflow-hidden">
      <CardHeader>
        <CardTitle>{t(language, "capture.files.uploaded")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">{body()}</CardContent>
      {opened ? (
        <React.Suspense fallback={<p role="status" className="px-6 pb-4 text-sm text-muted-foreground">{t(language, "ui.loading")}</p>}>
          <SourceChunkModal language={language} nodeId={opened.id} fallbackTitle={opened.name} onClose={() => setOpened(null)} />
        </React.Suspense>
      ) : null}
    </Card>
  );
}
