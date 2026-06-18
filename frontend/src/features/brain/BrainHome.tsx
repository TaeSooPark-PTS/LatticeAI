import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { latticeApi } from "@/api/client";
import { type BrainState, LivingBrain, triggerBrainRecall } from "@/components/LivingBrain";
import { useAppStore } from "@/store/appStore";
import { t } from "@/i18n";
import { BrainConversation } from "./BrainConversation";
import { buildBrainProof, buildBrainReadiness, buildMemoryFragments, currentModelName, parseKnowledgeGraph } from "./brainData";
import { DepthEmergence } from "./DepthEmergence";
import { DEPTHS, type BrainDepth, type BrainProof, type MemoryFragment, type Message, type MessageProof } from "./types";

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
  const streamRef = React.useRef<HTMLDivElement>(null);
  const recallTimerRef = React.useRef<number | null>(null);

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

    try {
      const result = await latticeApi.uploadDocument(file);
      if (result.error) {
        setMemoryFeedback(t(language, "brain.upload.failed", { reason: result.error }));
        return;
      }

      setMemoryFeedback(t(language, "brain.upload.saved", { name: file.name }));
      setLastRecallQuery(file.name);
      triggerBrainRecall();
      void qc.invalidateQueries({ queryKey: ["memoryManager"] });
      void qc.invalidateQueries({ queryKey: ["graph"] });
      void qc.invalidateQueries({ queryKey: ["memoryBrainProof"] });
    } finally {
      setUploadingDocument(false);
    }
  }

  async function connectFolder(path: string) {
    const target = path.trim();
    if (!target) return;
    setMemoryFeedback(t(language, "brain.ingest.folder.pending", { path: target }));
    onBrainChange("recalling", 0.84);
    const result = await latticeApi.connectFolder(target);
    if (result.error || !result.ok) {
      setMemoryFeedback(t(language, "brain.ingest.folder.failed", { reason: result.error || "unavailable" }));
      return;
    }
    setMemoryFeedback(t(language, "brain.ingest.folder.saved", { path: target }));
    setLastRecallQuery(target);
    triggerBrainRecall();
    void refreshBrainProof(target);
  }

  async function ingestNote(note: string) {
    const content = note.trim();
    if (!content) return;
    setMemoryFeedback(t(language, "brain.ingest.note.pending"));
    onBrainChange("recalling", 0.84);
    const result = await latticeApi.ingestNote(content, content.slice(0, 80));
    if (result.error || !result.ok) {
      setMemoryFeedback(t(language, "brain.ingest.note.failed", { reason: result.error || "unavailable" }));
      return;
    }
    setMemoryFeedback(t(language, "brain.ingest.note.saved"));
    setLastRecallQuery(content.slice(0, 120));
    triggerBrainRecall();
    void refreshBrainProof(content.slice(0, 120));
  }

  async function ingestWeb(url: string) {
    const target = url.trim();
    if (!target) return;
    setMemoryFeedback(t(language, "brain.ingest.web.pending", { url: target }));
    onBrainChange("recalling", 0.84);
    const result = await latticeApi.browserReadUrl(target);
    if (result.error || !result.ok) {
      setMemoryFeedback(t(language, "brain.ingest.web.failed", { reason: result.error || "unavailable" }));
      return;
    }
    setMemoryFeedback(t(language, "brain.ingest.web.saved", { url: target }));
    setLastRecallQuery(target);
    triggerBrainRecall();
    void refreshBrainProof(target);
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
