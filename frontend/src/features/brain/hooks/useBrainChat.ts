import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { latticeApi } from "@/api/client";
import { triggerBrainRecall, type BrainState } from "@/components/LivingBrain";
import { t, type Language } from "@/i18n";
import { type ApprovalResolution, parseApprovalPayload } from "../approvalFlow";
import { agentPayloadFiles, parseContextQuality, parseGrounding } from "../brainData";
import { useConversationSession } from "../conversationSession";
import type {
  BrainProactiveAction,
  BrainProactiveActivity,
  IngestionSourceType,
  Message,
} from "../types";

export function useBrainChat({
  language,
  modelReady,
  onBrainChange,
  setMemoryFeedback,
  beginIngestion,
  completeIngestion,
  failIngestion,
  setLastRecallQuery,
  attachAnswerProof,
}: {
  language: Language;
  modelReady: boolean;
  onBrainChange: (state: BrainState, intensity?: number) => void;
  setMemoryFeedback: React.Dispatch<React.SetStateAction<string | null>>;
  beginIngestion: (sourceType: IngestionSourceType, label: string) => void;
  completeIngestion: (sourceType: IngestionSourceType, data?: unknown) => Promise<{ memories: number; entities: number }>;
  failIngestion: (sourceType: IngestionSourceType, reason: string) => void;
  setLastRecallQuery: React.Dispatch<React.SetStateAction<string>>;
  attachAnswerProof: (query: string) => Promise<boolean>;
}) {
  const qc = useQueryClient();
  const messages = useConversationSession((state) => state.messages);
  const setMessages = useConversationSession((state) => state.setMessages);
  const conversationId = useConversationSession((state) => state.conversationId);
  const setConversationId = useConversationSession((state) => state.setConversationId);
  const [draft, setDraft] = React.useState("");
  const [imageData, setImageData] = React.useState<string | null>(null);
  const [streaming, setStreaming] = React.useState(false);
  const [proactiveActivities, setProactiveActivities] = React.useState<BrainProactiveActivity[]>([]);
  const streamRef = React.useRef<HTMLDivElement>(null);
  const abortRef = React.useRef<AbortController | null>(null);
  const recallTimerRef = React.useRef<number | null>(null);

  const delegateMutation = useMutation({
    mutationFn: async (goal: string) => {
      const result = await latticeApi.runAgent(goal, ["planner", "executor", "reviewer"]);
      if (!result.ok) throw new Error(result.error || t(language, "ui.status.unavailable"));
      return result;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["memoryManager"] });
      void qc.invalidateQueries({ queryKey: ["memoryBrainBrief"] });
      void qc.invalidateQueries({ queryKey: ["graphPreview"] });
      void qc.invalidateQueries({ queryKey: ["graph"] });
      void qc.invalidateQueries({ queryKey: ["agentRuntime"] });
      setMemoryFeedback(t(language, "brain.delegate.done"));
      window.setTimeout(() => setMemoryFeedback(null), 4200);
    },
    onError: (error) => setMemoryFeedback(t(language, "brain.delegate.failed", { reason: String(error) })),
  });

  React.useEffect(() => {
    if (streaming) onBrainChange("thinking", 0.94);
    else if (draft.trim().length > 4) onBrainChange("listening", 0.76);
    else onBrainChange("idle", 0.58);
  }, [draft, onBrainChange, streaming]);

  React.useEffect(() => {
    if (streamRef.current) streamRef.current.scrollTop = streamRef.current.scrollHeight;
  }, [messages]);

  React.useEffect(() => () => {
    if (recallTimerRef.current !== null) window.clearTimeout(recallTimerRef.current);
    abortRef.current?.abort();
  }, []);

  async function send() {
    const text = draft.trim();
    if (!text || streaming) return;
    setDraft("");
    await sendText(text);
  }

  async function regenerate() {
    if (streaming) return;
    const lastUser = [...messages].reverse().find((message) => message.role === "user");
    if (!lastUser) return;
    setMessages((items) => {
      const next = [...items];
      if (next[next.length - 1]?.role === "assistant") next.pop();
      if (next[next.length - 1]?.role === "user") next.pop();
      return next;
    });
    await sendText(lastUser.content);
  }

  async function sendText(text: string) {
    if (!text || streaming) return;
    const activeConversationId = conversationId || `brain-${Date.now()}`;
    if (!conversationId) setConversationId(activeConversationId);
    if (!modelReady) {
      setMessages((items) => [
        ...items,
        { role: "user", content: text },
        { role: "assistant", content: t(language, "brain.noModel.message") },
      ]);
      setImageData(null);
      setMemoryFeedback(t(language, "brain.noModel.status"));
      return;
    }

    setMessages((items) => [...items, { role: "user", content: text }, { role: "assistant", content: "" }]);
    setImageData(null);
    setStreaming(true);
    setMemoryFeedback(null);
    beginIngestion("chat", text.slice(0, 120));
    onBrainChange("thinking", 0.96);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const result = await latticeApi.streamChat(
        { message: text, conversation_id: activeConversationId, image_data: imageData || undefined },
        {
          signal: controller.signal,
          onChunk: (_delta, fullText) => {
            setMessages((items) => {
              const next = [...items];
              next[next.length - 1] = { ...next[next.length - 1], role: "assistant", content: fullText };
              return next;
            });
          },
          onAgent: (agent) => {
            // Governed plans pause server-side as awaiting_approval — attach
            // the resume token to this reply so the inline approval card can
            // approve/edit/cancel the parked run.
            const approval = parseApprovalPayload(agent);
            if (approval) {
              setMessages((items) => {
                const next = [...items];
                next[next.length - 1] = { ...next[next.length - 1], approval };
                return next;
              });
              return;
            }
            // artifacts[] carries the per-file preview verdict; keyed by path
            // so it can be joined back onto created_files defensively.
            const files = agentPayloadFiles(agent);
            // NEEDS_REVIEW / FAILED must reach the message even when no file
            // was produced — they render as warnings, never as success.
            const agentState =
              agent.final_state === "NEEDS_REVIEW" || agent.final_state === "FAILED"
                ? agent.final_state
                : undefined;
            if (!files.length && !agentState) return;
            setMessages((items) => {
              const next = [...items];
              next[next.length - 1] = {
                ...next[next.length - 1],
                ...(files.length ? { files } : {}),
                ...(agentState ? { agentState } : {}),
              };
              return next;
            });
          },
          onTrace: (trace) => {
            if (!trace) return;
            onBrainChange("recalling", 0.9);
            triggerBrainRecall();
            if (recallTimerRef.current !== null) window.clearTimeout(recallTimerRef.current);
            recallTimerRef.current = window.setTimeout(() => onBrainChange("thinking", 0.9), 900);
          },
        },
      );
      if (result.error) {
        setMessages((items) => replaceTrailingAssistant(items, `${t(language, "brain.unavailable")}: ${result.error}`));
        failIngestion("chat", String(result.error));
      } else {
        // Honest signaling: surface the additive context_quality meta (from the
        // trailer or the trace record) on the answer it belongs to.
        const contextQuality = parseContextQuality("contextQuality" in result ? result.contextQuality : null)
          || parseContextQuality(result.trace);
        // Answer-citation binding verdict (근거 있음/근거 없음) rides the same
        // trailer; it badges the answer without ever modifying it.
        const grounding = parseGrounding("grounding" in result ? result.grounding : null)
          || parseGrounding(result.trace);
        if (contextQuality || grounding) {
          setMessages((items) => {
            const next = [...items];
            for (let index = next.length - 1; index >= 0; index -= 1) {
              if (next[index].role === "assistant") {
                next[index] = {
                  ...next[index],
                  ...(contextQuality ? { contextQuality } : {}),
                  ...(grounding ? { grounding } : {}),
                };
                break;
              }
            }
            return next;
          });
        }
        setLastRecallQuery(text);
        const counts = await completeIngestion("chat");
        setMemoryFeedback(t(language, "brain.saved", { topics: counts.entities, memories: counts.memories }));
        triggerBrainRecall();
        void attachAnswerProof(text);
      }
    } catch (error) {
      const aborted = error instanceof DOMException && error.name === "AbortError";
      setMessages((items) => {
        const next = [...items];
        const last = next[next.length - 1];
        if (last?.role === "assistant" && !last.content.trim()) {
          next[next.length - 1] = {
            ...last,
            content: aborted
              ? t(language, "brain.stopped.empty")
              : `${t(language, "brain.unavailable")}: ${error instanceof Error ? error.message : String(error)}`,
          };
        }
        return next;
      });
      setMemoryFeedback(aborted ? t(language, "brain.stopped") : null);
      failIngestion("chat", aborted ? "stopped" : error instanceof Error ? error.message : String(error));
    } finally {
      abortRef.current = null;
      setStreaming(false);
      for (const key of ["chatHistory", "memoryManager", "graphPreview", "graph", "memoryBrainProof", "memoryBrainBrief"]) {
        void qc.invalidateQueries({ queryKey: [key] });
      }
    }
  }

  // Applies an /agent/resume outcome to the paused message. A finished run
  // merges exactly like a normal agent completion (response text + files via
  // the shared artifacts join + NEEDS_REVIEW/FAILED warning); every terminal
  // approval status persists on the message so the card stays honest.
  function handleApprovalResolved(messageIndex: number, resolution: ApprovalResolution) {
    setMessages((items) => {
      const message = items[messageIndex];
      if (!message?.approval) return items;
      const next = [...items];
      if (resolution.kind === "finished") {
        const agent = resolution.payload;
        const files = agentPayloadFiles(agent);
        const agentState =
          agent.final_state === "NEEDS_REVIEW" || agent.final_state === "FAILED"
            ? agent.final_state
            : undefined;
        const response = typeof agent.response === "string" ? agent.response.trim() : "";
        next[messageIndex] = {
          ...message,
          content: response || message.content,
          ...(files.length ? { files } : {}),
          ...(agentState ? { agentState } : {}),
          approval: { ...message.approval, status: "approved" },
        };
      } else if (resolution.kind === "error") {
        next[messageIndex] = {
          ...message,
          approval: { ...message.approval, status: "error", errorReason: resolution.reason },
        };
      } else {
        next[messageIndex] = {
          ...message,
          approval: { ...message.approval, status: resolution.kind },
        };
      }
      return next;
    });
    if (resolution.kind === "finished") {
      triggerBrainRecall();
      for (const key of ["chatHistory", "memoryManager", "graphPreview", "graph", "memoryBrainProof", "memoryBrainBrief", "agentRuntime"]) {
        void qc.invalidateQueries({ queryKey: [key] });
      }
    }
  }

  async function createActionItem(content: string): Promise<boolean> {
    const trimmed = content.trim();
    if (!trimmed) return false;
    const lastUser = [...messages].reverse().find((message) => message.role === "user");
    const title = (lastUser?.content || t(language, "brain.action.defaultTitle")).trim().slice(0, 96);
    const result = await latticeApi.createReviewItem({
      title,
      summary: trimmed.slice(0, 420),
      source: "chat_followup",
      kind: "task_draft",
      payload: { answer_preview: trimmed.slice(0, 2000), conversation_id: conversationId || "" },
      provenance: { conversation_id: conversationId || "", source_detail: "brain_chat" },
    });
    setMemoryFeedback(result.ok
      ? t(language, "brain.action.saved")
      : t(language, "brain.action.saveFailed", { reason: result.error || String(result.status || "") }));
    return result.ok;
  }

  function recordProactiveActivity(
    action: BrainProactiveAction,
    status: BrainProactiveActivity["status"],
    detail?: string,
    id = `${action.id}-${Date.now()}`,
  ) {
    const now = Date.now();
    setProactiveActivities((items) => {
      const existing = items.find((item) => item.id === id);
      return [{
        id,
        actionId: action.id,
        labelKey: action.labelKey,
        intent: action.intent,
        status,
        startedAt: existing?.startedAt ?? now,
        completedAt: status === "running" ? undefined : now,
        detail,
      }, ...items.filter((item) => item.id !== id)].slice(0, 6);
    });
    return id;
  }

  async function handleProactiveAction(action: BrainProactiveAction) {
    const prompt = action.prompt.trim();
    const activityId = recordProactiveActivity(action, "running");
    if (action.intent === "route" && action.route) {
      window.location.hash = action.route;
      recordProactiveActivity(action, "completed", action.route, activityId);
      return;
    }
    if (!prompt) {
      recordProactiveActivity(action, "failed", "empty prompt", activityId);
      return;
    }
    try {
      if (action.intent === "delegate") {
        await delegateMutation.mutateAsync(prompt);
        recordProactiveActivity(action, "completed", "agent", activityId);
      } else if (action.intent === "review") {
        const ok = await createActionItem(prompt);
        recordProactiveActivity(action, ok ? "completed" : "failed", "review", activityId);
      } else {
        await sendText(prompt);
        recordProactiveActivity(action, "completed", "chat", activityId);
      }
    } catch (error) {
      recordProactiveActivity(action, "failed", error instanceof Error ? error.message : String(error), activityId);
    }
  }

  return {
    messages,
    conversationId,
    draft,
    setDraft,
    imageData,
    setImageData,
    streaming,
    streamRef,
    proactiveActivities,
    send,
    sendText,
    regenerate,
    createActionItem,
    handleProactiveAction,
    handleApprovalResolved,
    stopStreaming: () => abortRef.current?.abort(),
  };
}

function replaceTrailingAssistant(items: Message[], content: string): Message[] {
  const next = [...items];
  next[next.length - 1] = { role: "assistant", content };
  return next;
}
