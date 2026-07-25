import * as React from "react";
import { CheckCircle2, Copy, DatabaseZap, History, ListTodo, MessageCirclePlus, RefreshCw, ShieldCheck, Sparkles, X } from "lucide-react";

import { type BrainState, LivingBrain } from "@/components/LivingBrain";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { useAppStore } from "@/store/appStore";
import { t, type Language } from "@/i18n";
import type {
  BrainBrief,
  BrainDepth,
  BrainProactiveAction,
  BrainProactiveActivity,
  BrainProof,
  BrainReadiness,
  ConversationSummary,
  EmergenceEvent,
  IngestionSourceType,
  IngestionState,
  KnowledgeConcept,
  KnowledgeGraphModel,
  MemoryFragment,
  Message,
} from "./types";
import { AgentApprovalCard } from "./AgentApprovalCard";
import { AgentStepTimeline, LoopRepairsNote } from "./AgentStepTimeline";
import type { ApprovalResolution } from "./approvalFlow";
import { AnswerProofCard, InlineCitationMarkers } from "./AnswerProof";
import { BrainCarePanel } from "./BrainCarePanel";
import { BrainIntelligencePanel } from "./BrainIntelligencePanel";
import { DailyBriefingPanel } from "@/features/command/DailyBriefingPanel";
import { PendingProposalsPanel } from "@/features/command/PendingProposalsPanel";
import { BrainComposer } from "./BrainComposer";
import { BrainOverviewPanel } from "./BrainOverviewPanel";
import { FirstFiveCard } from "./FirstFiveCard";
import type { FirstFiveStep } from "./firstFive";
import { FirstValueLoopCard } from "./FirstValueLoopCard";
import {
  BrainBriefPanel,
  handleBriefAction,
  ModelContinuityDemo,
  ModelMissingNotice,
  PastConversationsPanel,
} from "./HomePanels";
import { BrainIngestionDock, BrainIngestionPanel, IngestionTimelineSection } from "./IngestionPanels";
import { IngestionJobsPanel, PendingApprovalsNotice, VectorFreshnessNotice, WatchHealthCard } from "./BrainSignals";
import { BrainKnowledgeFlow, BrainMemoryAutomation, ConversationKnowledgeTrace } from "./BrainKnowledgeFlow";
import { CreatedFilesCard, MessageBody } from "./MessageMarkdown";
import { MemoryRings } from "./MemoryRings";
import { navigateHash } from "./navigation";

