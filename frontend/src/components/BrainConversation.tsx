import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ImagePlus, MessageSquare, Plus, Send, Trash2 } from "lucide-react";
import { latticeApi } from "@/api/client";
import { EmptyState, EntityList, SourceBadge, StructuredView } from "@/components/primitives";
import { LivingBrain, type BrainActivity, type BrainVitals } from "@/components/LivingBrain";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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

function newConversationId() {
  const suffix = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.round(Math.random() * 10000)}`;
  return `brain-${suffix}`;
}

function currentModelName(data: unknown) {
  const record = data && typeof data === "object" ? data as Record<string, unknown> : {};
  if (typeof record.current === "string" && record.current) return record.current;
  const loaded = asArray<Record<string, unknown>>(record.loaded);
  const firstLoaded = loaded.find((item) => item.id || item.name || item.model_id);
  if (firstLoaded) return String(firstLoaded.name || firstLoaded.id || firstLoaded.model_id);
  return null;
}

function usageNumber(data: unknown, key: string) {
  const record = data && typeof data === "object" ? data as Record<string, unknown> : {};
  const usage = record.usage && typeof record.usage === "object" ? record.usage as Record<string, unknown> : {};
  const value = Number(usage[key] ?? record[key]);
  return Number.isFinite(value) ? value : null;
}

export function BrainConversation({ className }: { className?: string }) {
  const qc = useQueryClient();
  const history = useQuery({ queryKey: ["chatHistory"], queryFn: latticeApi.chatHistory });
  const models = useQuery({ queryKey: ["models"], queryFn: latticeApi.models });
  const memory = useQuery({ queryKey: ["memoryManager"], queryFn: latticeApi.memoryManager });
  const agentRuntime = useQuery({ queryKey: ["agentRuntime"], queryFn: latticeApi.agentRuntime, refetchInterval: 12000 });
  const [conversationId, setConversationId] = React.useState<string | null>(null);
  const [selectedConversationId, setSelectedConversationId] = React.useState<string | null>(null);
  const conversation = useQuery({
    queryKey: ["conversation", selectedConversationId],
    queryFn: () => latticeApi.conversation(selectedConversationId || ""),
    enabled: !!selectedConversationId,
  });
  const [messages, setMessages] = React.useState<Msg[]>([]);
  const [draft, setDraft] = React.useState("");
  const [imageData, setImageData] = React.useState<string | null>(null);
  const [trace, setTrace] = React.useState<unknown>(null);
  const [streaming, setStreaming] = React.useState(false);

  React.useEffect(() => {
    if (conversation.data?.ok) {
      setMessages(asArray<Msg>((conversation.data.data as Record<string, unknown>).messages || conversation.data.data));
    }
  }, [conversation.data]);

  const send = async () => {
    const message = draft.trim();
    if (!message || streaming) return;
    const activeConversationId = conversationId || newConversationId();
    if (!conversationId) setConversationId(activeConversationId);
    setDraft("");
    setMessages((items) => [...items, { role: "user", content: message }, { role: "assistant", content: "" }]);
    setStreaming(true);
    try {
      const result = await latticeApi.streamChat(
        { message, conversation_id: activeConversationId, image_data: imageData || undefined },
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
      await qc.invalidateQueries({ queryKey: ["memoryManager"] });
    }
  };

  const deleteMutation = useMutation({
    mutationFn: (id: string) => latticeApi.deleteConversation(id),
    onSuccess: async (_result, id) => {
      if (conversationId === id) {
        setConversationId(null);
        setSelectedConversationId(null);
        setMessages([]);
      }
      await qc.invalidateQueries({ queryKey: ["chatHistory"] });
    },
  });

  const historyItems = asArray<Record<string, unknown>>(history.data?.data);
  const memoryData = memory.data?.data;
  const runtimeData = agentRuntime.data?.data as Record<string, unknown> | undefined;
  const runs = asArray<Record<string, unknown>>(runtimeData?.runs);
  const activity: BrainActivity =
    streaming ? "thinking" :
    imageData ? "recalling" :
    runs.some((run) => String(run.status || run.state || "").match(/running|active|queued/i)) ? "acting" :
    draft.trim().length > 2 ? "listening" :
    trace ? "recalling" :
    "idle";
  const vitals: BrainVitals = {
    connected: Boolean(models.data?.ok),
    memories: usageNumber(memoryData, "total_items") ?? asArray((memoryData as Record<string, unknown> | undefined)?.sources).length,
    knowledge: usageNumber(memoryData, "sources") ?? asArray((memoryData as Record<string, unknown> | undefined)?.tiers).length,
    conversations: historyItems.length,
    model: currentModelName(models.data?.data),
  };

  return (
    <div className={className}>
      <div className="brain-conversation-grid">
        <aside className="brain-presence-column">
          <LivingBrain activity={activity} vitals={vitals} />
          <RecentConversations
            conversations={historyItems}
            result={history.data}
            activeId={conversationId}
            onNew={() => {
              setConversationId(null);
              setSelectedConversationId(null);
              setMessages([]);
              setTrace(null);
            }}
            onSelect={(id) => {
              setConversationId(id);
              setSelectedConversationId(id);
              setTrace(null);
            }}
            onDelete={(id) => deleteMutation.mutate(id)}
          />
          <MemoryNearby question={draft || [...messages].reverse().find((msg) => msg.role === "user")?.content || ""} trace={trace} />
        </aside>

        <section className="brain-chat-panel premium-surface" aria-label="Conversation with Lattice Brain">
          <div className="brain-chat-head">
            <div>
              <div className="brain-chat-kicker"><MessageSquare className="h-4 w-4" /> Conversation</div>
              <h1>Talk to your Brain.</h1>
            </div>
            <div className="brain-chat-model">
              <Badge variant="muted">{currentModelName(models.data?.data) || "no model loaded"}</Badge>
              <SourceBadge result={models.data} />
            </div>
          </div>

          <div className="brain-message-stream soft-scrollbar">
            {messages.length ? messages.map((msg, index) => (
              <div key={`${msg.role || "message"}-${index}`} className={`brain-message-row ${msg.role === "user" ? "from-user" : "from-brain"}`}>
                <div className="brain-message-bubble">
                  <div className="brain-message-role">{msg.role === "user" ? "You" : "Brain"}</div>
                  <div className="whitespace-pre-wrap">{msg.content}</div>
                </div>
              </div>
            )) : (
              <div className="brain-empty-conversation">
                <EmptyState
                  title="What should we think through?"
                  detail="Bring a question, a project, or a loose thought. Lattice will answer through your private memory when a model is ready."
                />
              </div>
            )}
          </div>

          <div className="brain-composer">
            {imageData ? <Badge variant="success" className="mb-2">image attached</Badge> : null}
            <Textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void send();
                }
              }}
              placeholder="Ask the Brain anything..."
            />
            <div className="brain-composer-actions">
              <label className="inline-flex h-9 cursor-pointer items-center gap-2 rounded-md border border-border px-3 text-sm hover:bg-muted">
                <ImagePlus className="h-4 w-4" />
                Image
                <input
                  type="file"
                  accept="image/*"
                  className="sr-only"
                  onChange={async (event) => {
                    const file = event.target.files?.[0];
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
      </div>
    </div>
  );
}

function RecentConversations({
  conversations,
  result,
  activeId,
  onNew,
  onSelect,
  onDelete,
}: {
  conversations: Array<Record<string, unknown>>;
  result?: Parameters<typeof SourceBadge>[0]["result"];
  activeId: string | null;
  onNew: () => void;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  return (
    <section className="brain-side-panel">
      <div className="brain-side-head">
        <div>
          <h3>Recent conversations</h3>
          <SourceBadge result={result} />
        </div>
        <Button variant="outline" size="sm" onClick={onNew}><Plus className="h-4 w-4" /> New</Button>
      </div>
      <div className="brain-conversation-list soft-scrollbar">
        {conversations.length ? conversations.slice(0, 8).map((item) => {
          const id = String(item.id || item.conversation_id || "");
          return (
            <div key={id} className={`brain-conversation-item ${activeId === id ? "is-active" : ""}`}>
              <button onClick={() => onSelect(id)} className="min-w-0 text-left">
                <span>{String(item.title || id || "Conversation")}</span>
                <small>{String(item.updated_at || item.started_at || "")}</small>
              </button>
              <button className="brain-delete-button" onClick={() => onDelete(id)} aria-label="Delete conversation">
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          );
        }) : <EmptyState title="No conversations yet" detail="New exchanges will appear here." />}
      </div>
    </section>
  );
}

function MemoryNearby({ question, trace }: { question: string; trace: unknown }) {
  const hybrid = useQuery({
    queryKey: ["brainNearbyMemory", question],
    queryFn: () => latticeApi.hybridSearch(question),
    enabled: question.trim().length > 2,
  });
  return (
    <section className="brain-side-panel">
      <div className="brain-side-head">
        <div>
          <h3>Memory nearby</h3>
          <SourceBadge result={hybrid.data} />
        </div>
      </div>
      {hybrid.data?.ok ? (
        <EntityList items={(hybrid.data.data as Record<string, unknown>).matches || hybrid.data.data} titleKey="title" metaKey="type" limit={4} />
      ) : trace ? (
        <StructuredView value={trace} limit={4} />
      ) : (
        <EmptyState title="Quiet for now" detail="Relevant memory wakes up as the conversation gets specific." />
      )}
    </section>
  );
}
