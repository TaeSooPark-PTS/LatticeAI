import * as React from "react";
import { CheckCircle2, Copy, DatabaseZap, ListTodo, MessageCirclePlus, RefreshCw, ShieldCheck, Sparkles } from "lucide-react";

import { type BrainState, LivingBrain } from "@/components/LivingBrain";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { useAppStore } from "@/store/appStore";
import { t, type Language } from "@/i18n";
import type {
  BrainBrief,
  BrainDepth,
  BrainProof,
  BrainReadiness,
  ConversationSummary,
  EmergenceEvent,
  IngestionSourceType,
  IngestionState,
  KnowledgeConcept,
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
  ProductCommandCenter,
} from "./HomePanels";
import { BrainIngestionPanel, IngestionTimelineSection } from "./IngestionPanels";
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
  draft,
  streaming,
  imageData,
  streamRef,
  memories,
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
  onConnectFolder,
  onIngestNote,
  onIngestWeb,
  onVerifyModelContinuity,
  onSend,
  onSendText,
  onCreateActionItem,
  onStop,
  onRegenerate,
  onNewConversation,
  onResumeConversation,
  onDeleteConversation,
  brainState,
  intensity,
  onExploreBrain,
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
  draft: string;
  streaming: boolean;
  imageData: string | null;
  streamRef: React.RefObject<HTMLDivElement | null>;
  memories: MemoryFragment[];
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
  onConnectFolder: (path: string) => void;
  onIngestNote: (note: string) => void;
  onIngestWeb: (url: string) => void;
  onVerifyModelContinuity: () => void;
  onSend: () => void;
  onSendText: (text: string) => void;
  onCreateActionItem: (content: string) => void;
  onStop: () => void;
  onRegenerate: () => void;
  onNewConversation: () => void;
  onResumeConversation: (id: string) => void;
  onDeleteConversation: (id: string) => void;
  onExploreBrain: () => void;
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
            </>
          ) : (
            <div className="brain-centered-home">
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

              <div className="brain-home-welcome">
                <h2>{t(language, "brain.firstScreen.title")}</h2>
                <p>{t(language, "brain.firstScreen.body")}</p>
                <div className="brain-home-status-badge">
                  <span className="status-dot" />
                  <span>
                    {t(language, `brain.firstScreen.state.${readiness.state}`)}
                    {isBasic ? "" : ` (${readiness.score}%)`}
                  </span>
                </div>
                {readiness.state === "quiet" && memories.length === 0 ? (
                  <p className="brain-home-waking-hint" role="note">
                    {t(language, "brain.home.wakingHint")}
                  </p>
                ) : null}
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

              {suggestedQuestions.length ? (
                <section className="brain-home-suggestions" aria-label={t(language, "brain.suggestions.aria")}>
                  <div className="brain-home-suggestions-head">
                    <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
                    <span>{t(language, "brain.suggestions.title")}</span>
                  </div>
                  <div className="brain-home-suggestions-grid">
                    {suggestedQuestions.map((question) => {
                      const prompt = t(language, question.promptKey, question.params);
                      return (
                        <button
                          key={question.id}
                          type="button"
                          disabled={streaming}
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
                  </div>
                </section>
              ) : (
                <div className="brain-home-prompts">
                  {starterPrompts.map((prompt) => (
                    <button key={prompt} type="button" onClick={() => onDraftChange(prompt)} className="brain-prompt-pill">
                      {prompt}
                    </button>
                  ))}
                </div>
              )}

              <PastConversationsPanel
                language={language}
                items={pastConversations}
                busyId={historyBusyId}
                onResume={onResumeConversation}
                onDelete={onDeleteConversation}
              />

              <BrainBriefPanel
                language={language}
                brief={brief}
                showEvidence={!isBasic}
                onAction={(action) => handleBriefAction(action, onVerifyModelContinuity)}
              />
            </div>
          )}

          {memoryFeedback ? (
            <div className="brain-save-feedback" role="status">
              <DatabaseZap className="h-3.5 w-3.5" />
              <span>{memoryFeedback}</span>
              <small>{t(language, "brain.saved.detail")}</small>
            </div>
          ) : null}

          <details className="brain-utility-drawer">
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
              <BrainIngestionPanel
                language={language}
                uploadingDocument={uploadingDocument}
                ingestionStates={ingestionStates}
                onUploadDocument={onUploadDocument}
                onConnectFolder={onConnectFolder}
                onIngestNote={onIngestNote}
                onIngestWeb={onIngestWeb}
              />
              {isBasic ? null : (
                <>
                  <ProductCommandCenter
                    language={language}
                    readiness={readiness}
                    proof={proof}
                    modelName={modelName}
                    memories={memories}
                    concepts={concepts}
                    emergenceEvents={emergenceEvents}
                    onOpenDepth={onOpenDepth}
                    onVerifyModelContinuity={onVerifyModelContinuity}
                  />
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
        </section>
      </div>
    </section>
  );
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