export function BrainConversation({
  language,
  modelName,
  modelReady,
  messages,
  pastConversations,
  historyBusyId,
  starterPrompts,
  memoryFeedback,
  ingestionStates,
  emergenceEvents,
  proactiveActivities,
  draft,
  streaming,
  imageData,
  streamRef,
  memories,
  graph,
  concepts,
  relationshipCount,
  readiness,
  proof,
  brief,
  uploadingDocument,
  onOpenDepth,
  onDraftChange,
  onImageDataChange,
  onUploadDocument,
  onPickFolder,
  onConnectFolder,
  onIngestNote,
  onIngestWeb,
  onVerifyModelContinuity,
  onSend,
  onSendText,
  onCreateActionItem,
  onProactiveAction,
  onApprovalResolved,
  onStop,
  onRegenerate,
  onNewConversation,
  onResumeConversation,
  onDeleteConversation,
  brainState,
  intensity,
  onExploreBrain,
  onRequestDetails,
}: {
  language: Language;
  brainState: BrainState;
  intensity: number;
  modelName: string;
  modelReady: boolean;
  messages: Message[];
  pastConversations: ConversationSummary[];
  historyBusyId: string | null;
  starterPrompts: string[];
  memoryFeedback: string | null;
  ingestionStates: Record<IngestionSourceType, IngestionState | null>;
  emergenceEvents: EmergenceEvent[];
  proactiveActivities: BrainProactiveActivity[];
  draft: string;
  streaming: boolean;
  imageData: string | null;
  streamRef: React.RefObject<HTMLDivElement | null>;
  memories: MemoryFragment[];
  graph: KnowledgeGraphModel;
  concepts: KnowledgeConcept[];
  relationshipCount: number;
  readiness: BrainReadiness;
  proof: BrainProof;
  brief: BrainBrief;
  uploadingDocument: boolean;
  onOpenDepth: (depth: BrainDepth) => void;
  onDraftChange: (value: string) => void;
  onImageDataChange: (value: string | null) => void;
  onUploadDocument: (file: File) => void;
  onPickFolder: () => void;
  onConnectFolder: (path: string) => void;
  onIngestNote: (note: string) => void;
  onIngestWeb: (url: string) => void;
  onVerifyModelContinuity: () => void;
  onSend: () => void;
  onSendText: (text: string) => void;
  onCreateActionItem: (content: string) => void;
  onProactiveAction: (action: BrainProactiveAction) => void;
  onApprovalResolved: (messageIndex: number, resolution: ApprovalResolution) => void;
  onStop: () => void;
  onRegenerate: () => void;
  onNewConversation: () => void;
  onResumeConversation: (id: string) => void;
  onDeleteConversation: (id: string) => void;
  onExploreBrain: () => void;
  onRequestDetails: () => void;
}) {
  const hasMessages = messages.length > 0;
  const mode = useAppStore((state) => state.mode);
  const isBasic = mode === "basic";
  const lastAssistantIndex = findLastAssistantIndex(messages);
  const suggestedQuestions = brief.suggestedQuestions.slice(0, 3);

  // First-five guided card wiring: each step drives a real, already-existing
  // surface on this screen (composer, ingestion dock, insights shelf).
  const homeDeckRef = React.useRef<HTMLDivElement>(null);
  const sourceDockRef = React.useRef<HTMLDivElement>(null);
  const insightsShelfRef = React.useRef<HTMLDetailsElement>(null);

  const firstFiveAutoDone = React.useMemo(
    () => ({
      // Any past conversation means the user already asked the Brain something.
      ask: pastConversations.length > 0,
      // "Add" completes only when a non-chat source really lands in the Brain.
      add: [ingestionStates.file, ingestionStates.folder, ingestionStates.note, ingestionStates.web].some(Boolean),
    }),
    [pastConversations.length, ingestionStates],
  );

  // Shared "take me to the ingestion dock" behavior: used by the first-five
  // "add" step and the First Value Loop "내 폴더 연결하기" CTA.
  const focusSourceDock = React.useCallback(() => {
    const dock = sourceDockRef.current;
    if (!dock) return;
    dock.scrollIntoView?.({ behavior: "smooth", block: "center" });
    window.setTimeout(() => {
      dock.querySelector<HTMLElement>("button, input, [tabindex]")?.focus({ preventScroll: true });
    }, 0);
  }, []);

  // Approval runs already represented by an inline card — the pending-
  // approvals notice only surfaces runs that would otherwise be invisible.
  const knownApprovalRunIds = React.useMemo(
    () => messages.flatMap((message) => (message.approval ? [message.approval.runId] : [])),
    [messages],
  );

  const handleFirstFiveStep = React.useCallback(
    (step: FirstFiveStep) => {
      if (step === "ask") {
        const prompt = starterPrompts[0] || t(language, "brain.prompt.remember");
        onDraftChange(prompt);
        window.setTimeout(() => {
          homeDeckRef.current?.querySelector<HTMLTextAreaElement>(".brain-composer textarea")?.focus();
        }, 0);
      } else if (step === "add") {
        focusSourceDock();
      } else {
        const shelf = insightsShelfRef.current;
        if (!shelf) return;
        shelf.open = true;
        shelf.scrollIntoView?.({ behavior: "smooth", block: "nearest" });
        window.setTimeout(() => {
          shelf.querySelector<HTMLElement>(".brain-home-shelf-close")?.focus({ preventScroll: true });
        }, 0);
      }
    },
    [starterPrompts, language, onDraftChange, focusSourceDock],
  );

  return (
    <section className="brain-conversation" aria-label={t(language, "brain.aria.conversation")}>
      <div className="brain-chat-home-layout">
        <section className={`brain-chat-home-card ${hasMessages ? "has-messages" : "is-empty-home"}`} aria-label={t(language, "brain.chatHome.aria")}>
          <VectorFreshnessNotice language={language} />
          <PendingApprovalsNotice language={language} knownRunIds={knownApprovalRunIds} />
          {hasMessages ? (
            <>
              <header className="brain-chat-header">
                <div className="brain-header-presence">
                  <LivingBrain
                    state={brainState}
                    intensity={intensity}
                    size="trace"
                    showLabel={false}
                    onInteract={onExploreBrain}
                  />
                  <div className="brain-header-title">
                    <strong>{t(language, "brain.title")}</strong>
                    <span>
                      {t(language, `brain.firstScreen.state.${readiness.state}`)}
                      {isBasic ? "" : ` (${readiness.score}%)`}
                    </span>
                  </div>
                </div>
                <div className="brain-header-actions">
                  {isBasic ? null : <div className="brain-model-pill">{modelName}</div>}
                  <button type="button" className="brain-deeper-btn" onClick={onNewConversation}>
                    <MessageCirclePlus className="h-3.5 w-3.5" aria-hidden="true" />
                    {t(language, "brain.newChat")}
                  </button>
                  <button type="button" className="brain-deeper-btn" onClick={onExploreBrain}>
                    {t(language, "brain.firstScreen.action.graph")}
                  </button>
                </div>
              </header>

              <ConversationKnowledgeTrace
                language={language}
                state={ingestionStates.chat}
                concepts={concepts}
                relationshipCount={relationshipCount}
                onExploreBrain={onExploreBrain}
              />

              <div ref={streamRef} className="brain-stream">
                {messages.map((message, index) => {
                  const messageId = `brain-msg-${index}`;
                  const proof = message.role === "assistant" ? message.proof : undefined;
                  const showActions = message.role === "assistant" && Boolean(message.content.trim()) && !streaming;
                  const canFollowUp = showActions && index === lastAssistantIndex;
                  return (
                    <div key={`${message.role}-${index}`} className={`brain-message ${message.role}`}>
                      <div className="brain-message-bubble">
                        {message.role === "assistant" ? (
                          <MessageBody language={language} content={message.content} />
                        ) : (
                          message.content
                        )}
                        {proof && proof.citations.length ? (
                          <InlineCitationMarkers language={language} proof={proof} messageId={messageId} />
                        ) : null}
                        {message.role === "assistant" && message.contextQuality?.limited ? (
                          <ContextQualityNote
                            language={language}
                            reason={isBasic ? null : message.contextQuality.reason}
                          />
                        ) : null}
                        {message.role === "assistant" && message.content.trim() && message.grounding ? (
                          <GroundingBadge language={language} grounding={message.grounding} />
                        ) : null}
                      </div>
                      {message.role === "assistant" && message.approval ? (
                        <AgentApprovalCard
                          language={language}
                          approval={message.approval}
                          onResolved={(resolution) => onApprovalResolved(index, resolution)}
                          onReplan={onSendText}
                        />
                      ) : null}
                      {message.role === "assistant" && message.agentSteps?.length ? (
                        <AgentStepTimeline
                          language={language}
                          steps={message.agentSteps}
                          streaming={streaming && index === messages.length - 1}
                        />
                      ) : null}
                      {showActions ? (
                        <MessageActions
                          language={language}
                          content={message.content}
                          canRegenerate={index === lastAssistantIndex}
                          onRegenerate={onRegenerate}
                          canFollowUp={canFollowUp}
                          onFollowUp={onSendText}
                          onCreateActionItem={onCreateActionItem}
                        />
                      ) : null}
                      {message.files?.length ? <CreatedFilesCard language={language} files={message.files} /> : null}
                      {message.role === "assistant" && message.agentState ? (
                        <AgentStateNote language={language} state={message.agentState} />
                      ) : null}
                      {message.role === "assistant" && message.loopSummary ? (
                        <LoopRepairsNote language={language} summary={message.loopSummary} />
                      ) : null}
                      {proof ? <AnswerProofCard language={language} proof={proof} messageId={messageId} /> : null}
                    </div>
                  );
                })}
              </div>

              {modelReady ? null : <ModelMissingNotice language={language} />}
              <BrainComposer
                language={language}
                draft={draft}
                streaming={streaming}
                imageData={imageData}
                uploadingDocument={uploadingDocument}
                onDraftChange={onDraftChange}
                onImageDataChange={onImageDataChange}
                onUploadDocument={onUploadDocument}
                onSend={onSend}
                onStop={onStop}
              />
              <BrainMemoryAutomation
                language={language}
                brief={brief}
                activities={proactiveActivities}
                streaming={streaming}
                onAction={onProactiveAction}
              />
            </>
          ) : (
            <div className="brain-centered-home" data-testid="brain-home-stage">
              <BrainKnowledgeFlow
                language={language}
                brainState={brainState}
                intensity={intensity}
                graph={graph}
                readiness={readiness}
                brief={brief}
                memories={memories}
                ingestionStates={ingestionStates}
                emergenceEvents={emergenceEvents}
                streaming={streaming}
                onExploreBrain={onExploreBrain}
              />

              {modelReady ? null : <ModelMissingNotice language={language} />}

              {suggestedQuestions.length ? (
                <section className="brain-home-prompt-strip" aria-label={t(language, "brain.suggestions.aria")}>
                  <span className="brain-home-prompt-strip-label">
                    <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
                    {t(language, "brain.suggestions.title")}
                  </span>
                  {suggestedQuestions.map((question) => {
                    const prompt = t(language, question.promptKey, question.params);
                    return (
                      <button
                        key={question.id}
                        type="button"
                        disabled={streaming}
                        title={t(language, question.detailKey)}
                        onClick={() => {
                          onDraftChange("");
                          onSendText(prompt);
                        }}
                      >
                        <strong>{t(language, question.labelKey)}</strong>
                        <span>{t(language, question.detailKey)}</span>
                      </button>
                    );
                  })}
                </section>
              ) : (
                <div className="brain-home-prompt-strip" aria-label={t(language, "brain.suggestions.aria")}>
                  <span className="brain-home-prompt-strip-label">
                    <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
                    {t(language, "brain.suggestions.title")}
                  </span>
                  {starterPrompts.slice(0, 3).map((prompt) => (
                    <button key={prompt} type="button" onClick={() => onDraftChange(prompt)} className="brain-prompt-pill">
                      {prompt}
                    </button>
                  ))}
                </div>
              )}

              <div className="brain-home-control-deck" ref={homeDeckRef}>
                <div className="brain-live-source-panel" ref={sourceDockRef}>
                  <BrainIngestionDock
                    language={language}
                    uploadingDocument={uploadingDocument}
                    ingestionStates={ingestionStates}
                    onUploadDocument={onUploadDocument}
                    onPickFolder={onPickFolder}
                    onConnectFolder={onConnectFolder}
                    onIngestNote={onIngestNote}
                    onIngestWeb={onIngestWeb}
                  />
                  <WatchHealthCard language={language} />
                  <IngestionJobsPanel language={language} />
                </div>

                <BrainComposer
                  language={language}
                  draft={draft}
                  streaming={streaming}
                  imageData={imageData}
                  uploadingDocument={uploadingDocument}
                  onDraftChange={onDraftChange}
                  onImageDataChange={onImageDataChange}
                  onUploadDocument={onUploadDocument}
                  onSend={onSend}
                  onStop={onStop}
                />

                <aside className="brain-home-action-dock" aria-label={t(language, "brain.automation.title")}>
                  <BrainMemoryAutomation
                    language={language}
                    brief={brief}
                    activities={proactiveActivities}
                    streaming={streaming}
                    onAction={onProactiveAction}
                    compact
                  />

                  <div className="brain-home-shelves">
                    <details
                      className="brain-home-history-shelf"
                      data-testid="brain-history-shelf"
                      onKeyDown={(event) => {
                        if (event.key !== "Escape") return;
                        event.preventDefault();
                        closeHomeShelf(event.currentTarget);
                      }}
                    >
                      <summary>
                        <History className="h-3.5 w-3.5" aria-hidden="true" />
                        <span>{t(language, "brain.history.title")}</span>
                        <small>{pastConversations.length}</small>
                      </summary>
                      <div className="brain-home-shelf-popover">
                        <button
                          type="button"
                          className="brain-home-shelf-close"
                          aria-label={t(language, "brain.home.shelf.close")}
                          onClick={(event) => closeHomeShelf(event.currentTarget)}
                        >
                          <X className="h-4 w-4" aria-hidden="true" />
                        </button>
                        <PastConversationsPanel
                          language={language}
                          items={pastConversations}
                          busyId={historyBusyId}
                          onResume={onResumeConversation}
                          onDelete={onDeleteConversation}
                        />
                      </div>
                    </details>

                    <details
                      ref={insightsShelfRef}
                      className="brain-home-insights"
                      data-testid="brain-insights-shelf"
                      onToggle={(event) => event.currentTarget.open && onRequestDetails()}
                      onKeyDown={(event) => {
                        if (event.key !== "Escape") return;
                        if (event.currentTarget.querySelector("#brain-ring-peek")) return;
                        event.preventDefault();
                        closeHomeShelf(event.currentTarget);
                      }}
                    >
                      <summary>
                        <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
                        <span>{t(language, "brain.home.insights")}</span>
                      </summary>
                      <div className="brain-home-shelf-popover brain-home-insights-content">
                        <button
                          type="button"
                          className="brain-home-shelf-close"
                          aria-label={t(language, "brain.home.shelf.close")}
                          onClick={(event) => closeHomeShelf(event.currentTarget)}
                        >
                          <X className="h-4 w-4" aria-hidden="true" />
                        </button>
                        <MemoryRings
                          language={language}
                          brainState={brainState}
                          intensity={intensity}
                          readiness={readiness}
                          memories={memories}
                          concepts={concepts}
                          relationshipCount={relationshipCount}
                          onExploreBrain={onExploreBrain}
                          onOpenDepth={onOpenDepth}
                        />

                        <BrainBriefPanel
                          language={language}
                          brief={brief}
                          showEvidence={!isBasic}
                          onAction={(action) => handleBriefAction(action, onVerifyModelContinuity)}
                        />

                        {isBasic ? null : (
                          <>
                            <IngestionTimelineSection language={language} emergenceEvents={emergenceEvents} />
                            <ModelContinuityDemo
                              language={language}
                              proof={proof}
                              modelName={modelName}
                              onVerify={onVerifyModelContinuity}
                            />
                            <BrainOverviewPanel
                              memories={memories}
                              concepts={concepts}
                              readiness={readiness}
                              proof={proof}
                              onOpenDepth={onOpenDepth}
                            />
                          </>
                        )}
                        {/* Daily briefing now lives directly on the empty home
                            (brain-home-intro-row), so the shelf only keeps the
                            remaining insight panels. */}
                        <PendingProposalsPanel language={language} />
                        <BrainIntelligencePanel language={language} />
                        <BrainCarePanel language={language} />
                      </div>
                    </details>
                  </div>
                </aside>
              </div>

              {/* First-run guidance + today's briefing live below the primary
                  controls so compact viewports keep the composer and docks
                  fully on screen. */}
              <div className="brain-home-intro-row">
                <FirstValueLoopCard
                  language={language}
                  streaming={streaming}
                  onSendText={onSendText}
                  onConnectData={focusSourceDock}
                />
                <FirstFiveCard language={language} autoDone={firstFiveAutoDone} onStep={handleFirstFiveStep} />
                <DailyBriefingPanel language={language} variant="home" />
              </div>
            </div>
          )}

          {memoryFeedback ? (
            <div className="brain-save-feedback" role="status">
              <DatabaseZap className="h-3.5 w-3.5" />
              <span>{memoryFeedback}</span>
              <small>{t(language, "brain.saved.detail")}</small>
            </div>
          ) : null}

          {hasMessages ? <details className="brain-utility-drawer" onToggle={(event) => event.currentTarget.open && onRequestDetails()}>
            <summary>{t(language, isBasic ? "brain.chatHome.utility.basic" : "brain.chatHome.utility")}</summary>
            <div className="brain-utility-tools" aria-label={t(language, "brain.chatHome.contextAria")}>
              <LanguageSwitcher compact />
              {isBasic ? null : (
                <>
                  <div className="brain-model-pill">{modelName}</div>
                  <button className="brain-admin-link" type="button" onClick={() => navigateHash("/admin")}>
                    <ShieldCheck className="h-3.5 w-3.5" />
                    {t(language, "brain.admin")}
                  </button>
                </>
              )}
              <span><CheckCircle2 className="h-3.5 w-3.5" />{t(language, "brain.local")}</span>
              <span><CheckCircle2 className="h-3.5 w-3.5" />{t(language, "brain.private")}</span>
              <span><CheckCircle2 className="h-3.5 w-3.5" />{t(language, "brain.portable")}</span>
            </div>
            <div className="brain-utility-grid">
              {hasMessages ? (
                <>
                  <BrainIngestionPanel
                    language={language}
                    uploadingDocument={uploadingDocument}
                    ingestionStates={ingestionStates}
                    onUploadDocument={onUploadDocument}
                    onPickFolder={onPickFolder}
                    onConnectFolder={onConnectFolder}
                    onIngestNote={onIngestNote}
                    onIngestWeb={onIngestWeb}
                  />
                  <IngestionJobsPanel language={language} />
                </>
              ) : null}
              {isBasic ? null : (
                <>
                  <IngestionTimelineSection language={language} emergenceEvents={emergenceEvents} />
                  <ModelContinuityDemo
                    language={language}
                    proof={proof}
                    modelName={modelName}
                    onVerify={onVerifyModelContinuity}
                  />
                  <BrainOverviewPanel
                    memories={memories}
                    concepts={concepts}
                    readiness={readiness}
                    proof={proof}
                    onOpenDepth={onOpenDepth}
                  />
                </>
              )}
              <DailyBriefingPanel language={language} />
              <PendingProposalsPanel language={language} />
              <BrainIntelligencePanel language={language} />
              <BrainCarePanel language={language} />
            </div>
          </details> : null}
        </section>
      </div>
    </section>
  );
}

