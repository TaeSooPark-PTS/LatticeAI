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
import { AnswerProofCard, InlineCitationMarkers } from "./AnswerProof";
import { BrainCarePanel } from "./BrainCarePanel";
import { BrainComposer } from "./BrainComposer";
import { BrainOverviewPanel } from "./BrainOverviewPanel";
import {
  BrainBriefPanel,
  handleBriefAction,
  ModelContinuityDemo,
  ModelMissingNotice,
  PastConversationsPanel,
} from "./HomePanels";
import { BrainIngestionDock, BrainIngestionPanel, IngestionTimelineSection } from "./IngestionPanels";
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

  return (
    <section className="brain-conversation" aria-label={t(language, "brain.aria.conversation")}>
      <div className="brain-chat-home-layout">
        <section className={`brain-chat-home-card ${hasMessages ? "has-messages" : "is-empty-home"}`} aria-label={t(language, "brain.chatHome.aria")}>
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
                    <strong>Lattice Brain</strong>
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
                      </div>
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

              <div className="brain-home-control-deck">
                <div className="brain-live-source-panel">
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
                        <BrainCarePanel language={language} />
                      </div>
                    </details>
                  </div>
                </aside>
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
              <BrainCarePanel language={language} />
            </div>
          </details> : null}
        </section>
      </div>
    </section>
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
