import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ImagePlus, MessageSquare, Send, Trash2 } from "lucide-react";
import { latticeApi } from "@/api/client";
import { DataPanel, EmptyState, EntityList, JsonView, SourceBadge } from "@/components/primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { asArray } from "@/lib/utils";

type Msg = { role?: string; content?: string; timestamp?: string };

function fileToDataUrl(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

export function AskPage() {
  const qc = useQueryClient();
  const history = useQuery({ queryKey: ["chatHistory"], queryFn: latticeApi.chatHistory });
  const models = useQuery({ queryKey: ["models"], queryFn: latticeApi.models });
  const [conversationId, setConversationId] = React.useState<string | null>(null);
  const conversation = useQuery({
    queryKey: ["conversation", conversationId],
    queryFn: () => latticeApi.conversation(conversationId || ""),
    enabled: !!conversationId,
  });
  const [messages, setMessages] = React.useState<Msg[]>([]);
  const [draft, setDraft] = React.useState("");
  const [imageData, setImageData] = React.useState<string | null>(null);
  const [trace, setTrace] = React.useState<unknown>(null);
  const [streaming, setStreaming] = React.useState(false);

  React.useEffect(() => {
    if (conversation.data?.ok) setMessages(asArray<Msg>((conversation.data.data as Record<string, unknown>).messages || conversation.data.data));
  }, [conversation.data]);

  const send = async () => {
    const message = draft.trim();
    if (!message || streaming) return;
    setDraft("");
    setMessages((items) => [...items, { role: "user", content: message }, { role: "assistant", content: "" }]);
    setStreaming(true);
    try {
      const result = await latticeApi.streamChat(
        { message, conversation_id: conversationId || undefined, image_data: imageData || undefined },
        {
          onChunk: (_delta, fullText) => {
            setMessages((items) => {
              const next = [...items];
              const last = next[next.length - 1] || { role: "assistant" };
              next[next.length - 1] = { ...last, role: "assistant", content: fullText };
              return next;
            });
          },
          onTrace: setTrace,
        },
      );
      if (result.error) {
        setMessages((items) => {
          const next = [...items];
          next[next.length - 1] = { role: "assistant", content: `Unavailable: ${result.error}` };
          return next;
        });
      }
    } finally {
      setStreaming(false);
      setImageData(null);
      await qc.invalidateQueries({ queryKey: ["chatHistory"] });
    }
  };

  const deleteMutation = useMutation({
    mutationFn: (id: string) => latticeApi.deleteConversation(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["chatHistory"] }),
  });

  return (
    <div className="grid min-h-[calc(100vh-7rem)] gap-4 xl:grid-cols-[18rem_minmax(0,1fr)_22rem]">
      <Card className="overflow-hidden">
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><MessageSquare className="h-4 w-4" /> Conversations</CardTitle>
          <CardDescription>Durable history from the backend conversation store.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          <SourceBadge result={history.data} />
          {asArray<Record<string, unknown>>(history.data?.data).length ? asArray<Record<string, unknown>>(history.data?.data).map((item) => (
            <button
              key={String(item.id)}
              onClick={() => setConversationId(String(item.id))}
              className="block w-full rounded-md border border-border bg-background p-3 text-left text-sm transition hover:bg-muted"
            >
              <div className="font-medium">{String(item.title || item.id)}</div>
              <div className="mt-1 flex items-center justify-between gap-2 text-xs text-muted-foreground">
                <span>{String(item.updated_at || "")}</span>
                <Trash2
                  className="h-3.5 w-3.5"
                  onClick={(e) => {
                    e.stopPropagation();
                    deleteMutation.mutate(String(item.id));
                  }}
                />
              </div>
            </button>
          )) : <EmptyState title="No conversations" detail="Start a new exchange or sign in to load history." />}
        </CardContent>
      </Card>

      <section className="flex min-h-[40rem] flex-col rounded-lg border border-border bg-card">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border p-4">
          <div>
            <h1 className="text-xl font-semibold">Ask</h1>
            <p className="text-sm text-muted-foreground">Streams through `/chat`; no local answer is fabricated when the model is unavailable.</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="muted">{String((models.data?.data as Record<string, unknown>)?.current || "no model loaded")}</Badge>
            <SourceBadge result={models.data} />
          </div>
        </div>
        <div className="scrollbar-thin flex-1 space-y-3 overflow-auto p-4">
          {messages.length ? messages.map((msg, index) => (
            <div key={index} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
              <div className={`max-w-[78%] rounded-lg border p-3 text-sm ${msg.role === "user" ? "border-primary/30 bg-primary/15" : "border-border bg-background"}`}>
                <div className="mb-1 text-xs uppercase text-muted-foreground">{msg.role || "message"}</div>
                <div className="whitespace-pre-wrap">{msg.content}</div>
              </div>
            </div>
          )) : <EmptyState title="Ready" detail="Ask a question once the backend and model are available." />}
        </div>
        <div className="border-t border-border p-4">
          {imageData ? <Badge variant="success" className="mb-2">image attached</Badge> : null}
          <Textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send();
              }
            }}
            placeholder="Ask the brain..."
          />
          <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
            <label className="inline-flex h-9 cursor-pointer items-center gap-2 rounded-md border border-border px-3 text-sm hover:bg-muted">
              <ImagePlus className="h-4 w-4" />
              Attach image
              <input
                type="file"
                accept="image/*"
                className="sr-only"
                onChange={async (e) => {
                  const file = e.target.files?.[0];
                  if (file) setImageData(await fileToDataUrl(file));
                }}
              />
            </label>
            <Button disabled={!draft.trim() || streaming} onClick={() => void send()}>
              <Send className="h-4 w-4" /> Send
            </Button>
          </div>
        </div>
      </section>

      <aside className="space-y-4">
        <ContextPreview question={draft || [...messages].reverse().find((m: Msg) => m.role === "user")?.content || ""} trace={trace} />
      </aside>
    </div>
  );
}

function ContextPreview({ question, trace }: { question: string; trace: unknown }) {
  const hybrid = useQuery({
    queryKey: ["askHybrid", question],
    queryFn: () => latticeApi.hybridSearch(question),
    enabled: question.trim().length > 2,
  });
  const graph = useQuery({ queryKey: ["graph"], queryFn: latticeApi.graph });
  return (
    <>
      <DataPanel title="Retrieval preview" result={hybrid.data}>
        {(data) => <EntityList items={(data as Record<string, unknown>).matches || data} titleKey="title" metaKey="type" limit={5} />}
      </DataPanel>
      <DataPanel title="Graph context" result={graph.data}>
        {(data) => <EntityList items={(data as Record<string, unknown>).nodes} titleKey="title" metaKey="type" limit={5} />}
      </DataPanel>
      <Card>
        <CardHeader>
          <CardTitle>Why this context</CardTitle>
          <CardDescription>Trace emitted by `/chat` when the backend includes it.</CardDescription>
        </CardHeader>
        <CardContent>{trace ? <JsonView value={trace} /> : <EmptyState title="No trace yet" />}</CardContent>
      </Card>
    </>
  );
}
