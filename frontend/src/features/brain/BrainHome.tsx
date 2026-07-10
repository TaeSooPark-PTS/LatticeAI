import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { latticeApi } from "@/api/client";
import { type BrainState, triggerBrainRecall } from "@/components/LivingBrain";
import { useAppStore } from "@/store/appStore";
import { t } from "@/i18n";
import { BrainConversation } from "./BrainConversation";
import { buildBrainBrief, buildBrainProof, buildBrainReadiness, buildConversationSummaries, buildMemoryFragments, currentModelName, hasLoadedModel, parseConversationMessages, parseKnowledgeGraph } from "./brainData";
import { useConversationSession } from "./conversationSession";
import {
  INGESTION_STAGE_ORDER,
  type BrainProactiveAction,
  type BrainProactiveActivity,
  type BrainProof,
  type EmergenceEvent,
  type IngestionPipelineStage,
  type IngestionSourceType,
  type IngestionState,
  type Message,
  type MessageProof,
} from "./types";

export function BrainHome({
  brainState,
  intensity,
  onBrainChange,
}: {
  brainState: BrainState;
  intensity: number;
  onBrainChange: (state: BrainState, intensity?: number) => void;
}) {
  const qc = useQueryClient();
  const language = useAppStore((state) => state.language);
  const messages = useConversationSession((state) => state.messages);
  const setMessages = useConversationSession((state) => state.setMessages);
  const conversationId = useConversationSession((state) => state.conversationId);
  const setConversationId = useConversationSession((state) => state.setConversationId);
  const resetConversation = useConversationSession((state) => state.resetConversation);
  const [draft, setDraft] = React.useState("");
  const [imageData, setImageData] = React.useState<string | null>(null);
  const [streaming, setStreaming] = React.useState(false);
  const [historyBusyId, setHistoryBusyId] = React.useState<string | null>(null);
  const [memoryFeedback, setMemoryFeedback] = React.useState<string | null>(null);
  const [uploadingDocument, setUploadingDocument] = React.useState(false);
  const [lastRecallQuery, setLastRecallQuery] = React.useState("");
  const [ingestionStates, setIngestionStates] = React.useState<Record<IngestionSourceType, IngestionState | null>>({
    file: null,
    folder: null,
    note: null,
    web: null,
  });
  const [emergenceEvents, setEmergenceEvents] = React.useState<EmergenceEvent[]>([]);
  const [proactiveActivities, setProactiveActivities] = React.useState<BrainProactiveActivity[]>([]);
  const [detailsRequested, setDetailsRequested] = React.useState(false);
  const streamRef = React.useRef<HTMLDivElement>(null);
  const abortRef = React.useRef<AbortController | null>(null);
  const recallTimerRef = React.useRef<number | null>(null);
  const stageTimersRef = React.useRef<Record<IngestionSourceType, number[]>>({
    file: [],
    folder: [],
    note: [],
    web: [],
  });
  const pendingBaselineRef = React.useRef<
    Partial<Record<IngestionSourceType, { memories: number; entities: number; label: string }>>
  >({});
  // Source types whose request has resolved and are awaiting count settle to record emergence.
  const awaitingEmergenceRef = React.useRef<Set<IngestionSourceType>>(new Set());
  const settleTimerRef = React.useRef<number | null>(null);

  const memoriesQ = useQuery({ queryKey: ["memoryManager"], queryFn: latticeApi.memoryManager });
  const historyQ = useQuery({ queryKey: ["chatHistory"], queryFn: latticeApi.chatHistory });
  const graphQ = useQuery({ queryKey: ["graph"], queryFn: latticeApi.graph, enabled: detailsRequested });
  const modelsQ = useQuery({ queryKey: ["models"], queryFn: latticeApi.models });
  const brainProofQ = useQuery({
    queryKey: ["memoryBrainProof", lastRecallQuery],
    queryFn: () => latticeApi.memoryBrainProof(lastRecallQuery, 3),
    enabled: detailsRequested || Boolean(lastRecallQuery) || messages.length > 0,
  });
  const brainBriefQ = useQuery({
    queryKey: ["memoryBrainBrief", lastRecallQuery],
    queryFn: () => latticeApi.memoryBrainBrief(lastRecallQuery, 3),
  });

  // Large scale delegation: from main Brain composer, users can delegate big goals
  // directly. Results auto-synthesize into Brain memory/graph for strong user feel.
  const delegateMutation = useMutation({
    mutationFn: (g: string) => latticeApi.runAgent(g, ["planner", "executor", "reviewer"]),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["memoryManager"] });
      qc.invalidateQueries({ queryKey: ["memoryBrainBrief"] });
      qc.invalidateQueries({ queryKey: ["graph"] });
      qc.invalidateQueries({ queryKey: ["agentRuntime"] });
      setMemoryFeedback(t(language, "brain.delegate.done"));
      setTimeout(() => setMemoryFeedback(null), 4200);
    },
    onError: (error) => setMemoryFeedback(t(language, "brain.delegate.failed", { reason: String(error) })),
  });

  const memoryFragments = React.useMemo(
    () => buildMemoryFragments(memoriesQ.data?.data, historyQ.data?.data),
    [memoriesQ.data, historyQ.data],
  );
  const pastConversations = React.useMemo(
    () => buildConversationSummaries(historyQ.data?.data),
    [historyQ.data],
  );
  const graphModel = React.useMemo(() => parseKnowledgeGraph(graphQ.data?.data), [graphQ.data]);
  const knowledgeConcepts = React.useMemo(
    () => graphModel.nodes.slice(0, 10),
    [graphModel.nodes],
  );
  const brainReadiness = React.useMemo(
    () => buildBrainReadiness(memoriesQ.data?.data, memoryFragments.length, knowledgeConcepts.length),
    [knowledgeConcepts.length, memoriesQ.data, memoryFragments.length],
  );
  const modelName = React.useMemo(() => currentModelName(modelsQ.data?.data), [modelsQ.data]);
  const modelReady = React.useMemo(() => hasLoadedModel(modelsQ.data?.data), [modelsQ.data]);
  const brainProof = React.useMemo(
    () => buildBrainProof(brainProofQ.data?.data, modelName),
    [brainProofQ.data, modelName],
  );
  const brainBrief = React.useMemo(
    () => buildBrainBrief(brainBriefQ.data?.data),
    [brainBriefQ.data],
  );
  const starterPrompts = React.useMemo(
    () => [
      t(language, "brain.prompt.remember"),
      t(language, "brain.prompt.know"),
      t(language, "brain.prompt.plan"),
    ],
    [language],
  );

  React.useEffect(() => {
    if (streaming) onBrainChange("thinking", 0.94);
    else if (draft.trim().length > 4) onBrainChange("listening", 0.76);
    else onBrainChange("idle", 0.58);
  }, [streaming, draft, onBrainChange]);

  React.useEffect(() => {
    const stream = streamRef.current;
    if (stream) stream.scrollTop = stream.scrollHeight;
  }, [messages]);

  React.useEffect(() => {
    return () => {
      if (recallTimerRef.current !== null) window.clearTimeout(recallTimerRef.current);
      for (const timers of Object.values(stageTimersRef.current)) {
        for (const timer of timers) window.clearTimeout(timer);
      }
    };
  }, []);

  const clearStageTimers = React.useCallback((sourceType: IngestionSourceType) => {
    for (const timer of stageTimersRef.current[sourceType]) window.clearTimeout(timer);
    stageTimersRef.current[sourceType] = [];
  }, []);

  const setStage = React.useCallback((sourceType: IngestionSourceType, stage: IngestionPipelineStage) => {
    setIngestionStates((prev) => {
      const current = prev[sourceType];
      if (!current) return prev;
      return {
        ...prev,
        [sourceType]: {
          ...current,
          stage,
          completedAt: stage === "complete" || stage === "error" ? Date.now() : current.completedAt,
        },
      };
    });
  }, []);

  const beginIngestion = React.useCallback(
    (sourceType: IngestionSourceType, label: string) => {
      setDetailsRequested(true);
      clearStageTimers(sourceType);
      pendingBaselineRef.current[sourceType] = {
        memories: memoryFragments.length,
        entities: knowledgeConcepts.length,
        label,
      };
      setIngestionStates((prev) => ({
        ...prev,
        [sourceType]: {
          sourceType,
          label,
          stage: "preparing",
          startedAt: Date.now(),
          completedAt: null,
          newMemories: 0,
          newEntities: 0,
        },
      }));
      // Progressive disclosure of the in-flight pipeline while the request runs.
      const interim: IngestionPipelineStage[] = ["parsing", "embedding", "indexing"];
      interim.forEach((stage, index) => {
        const timer = window.setTimeout(() => setStage(sourceType, stage), 420 * (index + 1));
        stageTimersRef.current[sourceType].push(timer);
      });
    },
    [clearStageTimers, knowledgeConcepts.length, memoryFragments.length, setStage],
  );

  const resolveEmergence = React.useCallback(
    (sourceType: IngestionSourceType, memoryCount: number, entityCount: number) => {
      clearStageTimers(sourceType);
      const baseline = pendingBaselineRef.current[sourceType];
      const label = baseline?.label ?? "";
      // Snapshot deltas after invalidation lands; cap at >=0 to avoid noise.
      const newMemories = Math.max(0, memoryCount - (baseline?.memories ?? memoryCount));
      const newEntities = Math.max(0, entityCount - (baseline?.entities ?? entityCount));
      delete pendingBaselineRef.current[sourceType];
      setIngestionStates((prev) => {
        const current = prev[sourceType];
        if (!current) return prev;
        return {
          ...prev,
          [sourceType]: {
            ...current,
            stage: "complete",
            completedAt: Date.now(),
            newMemories,
            newEntities,
          },
        };
      });
      setEmergenceEvents((events) =>
        [
          {
            id: `${sourceType}-${Date.now()}`,
            sourceType,
            label,
            newMemories,
            newEntities,
            at: Date.now(),
          },
          ...events,
        ].slice(0, 10),
      );
    },
    [clearStageTimers],
  );

  // Once a request resolves we wait for the refetched counts to settle, then record the
  // real emergence delta. A fallback timer guarantees the panel never hangs on a stale count.
  const markAwaitingEmergence = React.useCallback(
    (sourceType: IngestionSourceType) => {
      awaitingEmergenceRef.current.add(sourceType);
      if (settleTimerRef.current !== null) window.clearTimeout(settleTimerRef.current);
      settleTimerRef.current = window.setTimeout(() => {
        for (const pending of Array.from(awaitingEmergenceRef.current)) {
          resolveEmergence(pending, memoryFragments.length, knowledgeConcepts.length);
          awaitingEmergenceRef.current.delete(pending);
        }
      }, 1600);
    },
    [knowledgeConcepts.length, memoryFragments.length, resolveEmergence],
  );

  // Flush awaiting ingestions as soon as the underlying counts change post-invalidation.
  React.useEffect(() => {
    if (awaitingEmergenceRef.current.size === 0) return;
    for (const pending of Array.from(awaitingEmergenceRef.current)) {
      const baseline = pendingBaselineRef.current[pending];
      if (!baseline) {
        awaitingEmergenceRef.current.delete(pending);
        continue;
      }
      if (memoryFragments.length !== baseline.memories || knowledgeConcepts.length !== baseline.entities) {
        resolveEmergence(pending, memoryFragments.length, knowledgeConcepts.length);
        awaitingEmergenceRef.current.delete(pending);
      }
    }
  }, [memoryFragments.length, knowledgeConcepts.length, resolveEmergence]);

  React.useEffect(() => {
    return () => {
      if (settleTimerRef.current !== null) window.clearTimeout(settleTimerRef.current);
    };
  }, []);

  const failIngestion = React.useCallback(
    (sourceType: IngestionSourceType, reason: string) => {
      clearStageTimers(sourceType);
      delete pendingBaselineRef.current[sourceType];
      setIngestionStates((prev) => {
        const current = prev[sourceType];
        if (!current) return prev;
        return {
          ...prev,
          [sourceType]: { ...current, stage: "error", completedAt: Date.now(), error: reason },
        };
      });
    },
    [clearStageTimers],
  );

  async function send() {
    const text = draft.trim();
    if (!text || streaming) return;
    setDraft("");
    await sendText(text);
  }

  // Regenerate: answer the latest user question again in the same conversation,
  // replacing the trailing assistant reply so the thread does not duplicate.
  async function regenerate() {
    if (streaming) return;
    const lastUser = [...messages].reverse().find((message: Message) => message.role === "user");
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
            const files = (agent.created_files || []).map((file) => ({
              path: file.path,
              filename: file.filename || file.path.split("/").pop() || file.path,
              bytes: file.bytes || 0,
            }));
            if (!files.length) return;
            setMessages((items) => {
              const next = [...items];
              next[next.length - 1] = { ...next[next.length - 1], files };
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
        setMessages((items) => {
          const next = [...items];
          next[next.length - 1] = { role: "assistant", content: `${t(language, "brain.unavailable")}: ${result.error}` };
          return next;
        });
      } else {
        setMemoryFeedback(t(language, "brain.saved", { topics: knowledgeConcepts.length, memories: memoryFragments.length }));
        setLastRecallQuery(text);
        void attachAnswerProof(text);
      }
    } catch (error) {
      const aborted = error instanceof DOMException && error.name === "AbortError";
      // Keep whatever partial answer already streamed in; never leave a
      // silently empty assistant bubble behind.
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
    } finally {
      abortRef.current = null;
      setStreaming(false);
      void qc.invalidateQueries({ queryKey: ["chatHistory"] });
      void qc.invalidateQueries({ queryKey: ["memoryManager"] });
      void qc.invalidateQueries({ queryKey: ["graph"] });
      void qc.invalidateQueries({ queryKey: ["memoryBrainProof"] });
      void qc.invalidateQueries({ queryKey: ["memoryBrainBrief"] });
    }
  }

  async function attachAnswerProof(query: string) {
    const proofResult = await latticeApi.memoryBrainProof(query, 4);
    const proof = buildBrainProof(proofResult.data, modelName);
    const messageProof = toMessageProof(proof, query, modelName);
    setMessages((items) => {
      const next = [...items];
      for (let index = next.length - 1; index >= 0; index -= 1) {
        if (next[index].role === "assistant") {
          next[index] = { ...next[index], proof: messageProof };
          break;
        }
      }
      return next;
    });
  }

  async function uploadDocument(file: File) {
    if (uploadingDocument) return;

    setUploadingDocument(true);
    setMemoryFeedback(t(language, "brain.upload.pending", { name: file.name }));
    onBrainChange("recalling", 0.86);
    beginIngestion("file", file.name);

    try {
      const result = await latticeApi.uploadDocument(file);
      if (result.error || !result.ok) {
        const reason = result.error || "unavailable";
        setMemoryFeedback(t(language, "brain.upload.failed", { reason }));
        failIngestion("file", reason);
        return;
      }

      setMemoryFeedback(t(language, "brain.upload.saved", { name: file.name }));
      setLastRecallQuery(file.name);
      triggerBrainRecall();
      void qc.invalidateQueries({ queryKey: ["memoryManager"] });
      void qc.invalidateQueries({ queryKey: ["graph"] });
      void qc.invalidateQueries({ queryKey: ["memoryBrainProof"] });
      void qc.invalidateQueries({ queryKey: ["memoryBrainBrief"] });
      markAwaitingEmergence("file");
    } finally {
      setUploadingDocument(false);
    }
  }

  async function connectFolder(path: string) {
    const target = path.trim();
    if (!target) return;
    setMemoryFeedback(t(language, "brain.ingest.folder.pending", { path: target }));
    onBrainChange("recalling", 0.84);
    beginIngestion("folder", target);
    const result = await latticeApi.connectFolder(target);
    if (result.error || !result.ok) {
      setMemoryFeedback(t(language, "brain.ingest.folder.failed", { reason: result.error || "unavailable" }));
      failIngestion("folder", result.error || "unavailable");
      return;
    }
    setMemoryFeedback(t(language, "brain.ingest.folder.saved", { path: target }));
    setLastRecallQuery(target);
    triggerBrainRecall();
    void refreshBrainProof(target);
    markAwaitingEmergence("folder");
  }

  async function ingestNote(note: string) {
    const content = note.trim();
    if (!content) return;
    setMemoryFeedback(t(language, "brain.ingest.note.pending"));
    onBrainChange("recalling", 0.84);
    beginIngestion("note", content.slice(0, 80));
    const result = await latticeApi.ingestNote(content, content.slice(0, 80));
    if (result.error || !result.ok) {
      setMemoryFeedback(t(language, "brain.ingest.note.failed", { reason: result.error || "unavailable" }));
      failIngestion("note", result.error || "unavailable");
      return;
    }
    setMemoryFeedback(t(language, "brain.ingest.note.saved"));
    setLastRecallQuery(content.slice(0, 120));
    triggerBrainRecall();
    void refreshBrainProof(content.slice(0, 120));
    markAwaitingEmergence("note");
  }

  async function ingestWeb(url: string) {
    const target = url.trim();
    if (!target) return;
    setMemoryFeedback(t(language, "brain.ingest.web.pending", { url: target }));
    onBrainChange("recalling", 0.84);
    beginIngestion("web", target);
    const result = await latticeApi.browserReadUrl(target);
    if (result.error || !result.ok) {
      setMemoryFeedback(t(language, "brain.ingest.web.failed", { reason: result.error || "unavailable" }));
      failIngestion("web", result.error || "unavailable");
      return;
    }
    setMemoryFeedback(t(language, "brain.ingest.web.saved", { url: target }));
    setLastRecallQuery(target);
    triggerBrainRecall();
    void refreshBrainProof(target);
    markAwaitingEmergence("web");
  }

  async function refreshBrainProof(query = lastRecallQuery) {
    await Promise.all([
      qc.invalidateQueries({ queryKey: ["memoryManager"] }),
      qc.invalidateQueries({ queryKey: ["graph"] }),
      qc.invalidateQueries({ queryKey: ["memoryBrainProof"] }),
      qc.invalidateQueries({ queryKey: ["memoryBrainBrief"] }),
    ]);
    if (query.trim()) {
      await attachAnswerProof(query);
    }
  }

  async function verifyModelContinuity() {
    const lastUserMessage = [...messages].reverse().find((message: Message) => message.role === "user");
    const query = lastRecallQuery || lastUserMessage?.content || "";
    if (!query.trim()) {
      setMemoryFeedback(t(language, "brain.modelDemo.needQuestion"));
      return;
    }
    setMemoryFeedback(t(language, "brain.modelDemo.checking", { model: modelName }));
    await attachAnswerProof(query);
    setLastRecallQuery(query);
    setMemoryFeedback(t(language, "brain.modelDemo.done", { model: modelName }));
  }

  async function createActionItem(content: string): Promise<boolean> {
    const trimmed = content.trim();
    if (!trimmed) return false;
    const lastUser = [...messages].reverse().find((message: Message) => message.role === "user");
    const title = (lastUser?.content || t(language, "brain.action.defaultTitle")).trim().slice(0, 96);
    const result = await latticeApi.createReviewItem({
      title,
      summary: trimmed.slice(0, 420),
      source: "chat_followup",
      kind: "task_draft",
      payload: {
        answer_preview: trimmed.slice(0, 2000),
        conversation_id: conversationId || "",
      },
      provenance: {
        conversation_id: conversationId || "",
        source_detail: "brain_chat",
      },
    });
    setMemoryFeedback(result.ok
      ? t(language, "brain.action.saved")
      : t(language, "brain.action.saveFailed", { reason: result.error || String(result.status || "") }));
    return Boolean(result.ok);
  }

  function recordProactiveActivity(action: BrainProactiveAction, status: BrainProactiveActivity["status"], detail?: string, id = `${action.id}-${Date.now()}`) {
    const now = Date.now();
    setProactiveActivities((items) => {
      const next = items.filter((item) => item.id !== id);
      return [
        {
          id,
          actionId: action.id,
          labelKey: action.labelKey,
          intent: action.intent,
          status,
          startedAt: items.find((item) => item.id === id)?.startedAt ?? now,
          completedAt: status === "running" ? undefined : now,
          detail,
        },
        ...next,
      ].slice(0, 6);
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
        return;
      }
      if (action.intent === "review") {
        const ok = await createActionItem(prompt);
        recordProactiveActivity(action, ok ? "completed" : "failed", "review", activityId);
        return;
      }
      await sendText(prompt);
      recordProactiveActivity(action, "completed", "chat", activityId);
    } catch (error) {
      recordProactiveActivity(action, "failed", error instanceof Error ? error.message : String(error), activityId);
    }
  }

  function stopStreaming() {
    abortRef.current?.abort();
  }

  function startNewConversation() {
    if (streaming) stopStreaming();
    resetConversation();
    setMemoryFeedback(null);
    onBrainChange("idle", 0.58);
  }

  async function resumeConversation(id: string) {
    if (historyBusyId || streaming) return;
    setHistoryBusyId(id);
    setMemoryFeedback(t(language, "brain.history.loading"));
    try {
      const result = await latticeApi.conversation(id);
      if (result.error || !result.ok) {
        setMemoryFeedback(t(language, "brain.history.loadFailed", { reason: result.error || "unavailable" }));
        return;
      }
      const restored = parseConversationMessages(result.data);
      if (!restored.length) {
        setMemoryFeedback(t(language, "brain.history.loadFailed", { reason: t(language, "brain.history.emptyConversation") }));
        return;
      }
      setConversationId(id);
      setMessages(restored);
      setMemoryFeedback(t(language, "brain.history.resumed"));
    } finally {
      setHistoryBusyId(null);
    }
  }

  async function deleteConversation(id: string) {
    if (historyBusyId) return;
    setHistoryBusyId(id);
    try {
      const result = await latticeApi.deleteConversation(id);
      if (result.error || !result.ok) {
        setMemoryFeedback(t(language, "brain.history.deleteFailed", { reason: result.error || "unavailable" }));
        return;
      }
      if (conversationId === id) resetConversation();
      setMemoryFeedback(t(language, "brain.history.deleted"));
      void qc.invalidateQueries({ queryKey: ["chatHistory"] });
      void qc.invalidateQueries({ queryKey: ["memoryManager"] });
    } finally {
      setHistoryBusyId(null);
    }
  }

  function openKnowledgeGraph() {
    openDepth(5);
  }

  // Depth-aware navigation: memory layers land on the memory view, knowledge
  // layers land on the graph, so a ring peek's "go deeper" keeps its meaning.
  function openDepth(depth: number) {
    triggerBrainRecall();
    onBrainChange("recalling", 0.82);
    window.location.hash = depth <= 2 ? "/memory" : "/knowledge-graph";
  }

  return (
    <main className="brain-home" aria-label={t(language, "brain.aria.home")}>
      <BrainConversation
        language={language}
        brainState={brainState}
        intensity={intensity}
        modelName={modelName}
        modelReady={modelReady}
        messages={messages}
        pastConversations={pastConversations}
        historyBusyId={historyBusyId}
        starterPrompts={starterPrompts}
        memoryFeedback={memoryFeedback}
        ingestionStates={ingestionStates}
        emergenceEvents={emergenceEvents}
        proactiveActivities={proactiveActivities}
        draft={draft}
        streaming={streaming}
        imageData={imageData}
        streamRef={streamRef}
        memories={memoryFragments}
        concepts={knowledgeConcepts}
        relationshipCount={graphModel.edges.length}
        readiness={brainReadiness}
        proof={brainProof}
        brief={brainBrief}
        uploadingDocument={uploadingDocument}
        onOpenDepth={openDepth}
        onDraftChange={setDraft}
        onImageDataChange={setImageData}
        onUploadDocument={(file) => void uploadDocument(file)}
        onConnectFolder={(path) => void connectFolder(path)}
        onIngestNote={(note) => void ingestNote(note)}
        onIngestWeb={(url) => void ingestWeb(url)}
        onVerifyModelContinuity={() => void verifyModelContinuity()}
        onSend={() => void send()}
        onSendText={(text) => void sendText(text)}
        onCreateActionItem={(content) => void createActionItem(content)}
        onProactiveAction={(action) => void handleProactiveAction(action)}
        onStop={stopStreaming}
        onRegenerate={() => void regenerate()}
        onNewConversation={startNewConversation}
        onResumeConversation={(id) => void resumeConversation(id)}
        onDeleteConversation={(id) => void deleteConversation(id)}
        onExploreBrain={openKnowledgeGraph}
        onRequestDetails={() => setDetailsRequested(true)}
      />
    </main>
  );
}

function toMessageProof(proof: BrainProof, query: string, fallbackModelName: string): MessageProof {
  return {
    query,
    model: proof.modelContinuity.activeModel || fallbackModelName,
    provenAcrossModels: proof.modelContinuity.proven && proof.claims.keepsContextAcrossModels,
    citations: proof.recall.items.slice(0, 4).map((item) => ({
      id: item.id,
      source: item.source,
      title: item.title,
      snippet: item.snippet,
      matchedTerms: item.matchedTerms,
      confidence: item.confidence,
      score: item.score,
    })),
  };
}
