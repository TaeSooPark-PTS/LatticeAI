import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { latticeApi } from "@/api/client";
import { type BrainState, LivingBrain, triggerBrainRecall } from "@/components/LivingBrain";
import { useAppStore } from "@/store/appStore";
import { t } from "@/i18n";
import { BrainConversation } from "./BrainConversation";
import { buildBrainProof, buildBrainReadiness, buildMemoryFragments, currentModelName, parseKnowledgeGraph } from "./brainData";
import { DepthEmergence } from "./DepthEmergence";
import {
  DEPTHS,
  INGESTION_STAGE_ORDER,
  type BrainDepth,
  type BrainProof,
  type EmergenceEvent,
  type IngestionPipelineStage,
  type IngestionSourceType,
  type IngestionState,
  type MemoryFragment,
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
  const [messages, setMessages] = React.useState<Message[]>([]);
  const [draft, setDraft] = React.useState("");
  const [imageData, setImageData] = React.useState<string | null>(null);
  const [streaming, setStreaming] = React.useState(false);
  const [conversationId, setConversationId] = React.useState<string | null>(null);
  const [explorationDepth, setExplorationDepth] = React.useState<BrainDepth>(1);
  const [graphSearch, setGraphSearch] = React.useState("");
  const [selectedGraphId, setSelectedGraphId] = React.useState<string | null>(null);
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
  const streamRef = React.useRef<HTMLDivElement>(null);
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
  const graphQ = useQuery({ queryKey: ["graph"], queryFn: latticeApi.graph });
  const modelsQ = useQuery({ queryKey: ["models"], queryFn: latticeApi.models });
  const brainProofQ = useQuery({
    queryKey: ["memoryBrainProof", lastRecallQuery],
    queryFn: () => latticeApi.memoryBrainProof(lastRecallQuery, 3),
  });

  const memoryFragments = React.useMemo(
    () => buildMemoryFragments(memoriesQ.data?.data, historyQ.data?.data),
    [memoriesQ.data, historyQ.data],
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
  const relationshipThreads = React.useMemo(
    () => graphModel.edges.slice(0, 10),
    [graphModel.edges],
  );
  const modelName = React.useMemo(() => currentModelName(modelsQ.data?.data), [modelsQ.data]);
  const brainProof = React.useMemo(
    () => buildBrainProof(brainProofQ.data?.data, modelName),
    [brainProofQ.data, modelName],
  );
  const currentDepth = DEPTHS[explorationDepth - 1];
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
    else onBrainChange(currentDepth.state, explorationDepth === 1 ? 0.58 : 0.66 + explorationDepth * 0.06);
  }, [streaming, draft, currentDepth.state, explorationDepth, onBrainChange]);

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
    const activeConversationId = conversationId || `brain-${Date.now()}`;
    if (!conversationId) setConversationId(activeConversationId);

    setMessages((items) => [...items, { role: "user", content: text }, { role: "assistant", content: "" }]);
    setDraft("");
    setImageData(null);
    setStreaming(true);
    setMemoryFeedback(null);
    onBrainChange("thinking", 0.96);

    try {
      const result = await latticeApi.streamChat(
        { message: text, conversation_id: activeConversationId, image_data: imageData || undefined },
        {
          onChunk: (_delta, fullText) => {
            setMessages((items) => {
              const next = [...items];
              next[next.length - 1] = { role: "assistant", content: fullText };
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
    } finally {
      setStreaming(false);
      void qc.invalidateQueries({ queryKey: ["chatHistory"] });
      void qc.invalidateQueries({ queryKey: ["memoryManager"] });
      void qc.invalidateQueries({ queryKey: ["graph"] });
      void qc.invalidateQueries({ queryKey: ["memoryBrainProof"] });
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
      if (result.error) {
        setMemoryFeedback(t(language, "brain.upload.failed", { reason: result.error }));
        failIngestion("file", result.error);
        return;
      }

      setMemoryFeedback(t(language, "brain.upload.saved", { name: file.name }));
      setLastRecallQuery(file.name);
      triggerBrainRecall();
      void qc.invalidateQueries({ queryKey: ["memoryManager"] });
      void qc.invalidateQueries({ queryKey: ["graph"] });
      void qc.invalidateQueries({ queryKey: ["memoryBrainProof"] });
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

  function deepen() {
    setExplorationDepth((depth) => {
      const next = Math.min(5, depth + 1) as BrainDepth;
      const nextDepth = DEPTHS[next - 1];
      onBrainChange(nextDepth.state, 0.66 + next * 0.06);
      if (next >= 2) triggerBrainRecall();
      return next;
    });
  }

  function jumpToDepth(next: BrainDepth) {
    setExplorationDepth(next);
    const nextDepth = DEPTHS[next - 1];
    onBrainChange(nextDepth.state, next === 1 ? 0.58 : 0.66 + next * 0.06);
    if (next >= 2) triggerBrainRecall();
  }

  function surface() {
    setExplorationDepth(1);
    setSelectedGraphId(null);
    setGraphSearch("");
    onBrainChange("idle", 0.58);
  }

  function recallMemory(fragment: MemoryFragment) {
    triggerBrainRecall();
    setExplorationDepth((depth) => Math.max(depth, 2) as BrainDepth);
    setMessages((items) => [
      ...items,
      { role: "assistant", content: t(language, "brain.recalled", { title: fragment.title }) },
    ]);
  }

  return (
    <main className="brain-home" aria-label={t(language, "brain.aria.home")}>
      <div className="brain-product-intro">
        <div className="brain-product-kicker">{t(language, "brain.home.kicker")}</div>
        <div className="brain-product-line">{t(language, "brain.home.line")}</div>
      </div>
      <section className="brain-presence" aria-label={t(language, "brain.aria.exploration")}>
        <div className="brain-exploration" data-depth={explorationDepth}>
          <LivingBrain
            state={brainState}
            intensity={intensity + explorationDepth * 0.035}
            size="large"
            depth={explorationDepth}
            showLabel={false}
            onInteract={deepen}
          />

          <BrainDepthRings explorationDepth={explorationDepth} onOpenDepth={jumpToDepth} />

          <div className="brain-depth-badge" aria-live="polite">
            <span>{t(language, "brain.level")} {explorationDepth}</span>
            <strong>{t(language, `brain.depth.${explorationDepth}`)}</strong>
          </div>

          <div className="brain-depth-actions" aria-label={t(language, "brain.aria.quickViews")}>
            <button type="button" className={explorationDepth === 2 ? "is-active" : ""} onClick={() => jumpToDepth(2)}>{t(language, "brain.view.memories")}</button>
            <button type="button" className={explorationDepth === 3 ? "is-active" : ""} onClick={() => jumpToDepth(3)}>{t(language, "brain.view.topics")}</button>
            <button type="button" className={explorationDepth === 4 ? "is-active" : ""} onClick={() => jumpToDepth(4)}>{t(language, "brain.view.relationships")}</button>
            <button type="button" className={explorationDepth === 5 ? "is-active" : ""} onClick={() => jumpToDepth(5)}>{t(language, "brain.view.graph")}</button>
          </div>

          <div className="brain-depth-rail" aria-label={t(language, "brain.depthRail.aria")}>
            {DEPTHS.map((depth) => (
              <button
                key={depth.level}
                type="button"
                className={depth.level <= explorationDepth ? "is-revealed" : ""}
                aria-current={depth.level === explorationDepth ? "step" : undefined}
                onClick={() => jumpToDepth(depth.level)}
              >
                <span>{depth.level}</span>
                <strong>{t(language, `brain.depth.${depth.level}`)}</strong>
              </button>
            ))}
          </div>

          <div className="brain-field-layer" aria-hidden={explorationDepth < 2}>
            <DepthEmergence
              depth={explorationDepth}
              memories={memoryFragments}
              concepts={knowledgeConcepts}
              relationships={relationshipThreads}
              graphModel={graphModel}
              graphSearch={graphSearch}
              selectedGraphId={selectedGraphId}
              onGraphSearch={setGraphSearch}
              onSelectGraphNode={setSelectedGraphId}
              onRecallMemory={recallMemory}
            />
          </div>

          {explorationDepth > 1 ? (
            <button className="brain-surface-control" type="button" onClick={surface}>
              {t(language, "brain.surface")}
            </button>
          ) : null}
        </div>
      </section>

      <BrainConversation
        language={language}
        explorationDepth={explorationDepth}
        modelName={modelName}
        messages={messages}
        starterPrompts={starterPrompts}
        memoryFeedback={memoryFeedback}
        ingestionStates={ingestionStates}
        emergenceEvents={emergenceEvents}
        draft={draft}
        streaming={streaming}
        imageData={imageData}
        streamRef={streamRef}
        memories={memoryFragments}
        concepts={knowledgeConcepts}
        readiness={brainReadiness}
        proof={brainProof}
        uploadingDocument={uploadingDocument}
        onOpenDepth={jumpToDepth}
        onDraftChange={setDraft}
        onImageDataChange={setImageData}
        onUploadDocument={(file) => void uploadDocument(file)}
        onConnectFolder={(path) => void connectFolder(path)}
        onIngestNote={(note) => void ingestNote(note)}
        onIngestWeb={(url) => void ingestWeb(url)}
        onVerifyModelContinuity={() => void verifyModelContinuity()}
        onSend={() => void send()}
      />
    </main>
  );
}

function BrainDepthRings({
  explorationDepth,
  onOpenDepth,
}: {
  explorationDepth: BrainDepth;
  onOpenDepth: (depth: BrainDepth) => void;
}) {
  const language = useAppStore((state) => state.language);
  return (
    <div className="brain-memory-rings" aria-label={t(language, "brain.rings.aria")}>
      {DEPTHS.map((depth, index) => (
        <button
          key={depth.level}
          type="button"
          className={`brain-memory-ring ring-${depth.level} ${depth.level <= explorationDepth ? "is-revealed" : ""}`}
          aria-current={depth.level === explorationDepth ? "step" : undefined}
          onClick={() => onOpenDepth(depth.level)}
          style={{ "--ring-delay": `${index * 70}ms` } as React.CSSProperties}
        >
          <span>{depth.level}</span>
          <strong>{t(language, `brain.rings.${depth.level}`)}</strong>
        </button>
      ))}
    </div>
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
    })),
  };
}