// Honest signaling: a quiet inline note when the Brain answered with limited
// graph-backed context, so users know how much to trust the recall behind it.
// Terminal non-success states get a visually distinct warning strip so a
// NEEDS_REVIEW/FAILED run can never be mistaken for a completed one.
function AgentStateNote({ language, state }: { language: Language; state: string }) {
  const key = state === "FAILED" ? "brain.agent.failed" : "brain.agent.needsReview";
  return (
    <p
      className={`brain-agent-state-note ${state === "FAILED" ? "is-failed" : "is-review"}`}
      role="alert"
      data-testid="agent-state-note"
    >
      {t(language, key)}
    </p>
  );
}

// Small, unobtrusive answer-citation verdict: whether the reply actually used
// retrieved sources ("근거 있음") or not ("근거 없음"). Copy comes from i18n —
// the backend's Korean label field is never rendered directly.
function GroundingBadge({ language, grounding }: { language: Language; grounding: NonNullable<Message["grounding"]> }) {
  const supported = grounding.status === "supported";
  return (
    <span
      className={`brain-grounding-badge ${supported ? "is-supported" : "is-none"}`}
      role="note"
      data-testid="grounding-badge"
      title={grounding.reason || undefined}
    >
      {t(language, supported ? "brain.grounding.supported" : "brain.grounding.none")}
    </span>
  );
}

