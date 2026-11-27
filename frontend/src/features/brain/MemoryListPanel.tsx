import * as React from "react";
import "@/i18n/brain";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { NotebookPen, Search, Trash2 } from "lucide-react";

import { latticeApi } from "@/api/client";
import { MEMORY_KINDS, type MemoryKind, type WorkspaceMemory } from "@/api/memory";
import { EmptyState } from "@/components/primitives";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { t, type Language } from "@/i18n";
import { useAppStore } from "@/store/appStore";
import { asArray } from "@/lib/utils";

export function MemoryListPanel() {
  const language = useAppStore((state) => state.language);
  const queryClient = useQueryClient();
  const [input, setInput] = React.useState("");
  const [term, setTerm] = React.useState("");
  const [content, setContent] = React.useState("");
  const [kind, setKind] = React.useState<MemoryKind>("long_term");
  const [confirmId, setConfirmId] = React.useState<string | null>(null);
  const [returnFocusId, setReturnFocusId] = React.useState<string | null>(null);

  const list = useQuery({
    queryKey: ["workspaceMemories", term],
    queryFn: () => (term ? latticeApi.searchWorkspaceMemories(term) : latticeApi.workspaceMemories()),
  });
  const refresh = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ["workspaceMemories"] }),
      queryClient.invalidateQueries({ queryKey: ["memoryManager"] }),
    ]);
  const add = useMutation({
    mutationFn: () => latticeApi.addWorkspaceMemory(kind, content.trim()),
    onSuccess: async (result) => {
      if (!result.ok) return;
      setContent("");
      await refresh();
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => latticeApi.deleteWorkspaceMemory(id),
    onSuccess: async (result) => {
      if (!result.ok) return;
      setConfirmId(null);
      await refresh();
    },
  });

  const rows = asArray<WorkspaceMemory>(list.data?.data?.memories);

  function askDelete(id: string) {
    remove.reset();
    setReturnFocusId(null);
    setConfirmId(id);
  }

  function cancelDelete(id: string) {
    setReturnFocusId(id);
    setConfirmId(null);
  }

  return (
    <Card data-testid="memory-list-panel">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <NotebookPen className="h-4 w-4" aria-hidden="true" />
          {t(language, "brain.memories.title")}
        </CardTitle>
        <CardDescription>{t(language, "brain.memories.detail")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <form
          className="space-y-2"
          onSubmit={(event) => {
            event.preventDefault();
            add.mutate();
          }}
        >
          <Textarea
            aria-label={t(language, "brain.memories.add.label")}
            placeholder={t(language, "brain.memories.add.placeholder")}
            value={content}
            onChange={(event) => setContent(event.target.value)}
          />
          <div className="flex flex-wrap items-center gap-2">
            <select
              className="h-10 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              aria-label={t(language, "brain.memories.kind.label")}
              value={kind}
              onChange={(event) => setKind(event.target.value as MemoryKind)}
            >
              {MEMORY_KINDS.map((item) => (
                <option key={item} value={item}>{kindLabel(language, item)}</option>
              ))}
            </select>
            <Button type="submit" disabled={!content.trim() || add.isPending}>
              {t(language, add.isPending ? "brain.memories.add.pending" : "brain.memories.add.action")}
            </Button>
          </div>
          {add.data && !add.data.ok ? (
            <p role="alert" className="text-sm text-destructive">
              {t(language, "brain.memories.add.failed", { reason: String(add.data.error) })}
            </p>
          ) : null}
        </form>

        <form
          role="search"
          className="flex flex-col gap-2 border-t border-border pt-4 sm:flex-row"
          onSubmit={(event) => {
            event.preventDefault();
            setTerm(input.trim());
          }}
        >
          <Input
            type="search"
            aria-label={t(language, "brain.memories.search.label")}
            placeholder={t(language, "brain.memories.search.placeholder")}
            value={input}
            onChange={(event) => {
              setInput(event.target.value);
              if (!event.target.value.trim()) setTerm("");
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter" && event.nativeEvent.isComposing) event.preventDefault();
            }}
          />
          <Button type="submit" variant="outline">
            <Search className="h-4 w-4" aria-hidden="true" />
            {t(language, "brain.memories.search.action")}
          </Button>
        </form>

        {list.isLoading ? (
          <p role="status" className="text-sm text-muted-foreground">{t(language, "ui.loading")}</p>
        ) : !list.data?.ok ? (
          <div role="alert" className="flex flex-wrap items-center gap-2 text-sm">
            <span>{t(language, "brain.memories.loadFailed", { reason: String(list.data?.error) })}</span>
            <Button type="button" size="sm" variant="outline" onClick={() => void list.refetch()}>
              {t(language, "common.retry")}
            </Button>
          </div>
        ) : !rows.length ? (
          <EmptyState
            title={t(language, term ? "brain.memories.noMatch" : "brain.memories.empty")}
            detail={t(language, term ? "brain.memories.noMatchDetail" : "brain.memories.emptyDetail")}
          />
        ) : (
          <ul className="divide-y divide-border rounded-md border border-border">
            {rows.map((memory) => (
              <li key={memory.id} className="space-y-2 p-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="whitespace-pre-wrap break-words text-sm">{memory.content}</p>
                    <p className="mt-1 text-xs tabular-nums text-muted-foreground">
                      {kindLabel(language, memory.kind)}
                      {memory.created_at ? ` · ${memory.created_at.slice(0, 10)}` : ""}
                    </p>
                  </div>
                  {confirmId === memory.id ? null : (
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      autoFocus={returnFocusId === memory.id}
                      aria-label={t(language, "brain.memories.delete.aria", { content: memory.content.slice(0, 60) })}
                      disabled={remove.isPending}
                      onClick={() => askDelete(memory.id)}
                    >
                      <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                      {t(language, "brain.memories.delete.action")}
                    </Button>
                  )}
                </div>
                {confirmId === memory.id ? (
                  <div role="group" aria-label={t(language, "brain.memories.delete.question")} className="space-y-2 rounded-md border border-destructive/50 p-3">
                    <p className="text-sm font-medium">{t(language, "brain.memories.delete.question")}</p>
                    <p className="text-xs text-muted-foreground">
                      {t(language, memory.graph_node_id ? "brain.memories.delete.graphCopy" : "brain.memories.delete.permanent")}
                    </p>
                    <div className="flex flex-wrap gap-2">
                      <Button type="button" size="sm" variant="destructive" disabled={remove.isPending} onClick={() => remove.mutate(memory.id)}>
                        {t(language, remove.isPending ? "brain.memories.delete.pending" : "brain.memories.delete.confirm")}
                      </Button>
                      <Button type="button" size="sm" variant="outline" autoFocus disabled={remove.isPending} onClick={() => cancelDelete(memory.id)}>
                        {t(language, "ui.cancel")}
                      </Button>
                    </div>
                    {remove.data && !remove.data.ok ? (
                      <p role="alert" className="text-sm text-destructive">
                        {t(language, "brain.memories.delete.failed", { reason: String(remove.data.error) })}
                      </p>
                    ) : null}
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function kindLabel(language: Language, kind: string) {
  const key = `brain.memories.kind.${kind}`;
  const label = t(language, key);
  return label === key ? kind : label;
}
