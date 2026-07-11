import * as React from "react";
import { useQuery } from "@tanstack/react-query";

import { latticeApi } from "@/api/client";
import { type BrainState, triggerBrainRecall } from "@/components/LivingBrain";
import { t } from "@/i18n";
import { useAppStore } from "@/store/appStore";
import { BrainConversation } from "./BrainConversation";
import {
  buildBrainReadiness,
  buildMemoryFragments,
  currentModelName,
  hasLoadedModel,
  parseKnowledgeGraph,
} from "./brainData";
import { useConversationSession } from "./conversationSession";
import { useBrainChat } from "./hooks/useBrainChat";
import { useBrainHistory } from "./hooks/useBrainHistory";
import { useBrainIngestion } from "./hooks/useBrainIngestion";
import { useBrainProof } from "./hooks/useBrainProof";

export function BrainHome({
  brainState,
  intensity,
  onBrainChange,
}: {
  brainState: BrainState;
  intensity: number;
  onBrainChange: (state: BrainState, intensity?: number) => void;
}) {
  const language = useAppStore((state) => state.language);
  const sessionMessages = useConversationSession((state) => state.messages);
  const [memoryFeedback, setMemoryFeedback] = React.useState<string | null>(null);
  const memoriesQ = useQuery({ queryKey: ["memoryManager"], queryFn: latticeApi.memoryManager });
  const graphQ = useQuery({ queryKey: ["graphPreview"], queryFn: () => latticeApi.graphPreview(48) });
  const modelsQ = useQuery({ queryKey: ["models"], queryFn: latticeApi.models });
  const history = useBrainHistory({ language, setMemoryFeedback });

  const memoryFragments = React.useMemo(
    () => buildMemoryFragments(memoriesQ.data?.data, history.historyQ.data?.data),
    [history.historyQ.data, memoriesQ.data],
  );
  const graphModel = React.useMemo(() => parseKnowledgeGraph(graphQ.data?.data), [graphQ.data]);
  const knowledgeConcepts = React.useMemo(() => graphModel.nodes.slice(0, 10), [graphModel.nodes]);
  const brainReadiness = React.useMemo(
    () => buildBrainReadiness(memoriesQ.data?.data, memoryFragments.length, knowledgeConcepts.length),
    [knowledgeConcepts.length, memoriesQ.data, memoryFragments.length],
  );
  const modelName = React.useMemo(() => currentModelName(modelsQ.data?.data), [modelsQ.data]);
  const modelReady = React.useMemo(() => hasLoadedModel(modelsQ.data?.data), [modelsQ.data]);
  const proof = useBrainProof({
    language,
    messages: sessionMessages,
    modelName,
    setMemoryFeedback,
  });
  const ingestion = useBrainIngestion({
    language,
    brainReadiness,
    memoryFragments,
    graphModel,
    memoriesFetched: memoriesQ.isFetched,
    graphFetched: graphQ.isFetched,
    requestDetails: proof.requestDetails,
    setLastRecallQuery: proof.setLastRecallQuery,
    setMemoryFeedback,
    onBrainChange,
  });
  const chat = useBrainChat({
    language,
    modelReady,
    onBrainChange,
    setMemoryFeedback,
    beginIngestion: ingestion.beginIngestion,
    completeIngestion: ingestion.completeIngestion,
    failIngestion: ingestion.failIngestion,
    setLastRecallQuery: proof.setLastRecallQuery,
    attachAnswerProof: proof.attachAnswerProof,
  });
  const starterPrompts = React.useMemo(() => [
    t(language, "brain.prompt.remember"),
    t(language, "brain.prompt.know"),
    t(language, "brain.prompt.plan"),
  ], [language]);

  const openDepth = React.useCallback((depth: number) => {
    triggerBrainRecall();
    onBrainChange("recalling", 0.82);
    window.location.hash = depth <= 2 ? "/memory" : "/knowledge-graph";
  }, [onBrainChange]);

  const startNewConversation = React.useCallback(() => {
    if (chat.streaming) chat.stopStreaming();
    history.resetConversation();
    ingestion.resetChatIngestion();
    setMemoryFeedback(null);
    onBrainChange("idle", 0.58);
  }, [chat, history, ingestion, onBrainChange]);

  const hasRunningAutomation = chat.proactiveActivities.some((activity) => activity.status === "running");
  const hasActiveIngestion = Object.values(ingestion.ingestionStates).some(
    (state) => state && state.stage !== "complete" && state.stage !== "error",
  );
  const visibleBrainState: BrainState = hasRunningAutomation
    ? "acting"
    : chat.streaming && brainState === "recalling"
      ? "recalling"
      : chat.streaming
        ? "thinking"
        : hasActiveIngestion
          ? "recalling"
          : ingestion.synthesisActive
            ? "synthesizing"
            : chat.draft.trim()
              ? "listening"
              : "idle";
  const visibleBrainIntensity = visibleBrainState === "idle"
    ? 0.62
    : visibleBrainState === "listening"
      ? 0.76
      : Math.max(0.92, intensity);

  return (
    <main className="brain-home" aria-label={t(language, "brain.aria.home")}>
      <BrainConversation
        language={language}
        brainState={visibleBrainState}
        intensity={visibleBrainIntensity}
        modelName={modelName}
        modelReady={modelReady}
        messages={chat.messages}
        pastConversations={history.pastConversations}
        historyBusyId={history.historyBusyId}
        starterPrompts={starterPrompts}
        memoryFeedback={memoryFeedback}
        ingestionStates={ingestion.ingestionStates}
        emergenceEvents={ingestion.emergenceEvents}
        proactiveActivities={chat.proactiveActivities}
        draft={chat.draft}
        streaming={chat.streaming}
        imageData={chat.imageData}
        streamRef={chat.streamRef}
        memories={memoryFragments}
        graph={graphModel}
        concepts={knowledgeConcepts}
        relationshipCount={Math.max(brainReadiness.signals.relationshipCount, graphModel.edges.length)}
        readiness={brainReadiness}
        proof={proof.brainProof}
        brief={proof.brainBrief}
        uploadingDocument={ingestion.uploadingDocument}
        onOpenDepth={openDepth}
        onDraftChange={chat.setDraft}
        onImageDataChange={chat.setImageData}
        onUploadDocument={(file) => void ingestion.uploadDocument(file)}
        onPickFolder={() => void ingestion.pickFolder()}
        onConnectFolder={(path) => void ingestion.connectFolder(path)}
        onIngestNote={(note) => void ingestion.ingestNote(note)}
        onIngestWeb={(url) => void ingestion.ingestWeb(url)}
        onVerifyModelContinuity={() => void proof.verifyModelContinuity()}
        onSend={() => void chat.send()}
        onSendText={(text) => void chat.sendText(text)}
        onCreateActionItem={(content) => void chat.createActionItem(content)}
        onProactiveAction={(action) => void chat.handleProactiveAction(action)}
        onStop={chat.stopStreaming}
        onRegenerate={() => void chat.regenerate()}
        onNewConversation={startNewConversation}
        onResumeConversation={(id) => void history.resumeConversation(id, chat.streaming)}
        onDeleteConversation={(id) => void history.deleteConversation(id)}
        onExploreBrain={() => openDepth(5)}
        onRequestDetails={proof.requestDetails}
      />
    </main>
  );
}
