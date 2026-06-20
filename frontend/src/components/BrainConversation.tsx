import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ImagePlus, MessageSquare, Plus, Send, Trash2 } from "lucide-react";
import { latticeApi } from "@/api/client";
import { EmptyState, EntityList, SourceBadge, StructuredView } from "@/components/primitives";
import { FeedbackState } from "@/components/FeedbackState";
import { type BrainState, LivingBrain } from "@/components/LivingBrain";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { asArray } from "@/lib/utils";
import { t, type Language } from "@/i18n";
import { useAppStore } from "@/store/appStore";

type Citation = { id: string; source: string; title: string; snippet: string; score: number | null };
type Msg = { role?: string; content?: string; timestamp?: string; citations?: Citation[] };
type BrainActivity = BrainState;
type BrainVitals = {
  connected: boolean;
  memories: number;
  knowledge: number;
  conversations: number;
  model: string | null;
};

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

// Pull human-readable citation records out of the heterogeneous streaming trace.
// The backend trace shape varies, so we defensively scan the common containers
// (matches / sources / retrieved / context / chunks) for source-like records.
function extractCitations(trace: unknown): Citation[] {
  if (!trace || typeof trace !== "object") return [];
  const record = trace as Record<string, unknown>;
  const containerKeys = ["matches", "sources", "retrieved", "context", "chunks", "citations", "evidence"];
  let rows: Array<Record<string, unknown>> = [];
  for (const key of containerKeys) {
    const candidate = asArray<Record<string, unknown>>(record[key]);
    if (candidate.length) {
      rows = candidate;
      break;
    }
  }
  if (!rows.length && Array.isArray(trace)) rows = trace as Array<Record<string, unknown>>;

  const seen = new Set<string>();
  const citations: Citation[] = [];
  for (const row of rows) {
    if (!row || typeof row !== "object") continue;
    const title = String(row.title || row.name || row.heading || row.label || row.id || "").trim();
    const source = String(row.source || row.type || row.origin || row.kind || row.provider || "memory").trim();
    const snippet = String(row.snippet || row.text || row.content || row.summary || row.excerpt || "").trim();
    if (!title && !snippet) continue;
    const scoreRaw = Number(row.score ?? row.relevance ?? row.similarity);
    const score = Number.isFinite(scoreRaw) ? Math.round((scoreRaw <= 1 ? scoreRaw * 100 : scoreRaw)) : null;
    const id = String(row.id || `${source}:${title}:${snippet.slice(0, 24)}`);
    if (seen.has(id)) continue;
    seen.add(id);
    citations.push({ id, source: source || "memory", title: title || snippet.slice(0, 48), snippet, score });
    if (citations.length >= 6) break;
  }
  return citations;
}

// Anchor citation chips after the first N sentences (conservative heuristic).
// Each segment carries an optional citation index so chips stay tied to text.
function splitWithCitations(content: string, citationCount: number): Array<{ text: string; citation: number | null }> {
  if (!citationCount || !content) return [{ text: content, citation: null }];
  const sentences = content.match(/[^.!?。！？\n]+[.!?。！？]?\s*/g);
  if (!sentences || sentences.length <= 1) return [{ text: content, citation: 0 }];
  const segments: Array<{ text: string; citation: number | null }> = [];
  sentences.forEach((sentence, index) => {
    const citation = index < citationCount ? index : null;
    segments.push({ text: sentence, citation });
  });
  return segments;
}

