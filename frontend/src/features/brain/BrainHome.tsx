import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { latticeApi } from "@/api/client";
import { type BrainState, LivingBrain, triggerBrainRecall } from "@/components/LivingBrain";
import { useAppStore } from "@/store/appStore";
import { t } from "@/i18n";
import { BrainConversation } from "./BrainConversation";
import { buildBrainReadiness, buildMemoryFragments, currentModelName, parseKnowledgeGraph } from "./brainData";
import { DepthEmergence } from "./DepthEmergence";
import { DEPTHS, type BrainDepth, type MemoryFragment, type Message } from "./types";

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
  const streamRef = React.useRef<HTMLDivElement>(null);
  const recallTimerRef = React.useRef<number | null>(null);

  const memoriesQ = useQuery({ queryKey: ["memoryManager"], queryFn: latticeApi.memoryManager });
  const historyQ = useQuery({ queryKey: ["chatHistory"], queryFn: latticeApi.chatHistory });
  const graphQ = useQuery({ queryKey: ["graph"], queryFn: latticeApi.graph });
  const modelsQ = useQuery({ queryKey: ["models"], queryFn: latticeApi.models });

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
    };
  }, []);

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
      }
    } finally {
      setStreaming(false);
      void qc.invalidateQueries({ queryKey: ["chatHistory"] });
      void qc.invalidateQueries({ queryKey: ["memoryManager"] });
      void qc.invalidateQueries({ queryKey: ["graph"] });
    }
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
        draft={draft}
        streaming={streaming}
        imageData={imageData}
        streamRef={streamRef}
        memories={memoryFragments}
        concepts={knowledgeConcepts}
        readiness={brainReadiness}
        onOpenDepth={jumpToDepth}
        onDraftChange={setDraft}
        onImageDataChange={setImageData}
        onSend={() => void send()}
      />
    </main>
  );
}
