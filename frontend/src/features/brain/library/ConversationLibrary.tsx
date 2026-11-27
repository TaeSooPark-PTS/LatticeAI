import * as React from "react";
import "@/i18n/brain";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Download, Loader2, Search, Trash2 } from "lucide-react";

import { latticeApi } from "@/api/client";
import { EmptyState } from "@/components/primitives";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { t, type Language } from "@/i18n";
import { buildConversationSummaries } from "../brainData";
import { formatConversationTime } from "../HomePanels";
import { conversationMarkdown, downloadMarkdown, markdownFileName } from "./conversationExport";
import { buildSearchMatches, SEARCH_GROUP_LIMIT } from "./searchResults";

type Row = { id: string; title: string; meta: string; snippet?: string };

export default function ConversationLibrary({
  language,
  busyId,
  onResume,
  onDelete,
  onBack,
}: {
  language: Language;
  busyId: string | null;
  onResume: (id: string) => void;
  onDelete: (id: string) => void;
  onBack: () => void;
}) {
  const [draft, setDraft] = React.useState("");
  const [query, setQuery] = React.useState("");
  const [confirmingId, setConfirmingId] = React.useState<string | null>(null);
  const [exportingId, setExportingId] = React.useState<string | null>(null);
  const [exportError, setExportError] = React.useState<string | null>(null);
  const inputRef = React.useRef<HTMLInputElement>(null);
  React.useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const historyQ = useQuery({ queryKey: ["chatHistory"], queryFn: latticeApi.chatHistory });
  const searchQ = useQuery({
    queryKey: ["chatHistory", "search", query],
    queryFn: () => latticeApi.searchHistory(query),
    enabled: query !== "",
  });
  const conversations = React.useMemo(() => buildConversationSummaries(historyQ.data?.data), [historyQ.data]);

  const searching = query !== "";
  const active = searching ? searchQ : historyQ;
  const failed = active.data?.ok === false;
  const matches = searching ? buildSearchMatches(searchQ.data?.data, query, conversations) : [];
  const meta = (count: string, updatedAt?: number) =>
    updatedAt ? `${count} · ${formatConversationTime(language, updatedAt)}` : count;
  const rows: Row[] = searching
    ? matches.map((item) => ({
      id: item.id,
      title: item.title,
      snippet: item.snippet,
      meta: meta(t(language, "brain.history.library.matches", { count: item.matches }), item.updatedAt),
    }))
    : conversations.map((item) => ({
      id: item.id,
      title: item.title,
      meta: meta(t(language, "brain.history.messages", { count: item.messageCount }), item.updatedAt),
    }));

  const exportConversation = async (row: Row) => {
    setExportingId(row.id);
    setExportError(null);
    const result = await latticeApi.conversation(row.id);
    setExportingId(null);
    const markdown = result.ok ? conversationMarkdown(language, row.title, result.data) : null;
    if (!markdown) {
      const reason = result.error || t(language, "brain.history.emptyConversation");
      setExportError(t(language, "brain.history.library.exportFailed", { reason }));
      return;
    }
    downloadMarkdown(markdownFileName(row.title), markdown);
  };

  const clearSearch = () => {
    setDraft("");
    setQuery("");
  };

  return (
    <section className="w-full space-y-3 text-left" aria-label={t(language, "brain.history.library.title")}>
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" onClick={onBack}>
          <ArrowLeft className="h-4 w-4" aria-hidden="true" /> {t(language, "brain.history.library.back")}
        </Button>
        <h2 className="text-sm font-semibold">{t(language, "brain.history.library.title")}</h2>
      </div>
      <form
        role="search"
        className="flex gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          setQuery(draft.trim());
        }}
      >
        <Input
          ref={inputRef}
          aria-label={t(language, "brain.history.library.searchLabel")}
          placeholder={t(language, "brain.history.library.searchLabel")}
          value={draft}
          onChange={(event) => {
            setDraft(event.target.value);
            if (!event.target.value) setQuery("");
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter" && event.nativeEvent.isComposing) event.preventDefault();
          }}
        />
        <Button type="submit" variant="outline" disabled={!draft.trim()}>
          <Search className="h-4 w-4" aria-hidden="true" /> {t(language, "brain.history.library.search")}
        </Button>
      </form>
      <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
        <span role="status" className="tabular-nums">
          {active.isPending || failed ? "" : t(language, "brain.history.library.count", { count: rows.length })}
        </span>
        {searching ? (
          <Button variant="ghost" size="sm" onClick={clearSearch}>{t(language, "brain.history.library.clear")}</Button>
        ) : null}
      </div>
      {searching && matches.length >= SEARCH_GROUP_LIMIT ? (
        <p className="text-xs text-muted-foreground">{t(language, "brain.history.library.capped", { count: SEARCH_GROUP_LIMIT })}</p>
      ) : null}
      {exportError ? <p role="alert" className="text-sm text-destructive">{exportError}</p> : null}

      {active.isPending ? (
        <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> {t(language, "ui.loading")}
        </p>
      ) : failed ? (
        <div className="space-y-2">
          <EmptyState title={t(language, "brain.history.library.unavailable")} detail={active.data?.error} />
          <Button variant="outline" size="sm" onClick={() => void active.refetch()}>
            {t(language, "brain.history.library.retry")}
          </Button>
        </div>
      ) : !rows.length ? (
        <EmptyState
          title={t(language, searching ? "brain.history.library.noMatchTitle" : "brain.history.library.emptyTitle")}
          detail={t(language, searching ? "brain.history.library.noMatchDetail" : "brain.history.library.emptyDetail")}
        />
      ) : (
        <ul className="divide-y divide-border rounded-md border border-border">
          {rows.map((row) => {
            const busy = busyId === row.id;
            const confirming = confirmingId === row.id;
            return (
              <li key={row.id} className="flex items-center gap-1 p-1.5 [content-visibility:auto]">
                <button
                  type="button"
                  disabled={busy}
                  className="min-w-0 flex-1 rounded-md px-2 py-1.5 text-left hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
                  aria-label={t(language, "brain.history.resumeAria", { title: row.title })}
                  onClick={() => onResume(row.id)}
                >
                  <span className="block truncate text-sm font-medium">{row.title}</span>
                  {row.snippet ? <span className="block truncate text-xs text-muted-foreground">{row.snippet}</span> : null}
                  <span className="block text-xs tabular-nums text-muted-foreground">{row.meta}</span>
                </button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={busy || exportingId !== null}
                  aria-label={t(language, "brain.history.library.exportAria", { title: row.title })}
                  onClick={() => void exportConversation(row)}
                >
                  {exportingId === row.id
                    ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                    : <Download className="h-3.5 w-3.5" aria-hidden="true" />}
                </Button>
                <Button
                  variant={confirming ? "destructive" : "ghost"}
                  size="sm"
                  disabled={busy}
                  aria-label={
                    confirming
                      ? t(language, "brain.history.deleteConfirm")
                      : t(language, "brain.history.deleteAria", { title: row.title })
                  }
                  onClick={() => {
                    if (!confirming) {
                      setConfirmingId(row.id);
                      return;
                    }
                    setConfirmingId(null);
                    onDelete(row.id);
                  }}
                  onBlur={() => setConfirmingId(null)}
                >
                  <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                  {confirming ? <span>{t(language, "brain.history.deleteConfirm")}</span> : null}
                </Button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