function ContextQualityNote({ language, reason }: { language: Language; reason: string | null }) {
  return (
    <p className="brain-context-quality-note" role="note" data-testid="context-quality-note">
      <span>{t(language, "brain.contextQuality.limited")}</span>
      {reason ? <small>{t(language, "brain.contextQuality.reason", { reason })}</small> : null}
    </p>
  );
}

function closeHomeShelf(target: HTMLElement) {
  const details = target instanceof HTMLDetailsElement ? target : target.closest("details");
  if (!details) return;
  details.removeAttribute("open");
  details.querySelector<HTMLElement>("summary")?.focus();
}

function findLastAssistantIndex(messages: Message[]): number {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index].role === "assistant") return index;
  }
  return -1;
}

// Per-answer actions users expect from every chat product: copy the whole
// reply, and regenerate the latest answer without retyping the question.
function MessageActions({
  language,
  content,
  canRegenerate,
  onRegenerate,
  canFollowUp,
  onFollowUp,
  onCreateActionItem,
}: {
  language: Language;
  content: string;
  canRegenerate: boolean;
  onRegenerate: () => void;
  canFollowUp: boolean;
  onFollowUp: (text: string) => void;
  onCreateActionItem: (content: string) => void;
}) {
  const [copied, setCopied] = React.useState(false);
  const followUps = React.useMemo(
    () => [
      { labelKey: "brain.message.followup.checklist", promptKey: "brain.message.followup.checklist.prompt" },
      { labelKey: "brain.message.followup.evidence", promptKey: "brain.message.followup.evidence.prompt" },
      { labelKey: "brain.message.followup.next", promptKey: "brain.message.followup.next.prompt" },
    ],
    [],
  );

  async function copy() {
    try {
      await navigator.clipboard?.writeText(content);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {}
  }

  return (
    <div className="brain-message-actions">
      <button type="button" aria-label={t(language, "brain.message.copyAria")} onClick={() => void copy()}>
        <Copy className="h-3 w-3" aria-hidden="true" />
        {copied ? t(language, "brain.message.copied") : t(language, "brain.message.copy")}
      </button>
      {canRegenerate ? (
        <button type="button" aria-label={t(language, "brain.message.regenerateAria")} onClick={onRegenerate}>
          <RefreshCw className="h-3 w-3" aria-hidden="true" />
          {t(language, "brain.message.regenerate")}
        </button>
      ) : null}
      {canFollowUp ? (
        <button type="button" aria-label={t(language, "brain.message.saveTaskAria")} onClick={() => onCreateActionItem(content)}>
          <ListTodo className="h-3 w-3" aria-hidden="true" />
          {t(language, "brain.message.saveTask")}
        </button>
      ) : null}
      {canFollowUp ? (
        <div className="brain-message-followups" aria-label={t(language, "brain.message.followup.aria")}>
          {followUps.map((item) => (
            <button key={item.promptKey} type="button" onClick={() => onFollowUp(t(language, item.promptKey))}>
              {t(language, item.labelKey)}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