export function BrainConversation({ className }: { className?: string }) {
  const language = useAppStore((state) => state.language);
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
          onTrace: (incoming) => {
            setTrace(incoming);
            const citations = extractCitations(incoming);
            if (citations.length) {
              setMessages((items) => {
                const next = [...items];
                const last = next[next.length - 1];
                if (last && last.role === "assistant") {
                  next[next.length - 1] = { ...last, citations };
                }
                return next;
              });
            }
          },
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
        <section className="brain-presence-column" aria-label="Living Brain presence">
          <LivingBrain state={activity} size="normal" />
        </section>

        <section className="brain-chat-panel premium-surface" aria-label="Conversation with Lattice Brain">
          <div className="brain-chat-head">
            <div>
              <div className="brain-chat-kicker"><MessageSquare className="h-4 w-4" /> Conversation</div>
              <h1>Talk to your Brain.</h1>
            </div>
            <div className="brain-chat-model">
              <Badge variant="muted">{currentModelName(models.data?.data) || "model readying"}</Badge>
              <SourceBadge result={models.data} />
            </div>
          </div>

          <div className="brain-message-stream soft-scrollbar">
            {messages.length ? messages.map((msg, index) => (
              <div key={`${msg.role || "message"}-${index}`} className={`brain-message-row ${msg.role === "user" ? "from-user" : "from-brain"}`}>
                <div className="brain-message-bubble">
                  <div className="brain-message-role">{msg.role === "user" ? "You" : "Brain"}</div>
                  {msg.role === "user" ? (
                    <div className="whitespace-pre-wrap">{msg.content}</div>
                  ) : (
                    <MessageWithCitations
                      language={language}
                      content={msg.content || ""}
                      citations={msg.citations || []}
                    />
                  )}
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

        <aside className="brain-context-column" aria-label="Conversation memory">
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
      </div>
    </div>
  );
}

// Renders assistant text with inline, keyboard-accessible citation chips that are
// linked to a proof list below. Selecting a chip highlights and reveals its source.
function MessageWithCitations({
  language,
  content,
  citations,
}: {
  language: Language;
  content: string;
  citations: Citation[];
}) {
  const [activeId, setActiveId] = React.useState<string | null>(null);
  const [announce, setAnnounce] = React.useState("");
  const listRef = React.useRef<HTMLOListElement | null>(null);

  if (!citations.length) {
    return <div className="whitespace-pre-wrap">{content}</div>;
  }

  const segments = splitWithCitations(content, citations.length);

  const focusListItem = (id: string) => {
    const node = listRef.current?.querySelector<HTMLLIElement>(`[data-citation-target="${id}"]`);
    node?.focus?.();
    node?.scrollIntoView?.({ block: "nearest" });
  };

  const toggle = (citation: Citation) => {
    setActiveId((prev) => {
      const next = prev === citation.id ? null : citation.id;
      if (next) {
        const idx = citations.findIndex((c) => c.id === citation.id) + 1;
        setAnnounce(t(language, "brain.citation.announce.open", { num: idx, title: citation.title }));
        focusListItem(citation.id);
      } else {
        setAnnounce(t(language, "brain.citation.announce.close"));
      }
      return next;
    });
  };

  const onChipKey = (event: React.KeyboardEvent<HTMLSpanElement>, citation: Citation) => {
    if (event.key === "Enter" || event.key === " " || event.key === "Spacebar") {
      event.preventDefault();
      toggle(citation);
    } else if (event.key === "Escape" && activeId) {
      event.preventDefault();
      setActiveId(null);
      setAnnounce(t(language, "brain.citation.announce.close"));
    }
  };

  return (
    <div className="brain-message-cited">
      <div className="whitespace-pre-wrap brain-message-text">
        {segments.map((segment, index) => {
          const citation = segment.citation != null ? citations[segment.citation] : null;
          const num = segment.citation != null ? segment.citation + 1 : 0;
          return (
            <React.Fragment key={index}>
              {segment.text}
              {citation ? (
                <span
                  role="button"
                  tabIndex={0}
                  className={`citation-chip ${activeId === citation.id ? "is-active" : ""}`}
                  data-citation-id={citation.id}
                  aria-pressed={activeId === citation.id}
                  aria-label={t(language, "brain.citation.chip.long", { num, title: citation.title })}
                  onClick={() => toggle(citation)}
                  onKeyDown={(event) => onChipKey(event, citation)}
                >
                  {t(language, "brain.citation.chip", { num })}
                </span>
              ) : null}
            </React.Fragment>
          );
        })}
      </div>

      <div className="brain-answer-proof" aria-label={t(language, "brain.citation.region")}>
        <div className="brain-answer-proof-head">
          <span>{t(language, "brain.answerProof.title")}</span>
          <strong>{t(language, "brain.citation.count", { count: citations.length })}</strong>
        </div>
        <ol ref={listRef}>
          {citations.map((citation, index) => {
            const isActive = activeId === citation.id;
            return (
              <li
                key={citation.id}
                data-citation-target={citation.id}
                className={isActive ? "is-cited" : ""}
                tabIndex={-1}
                aria-current={isActive ? "true" : undefined}
              >
                <span>{t(language, "brain.citation.preview.from")}{citation.source}</span>
                <strong>{t(language, "brain.citation.chip", { num: index + 1 })} · {citation.title}</strong>
                {citation.snippet ? (
                  <small>{t(language, "brain.citation.preview.snippet")}{citation.snippet}</small>
                ) : null}
                {citation.score != null ? (
                  <small className="brain-citation-score">{t(language, "brain.citation.score", { score: citation.score })}</small>
                ) : null}
              </li>
            );
          })}
        </ol>
        <small className="brain-citation-tip">{t(language, "brain.citation.keyboard.tip")}</small>
      </div>

      <div className="sr-only" role="status" aria-live="polite">{announce}</div>
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
  result?: { source?: string; ok?: boolean; status?: number; error?: string };
  activeId: string | null;
  onNew: () => void;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  const language = useAppStore((state) => state.language);
  return (
    <section className="brain-side-panel">
      <div className="brain-side-head">
        <div>
          <h3>Recent conversations</h3>
          <SourceBadge result={result as Parameters<typeof SourceBadge>[0]["result"]} />
        </div>
        <Button variant="outline" size="sm" onClick={onNew}><Plus className="h-4 w-4" /> New</Button>
      </div>
      <div className="brain-conversation-list soft-scrollbar">
        {!result?.ok && result?.source === "unavailable" ? (
          <FeedbackState
            tone="error"
            language={language}
            title={t(language, "feedback.error.title")}
            body={result.error || t(language, "feedback.error.body")}
            onAction={() => window.location.reload()}
          />
        ) : conversations.length ? conversations.slice(0, 8).map((item) => {
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
        }) : (
          <FeedbackState
            tone="empty"
            language={language}
            title="No conversations yet"
            body="New exchanges will appear here."
            actionLabel="Start a conversation"
            onAction={onNew}
          />
        )}
      </div>
    </section>
  );
}

function MemoryNearby({ question, trace }: { question: string; trace: unknown }) {
  const language = useAppStore((state) => state.language);
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
      ) : hybrid.data?.source === "unavailable" ? (
        <FeedbackState
          tone="error"
          language={language}
          title={t(language, "feedback.error.title")}
          body={hybrid.data.error || t(language, "feedback.error.body")}
          onAction={() => void hybrid.refetch()}
        />
      ) : trace ? (
        <StructuredView value={trace} limit={4} />
      ) : (
        <FeedbackState
          tone="empty"
          language={language}
          title="Quiet for now"
          body="Relevant memory wakes up as the conversation gets specific."
        />
      )}
    </section>
  );
}
