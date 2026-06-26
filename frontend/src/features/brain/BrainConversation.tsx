import * as React from "react";
import {
  ArrowRight,
  BrainCircuit,
  CheckCircle2,
  Cpu,
  DatabaseZap,
  FileText,
  FileUp,
  FolderPlus,
  Globe2,
  HardDrive,
  Loader2,
  Repeat2,
  Search,
  ShieldCheck,
  Sparkles,
} from "lucide-react";

import { type BrainState, LivingBrain } from "@/components/LivingBrain";

const INGESTION_TYPE_LABEL_KEY: Record<IngestionSourceType, string> = {
  file: "brain.ingest.type.file",
  folder: "brain.ingest.type.folder",
  note: "brain.ingest.type.note",
  web: "brain.ingest.type.web",
};
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { t, type Language } from "@/i18n";
import {
  INGESTION_STAGE_ORDER,
  type BrainBrief,
  type BrainBriefAction,
  type BrainDepth,
  type BrainProof,
  type BrainReadiness,
  type EmergenceEvent,
  type IngestionPipelineStage,
  type IngestionSourceType,
  type IngestionState,
  type KnowledgeConcept,
  type MemoryFragment,
  type Message,
} from "./types";
import { BrainCarePanel } from "./BrainCarePanel";
import { BrainComposer } from "./BrainComposer";
import { BrainOverviewPanel } from "./BrainOverviewPanel";

export function BrainConversation({
  language,
  modelName,
  messages,
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
  brainState,
  intensity,
  onExploreBrain,
}: {
  language: Language;
  brainState: BrainState;
  intensity: number;
  modelName: string;
  messages: Message[];
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
  onExploreBrain: () => void;
}) {
  const hasMessages = messages.length > 0;

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
                    <span>{t(language, `brain.firstScreen.state.${readiness.state}`)} ({readiness.score}%)</span>
                  </div>
                </div>
                <div className="brain-header-actions">
                  <div className="brain-model-pill">{modelName}</div>
                  <button type="button" className="brain-deeper-btn" onClick={onExploreBrain}>
                    {t(language, "brain.firstScreen.action.graph")}
                  </button>
                </div>
              </header>

              <div ref={streamRef} className="brain-stream">
                {messages.map((message, index) => {
                  const messageId = `brain-msg-${index}`;
                  const proof = message.role === "assistant" ? message.proof : undefined;
                  return (
                    <div key={`${message.role}-${index}`} className={`brain-message ${message.role}`}>
                      <div className="brain-message-bubble">
                        {message.content}
                        {proof && proof.citations.length ? (
                          <InlineCitationMarkers language={language} proof={proof} messageId={messageId} />
                        ) : null}
                      </div>
                      {proof ? <AnswerProofCard language={language} proof={proof} messageId={messageId} /> : null}
                    </div>
                  );
                })}
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
              />
            </>
          ) : (
            <div className="brain-centered-home">
              <div className="brain-concentric-container">
                <div className="brain-concentric-ring ring-4" title={t(language, "brain.depth.5")}>
                  <span className="ring-label">{t(language, "brain.depthLabel.5")}</span>
                </div>
                <div className="brain-concentric-ring ring-3" title={`${concepts.length} Topics`}>
                  <span className="ring-label">{concepts.length} {t(language, "brain.overview.topics")}</span>
                </div>
                <div className="brain-concentric-ring ring-2" title={`${memories.length} Memories`}>
                  <span className="ring-label">{memories.length} {t(language, "brain.overview.recent")}</span>
                </div>
                <div className="brain-concentric-ring ring-1" title={t(language, "brain.depthTitle.1")}>
                  <span className="ring-label">{t(language, "brain.depth.1")}</span>
                </div>

                <div className="brain-center-orb">
                  <LivingBrain
                    state={brainState}
                    intensity={intensity}
                    size="large"
                    depth={readiness.depth || Math.max(1, Math.floor(readiness.score / 20))}
                    showLabel={false}
                    className="brain-home-presence"
                    onInteract={onExploreBrain}
                  />
                </div>
              </div>

              <div className="brain-home-welcome">
                <h2>{t(language, "brain.firstScreen.title")}</h2>
                <p>{t(language, "brain.firstScreen.body")}</p>
                <div className="brain-home-status-badge">
                  <span className="status-dot" />
                  <span>{t(language, `brain.firstScreen.state.${readiness.state}`)} ({readiness.score}%)</span>
                </div>
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
              />

              <div className="brain-home-prompts">
                {starterPrompts.map((prompt) => (
                  <button key={prompt} type="button" onClick={() => onDraftChange(prompt)} className="brain-prompt-pill">
                    {prompt}
                  </button>
                ))}
              </div>

              <BrainBriefPanel
                language={language}
                brief={brief}
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
            <summary>{t(language, "brain.chatHome.utility")}</summary>
            <div className="brain-utility-tools" aria-label={t(language, "brain.chatHome.contextAria")}>
              <LanguageSwitcher compact />
              <div className="brain-model-pill">{modelName}</div>
              <button className="brain-admin-link" type="button" onClick={() => navigateHash("/admin")}>
                <ShieldCheck className="h-3.5 w-3.5" />
                {t(language, "brain.admin")}
              </button>
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
              <BrainCarePanel language={language} />
            </div>
          </details>
        </section>
      </div>
    </section>
  );
}

function handleBriefAction(action: BrainBriefAction, onVerifyModelContinuity: () => void) {
  if (action.id === "ask_brain") {
    focusComposer();
    return;
  }
  if (action.id === "verify_model") {
    onVerifyModelContinuity();
    return;
  }
  if (action.route) {
    navigateHash(action.route);
  }
}

function BrainBriefPanel({
  language,
  brief,
  onAction,
}: {
  language: Language;
  brief: BrainBrief;
  onAction: (action: BrainBriefAction) => void;
}) {
  const focusTitle = brief.focus.title || t(language, "brain.brief.focus.empty");
  const focusDetail = brief.focus.detail || t(language, "brain.brief.focus.empty.detail");
  const actions = brief.nextActions.slice(0, 3);

  return (
    <section className="brain-brief-panel" aria-label={t(language, "brain.brief.aria")}>
      <div className="brain-brief-copy">
        <span>{t(language, "brain.brief.kicker")}</span>
        <strong>{t(language, brief.headlineKey)}</strong>
        <p>{t(language, brief.bodyKey, { focus: focusTitle })}</p>
      </div>
      <div className="brain-brief-focus">
        <Sparkles className="h-4 w-4" />
        <div>
          <span>{t(language, "brain.brief.focus")}</span>
          <strong>{focusTitle}</strong>
          <small>{focusDetail}</small>
        </div>
      </div>
      <div className="brain-brief-evidence" aria-label={t(language, "brain.brief.evidence.aria")}>
        {brief.evidence.slice(0, 3).map((item) => (
          <span key={item.id} title={t(language, item.detailKey)}>
            <strong>{item.value}</strong>
            {t(language, item.labelKey)}
          </span>
        ))}
      </div>
      {actions.length ? (
        <div className="brain-brief-actions" aria-label={t(language, "brain.brief.actions")}>
          {actions.map((action) => (
            <button key={action.id} type="button" onClick={() => onAction(action)}>
              {briefActionIcon(action.id)}
              <span>{t(language, action.labelKey)}</span>
              <small>{t(language, action.detailKey)}</small>
            </button>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function briefActionIcon(id: string) {
  if (id === "add_source") return <FileUp className="h-4 w-4" />;
  if (id === "inspect_topics") return <Search className="h-4 w-4" />;
  if (id === "verify_model") return <Repeat2 className="h-4 w-4" />;
  if (id === "backup_brain") return <HardDrive className="h-4 w-4" />;
  return <ArrowRight className="h-4 w-4" />;
}

function BrainFirstScreen({
  language,
  brainState,
  intensity,
  readiness,
  memories,
  concepts,
  messages,
  onExploreBrain,
  onOpenDepth,
}: {
  language: Language;
  brainState: BrainState;
  intensity: number;
  readiness: BrainReadiness;
  memories: MemoryFragment[];
  concepts: KnowledgeConcept[];
  messages: Message[];
  onExploreBrain: () => void;
  onOpenDepth: (depth: BrainDepth) => void;
}) {
  const score = Math.max(0, Math.min(100, readiness.score));
  const latestMemory = memories[0]?.title?.trim();
  const strongestConcept = concepts.reduce<KnowledgeConcept | null>(
    (best, concept) => (!best || concept.importance > best.importance ? concept : best),
    null,
  );
  const nextKey =
    score < 30
      ? "brain.firstScreen.next.source"
      : messages.length === 0
        ? "brain.firstScreen.next.ask"
        : "brain.firstScreen.next.graph";

  return (
    <section className="brain-first-screen" aria-label={t(language, "brain.firstScreen.aria")}>
      <div className="brain-first-visual">
        <LivingBrain
          state={brainState}
          intensity={intensity}
          size="large"
          depth={readiness.depth || Math.max(1, Math.floor(score / 20))}
          showLabel={false}
          className="brain-chat-presence"
          onInteract={onExploreBrain}
        />
        <div className="brain-first-pulse" aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
      </div>

      <div className="brain-first-copy">
        <div className="brain-first-status" role="status">
          <span>{t(language, `brain.firstScreen.state.${readiness.state}`)}</span>
          <div className="brain-first-meter" aria-label={t(language, "brain.firstScreen.readiness", { score })}>
            <i style={{ width: `${Math.max(6, score)}%` }} />
          </div>
        </div>

        <h3>{t(language, "brain.firstScreen.title")}</h3>
        <p>{t(language, "brain.firstScreen.body")}</p>

        <div className="brain-first-memory" aria-label={t(language, "brain.firstScreen.memoryAria")}>
          <div>
            <DatabaseZap className="h-4 w-4" />
            <span>{t(language, "brain.firstScreen.memory")}</span>
            <strong>
              {latestMemory
                ? t(language, "brain.firstScreen.memory.with", { title: latestMemory })
                : t(language, "brain.firstScreen.memory.empty")}
            </strong>
          </div>
          <div>
            <BrainCircuit className="h-4 w-4" />
            <span>{t(language, "brain.firstScreen.connection")}</span>
            <strong>
              {strongestConcept
                ? t(language, "brain.firstScreen.concept.with", { title: strongestConcept.label })
                : t(language, "brain.firstScreen.concept.empty")}
            </strong>
          </div>
        </div>

        <div className="brain-first-next">
          <Sparkles className="h-4 w-4" />
          <span>{t(language, "brain.firstScreen.next")}</span>
          <strong>{t(language, nextKey)}</strong>
        </div>

        <div className="brain-first-actions" aria-label={t(language, "brain.firstScreen.actions")}>
          <button type="button" className="is-primary" onClick={() => focusComposer()}>
            <span>{t(language, "brain.firstScreen.action.talk")}</span>
            <ArrowRight className="h-4 w-4" />
          </button>
          <button type="button" onClick={() => navigateHash("/capture")}>
            <FileUp className="h-4 w-4" />
            <span>{t(language, "brain.firstScreen.action.source")}</span>
          </button>
          <button type="button" onClick={() => onOpenDepth(5)}>
            <Search className="h-4 w-4" />
            <span>{t(language, "brain.firstScreen.action.graph")}</span>
          </button>
        </div>
      </div>
    </section>
  );
}

function ProductCommandCenter({
  language,
  readiness,
  proof,
  modelName,
  memories,
  concepts,
  emergenceEvents,
  onOpenDepth,
  onVerifyModelContinuity,
}: {
  language: Language;
  readiness: BrainReadiness;
  proof: BrainProof;
  modelName: string;
  memories: MemoryFragment[];
  concepts: KnowledgeConcept[];
  emergenceEvents: EmergenceEvent[];
  onOpenDepth: (depth: BrainDepth) => void;
  onVerifyModelContinuity: () => void;
}) {
  const score = Math.max(0, Math.min(100, readiness.score));
  const nextKey =
    readiness.state === "alive"
      ? "brain.command.next.alive"
      : readiness.state === "forming"
        ? "brain.command.next.forming"
        : "brain.command.next.empty";
  const recallable = (proof.recall && proof.recall.items) ? proof.recall.items.length : (proof.proofs ? (proof.proofs.durableItems || 1) : 1);
  const latestSource = emergenceEvents[0]?.label ?? t(language, "brain.command.source.empty");

  return (
    <section className="brain-command-center" aria-label={t(language, "brain.command.aria")}>
      <div className="brain-command-head">
        <div>
          <span>{t(language, "brain.command.kicker")}</span>
          <strong>{t(language, "brain.command.title")}</strong>
        </div>
        <div className="brain-command-score" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={score}>
          <span>{t(language, "brain.command.score")}</span>
          <strong>{score}%</strong>
        </div>
      </div>

      <div className="brain-command-next">
        <BrainCircuit className="h-4 w-4" />
        <span>{t(language, "brain.command.next")}</span>
        <strong>{t(language, nextKey)}</strong>
      </div>

      <div className="brain-command-metrics" aria-label={t(language, "brain.command.metrics")}>
        <span>{t(language, "brain.command.metric.memories", { count: memories.length })}</span>
        <span>{t(language, "brain.command.metric.topics", { count: concepts.length })}</span>
        <span>{t(language, "brain.command.metric.sources", { count: readiness.signals.healthySources })}</span>
        <span>{t(language, "brain.command.metric.proof", { count: recallable })}</span>
      </div>

      <div className="brain-command-actions">
        <button type="button" aria-label={t(language, "brain.command.action.add")} onClick={() => navigateHash("/capture")}>
          <FileUp className="h-4 w-4" />
          <span>{t(language, "brain.command.action.add")}</span>
          <small>{latestSource}</small>
        </button>
        <button type="button" aria-label={t(language, "brain.command.action.find")} onClick={() => onOpenDepth(3)}>
          <Search className="h-4 w-4" />
          <span>{t(language, "brain.command.action.find")}</span>
          <small>{t(language, "brain.command.action.find.detail")}</small>
        </button>
        <button type="button" aria-label={t(language, "brain.command.action.proof")} onClick={onVerifyModelContinuity}>
          <Repeat2 className="h-4 w-4" />
          <span>{t(language, "brain.command.action.proof")}</span>
          <small>{proof.modelContinuity.proven ? proof.modelContinuity.activeModel || modelName : modelName}</small>
        </button>
        <button type="button" aria-label={t(language, "brain.command.action.own")} onClick={() => navigateHash("/settings")}>
          <HardDrive className="h-4 w-4" />
          <span>{t(language, "brain.command.action.own")}</span>
          <small>{t(language, "brain.command.action.own.detail")}</small>
        </button>
      </div>

      <div className="brain-command-signals">
        <span><CheckCircle2 className="h-3.5 w-3.5" />{t(language, "brain.command.signal.local")}</span>
        <span><CheckCircle2 className="h-3.5 w-3.5" />{t(language, "brain.command.signal.private")}</span>
        <span><CheckCircle2 className="h-3.5 w-3.5" />{t(language, "brain.command.signal.portable")}</span>
      </div>
    </section>
  );
}

function BrainIngestionPanel({
  language,
  uploadingDocument,
  ingestionStates,
  onUploadDocument,
  onConnectFolder,
  onIngestNote,
  onIngestWeb,
}: {
  language: Language;
  uploadingDocument: boolean;
  ingestionStates: Record<IngestionSourceType, IngestionState | null>;
  onUploadDocument: (file: File) => void;
  onConnectFolder: (path: string) => void;
  onIngestNote: (note: string) => void;
  onIngestWeb: (url: string) => void;
}) {
  const [folderPath, setFolderPath] = React.useState("");
  const [note, setNote] = React.useState("");
  const [url, setUrl] = React.useState("");

  return (
    <section className="brain-ingestion-panel" aria-label={t(language, "brain.ingest.aria")}>
      <div className="brain-ingestion-head">
        <span>{t(language, "brain.ingest.kicker")}</span>
        <strong>{t(language, "brain.ingest.title")}</strong>
      </div>
      <div className="brain-ingestion-grid">
        <label
          className={`brain-ingest-tile is-primary ${tileStateClass(ingestionStates.file)} ${uploadingDocument ? "is-disabled" : ""}`}
        >
          <FileUp className="h-4 w-4" />
          <span>{uploadingDocument ? t(language, "brain.upload.uploading") : t(language, "brain.ingest.file")}</span>
          <small>{t(language, "brain.ingest.file.detail")}</small>
          <input
            type="file"
            accept=".pdf,.docx,.xlsx,.pptx,.txt,.md,.csv,application/pdf,text/plain,text/markdown,text/csv"
            className="sr-only"
            disabled={uploadingDocument}
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.currentTarget.value = "";
              if (file) onUploadDocument(file);
            }}
          />
          <IngestionStageTrack language={language} state={ingestionStates.file} ctaKey="brain.ingest.cta.file" />
        </label>
        <form
          className={`brain-ingest-tile ${tileStateClass(ingestionStates.folder)}`}
          onSubmit={(event) => {
            event.preventDefault();
            onConnectFolder(folderPath);
            setFolderPath("");
          }}
        >
          <FolderPlus className="h-4 w-4" />
          <span>{t(language, "brain.ingest.folder")}</span>
          <input value={folderPath} onChange={(event) => setFolderPath(event.target.value)} placeholder={t(language, "brain.ingest.folder.placeholder")} />
          <IngestionStageTrack language={language} state={ingestionStates.folder} ctaKey="brain.ingest.cta.folder" />
        </form>
        <form
          className={`brain-ingest-tile ${tileStateClass(ingestionStates.note)}`}
          onSubmit={(event) => {
            event.preventDefault();
            onIngestNote(note);
            setNote("");
          }}
        >
          <FileText className="h-4 w-4" />
          <span>{t(language, "brain.ingest.note")}</span>
          <input value={note} onChange={(event) => setNote(event.target.value)} placeholder={t(language, "brain.ingest.note.placeholder")} />
          <IngestionStageTrack language={language} state={ingestionStates.note} ctaKey="brain.ingest.cta.note" />
        </form>
        <form
          className={`brain-ingest-tile ${tileStateClass(ingestionStates.web)}`}
          onSubmit={(event) => {
            event.preventDefault();
            onIngestWeb(url);
            setUrl("");
          }}
        >
          <Globe2 className="h-4 w-4" />
          <span>{t(language, "brain.ingest.web")}</span>
          <input value={url} onChange={(event) => setUrl(event.target.value)} placeholder={t(language, "brain.ingest.web.placeholder")} />
          <IngestionStageTrack language={language} state={ingestionStates.web} ctaKey="brain.ingest.cta.web" />
        </form>
      </div>
    </section>
  );
}

function tileStateClass(state: IngestionState | null): string {
  if (!state) return "";
  if (state.stage === "error") return "is-failed";
  if (state.stage === "complete") return "is-emerged";
  return "is-ingesting";
}

const STAGE_HINT_KEY: Record<IngestionPipelineStage, string> = {
  preparing: "brain.ingest.stage.preparing.hint",
  parsing: "brain.ingest.stage.parsing.hint",
  embedding: "brain.ingest.stage.embedding.hint",
  indexing: "brain.ingest.stage.indexing.hint",
  complete: "brain.ingest.stage.complete.hint",
  error: "brain.ingest.stage.error",
};

// Progressive disclosure of the pipeline: collect -> parse -> embed -> memorize.
function IngestionStageTrack({
  language,
  state,
  ctaKey,
}: {
  language: Language;
  state: IngestionState | null;
  ctaKey: string;
}) {
  if (!state) {
    return <small className="brain-ingest-cta">{t(language, ctaKey)}</small>;
  }

  const activeIndex = INGESTION_STAGE_ORDER.indexOf(state.stage);
  const isError = state.stage === "error";
  const isComplete = state.stage === "complete";
  const hasEmergence = state.newMemories > 0 || state.newEntities > 0;

  return (
    <div
      className="brain-ingest-progress"
      role="status"
      aria-live="polite"
      aria-label={t(language, "brain.ingest.progress.aria", { label: state.label })}
    >
      <div className="brain-ingest-stage-badges" aria-hidden="true">
        {INGESTION_STAGE_ORDER.filter((stage) => stage !== "complete").map((stage) => {
          const index = INGESTION_STAGE_ORDER.indexOf(stage);
          const done = !isError && !isComplete && index < activeIndex;
          const active = !isError && !isComplete && index === activeIndex;
          const passed = isComplete;
          const cls = isError
            ? "is-failed"
            : passed || done
              ? "is-done"
              : active
                ? "is-active"
                : "";
          return (
            <span key={stage} className={`brain-ingest-stage-badge ${cls}`}>
              {(active || (isError && index === activeIndex)) ? <Loader2 className="h-3 w-3 brain-ingest-spin" /> : null}
              {t(language, `brain.ingest.stage.${stage}`)}
            </span>
          );
        })}
      </div>
      <small className={`brain-ingest-stage-hint ${isError ? "is-failed" : isComplete ? "is-emerged" : ""}`}>
        {isError
          ? t(language, "brain.ingest.stage.error")
          : isComplete
            ? hasEmergence
              ? t(language, "brain.ingest.result", { memories: state.newMemories, entities: state.newEntities })
              : t(language, "brain.ingest.result.empty")
            : t(language, STAGE_HINT_KEY[state.stage])}
      </small>
    </div>
  );
}

function relativeTime(language: Language, at: number, now: number): string {
  const diffMinutes = Math.floor((now - at) / 60000);
  if (diffMinutes < 1) return t(language, "brain.timeline.justNow");
  return t(language, "brain.timeline.minutesAgo", { count: diffMinutes });
}

function IngestionTimelineSection({
  language,
  emergenceEvents,
}: {
  language: Language;
  emergenceEvents: EmergenceEvent[];
}) {
  // Re-read the clock on each render of the parent so relative labels stay fresh-enough.
  const now = Date.now();
  return (
    <section
      className="brain-emergence-timeline"
      aria-label={t(language, "brain.timeline.aria")}
      aria-live="polite"
    >
      <div className="brain-emergence-head">
        <Sparkles className="h-3.5 w-3.5" />
        <strong>{t(language, "brain.timeline.emergenceTitle")}</strong>
        <span>{t(language, "brain.timeline.recent")}</span>
      </div>
      {emergenceEvents.length === 0 ? (
        <p className="brain-emergence-empty">{t(language, "brain.timeline.empty")}</p>
      ) : (
        <ol className="brain-emergence-list">
          {emergenceEvents.map((event) => (
            <li key={event.id} className="brain-emergence-item">
              <span className="brain-emergence-type">{t(language, INGESTION_TYPE_LABEL_KEY[event.sourceType])}</span>
              <span className="brain-emergence-label">{event.label}</span>
              <span className="brain-emergence-counts">
                <strong>{t(language, "brain.timeline.newMemories", { count: event.newMemories })}</strong>
                <strong>{t(language, "brain.timeline.newEntities", { count: event.newEntities })}</strong>
              </span>
              <time className="brain-emergence-at">{relativeTime(language, event.at, now)}</time>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function ModelContinuityDemo({
  language,
  proof,
  modelName,
  onVerify,
}: {
  language: Language;
  proof: BrainProof;
  modelName: string;
  onVerify: () => void;
}) {
  return (
    <section className="brain-model-demo" aria-label={t(language, "brain.modelDemo.aria")}>
      <div>
        <span>{t(language, "brain.modelDemo.kicker")}</span>
        <strong>{proof.modelContinuity.proven ? t(language, "brain.modelDemo.proven") : t(language, "brain.modelDemo.pending")}</strong>
        <small>{t(language, "brain.modelDemo.detail", { model: proof.modelContinuity.activeModel || modelName })}</small>
      </div>
      <button type="button" onClick={onVerify}>
        <Repeat2 className="h-3.5 w-3.5" />
        {t(language, "brain.modelDemo.verify")}
      </button>
      <button type="button" onClick={() => navigateHash("/models")}>
        <Cpu className="h-3.5 w-3.5" />
        {t(language, "brain.modelDemo.change")}
      </button>
    </section>
  );
}

function citationDomId(messageId: string, citationId: string): string {
  return `${messageId}-cite-${citationId}`;
}

// Inline, keyboard-focusable citation markers rendered next to the answer text.
// Activating one moves focus to the matching row in the evidence card below.
function InlineCitationMarkers({
  language,
  proof,
  messageId,
}: {
  language: Language;
  proof: NonNullable<Message["proof"]>;
  messageId: string;
}) {
  const focusCitation = (citationId: string) => {
    const target = document.getElementById(citationDomId(messageId, citationId));
    if (target) {
      target.scrollIntoView({ behavior: "smooth", block: "nearest" });
      target.focus();
    }
  };
  return (
    <span className="brain-inline-citations" aria-label={t(language, "brain.answerProof.citationsLabel", { count: proof.citations.length })}>
      {proof.citations.map((citation, index) => (
        <button
          key={citation.id}
          type="button"
          className="brain-inline-citation"
          aria-label={t(language, "brain.answerProof.marker", { index: index + 1 })}
          aria-controls={citationDomId(messageId, citation.id)}
          title={citation.title}
          onClick={() => focusCitation(citation.id)}
        >
          {index + 1}
        </button>
      ))}
    </span>
  );
}

function AnswerProofCard({ language, proof, messageId }: { language: Language; proof: NonNullable<Message["proof"]>; messageId: string }) {
  return (
    <section className="brain-answer-proof" role="group" aria-label={t(language, "brain.answerProof.aria")}>
      <div className="brain-answer-proof-head">
        <span>{t(language, "brain.answerProof.title")}</span>
        <strong>{proof.provenAcrossModels ? t(language, "brain.answerProof.modelProven", { model: proof.model }) : t(language, "brain.answerProof.modelPending", { model: proof.model })}</strong>
        {proof.citations.length ? (
          <small className="brain-answer-proof-count">{t(language, "brain.answerProof.citationsLabel", { count: proof.citations.length })}</small>
        ) : null}
      </div>
      {proof.citations.length ? (
        <ol>
          {proof.citations.map((citation, index) => (
            <li
              key={citation.id}
              id={citationDomId(messageId, citation.id)}
              tabIndex={-1}
              aria-label={t(language, "brain.answerProof.citationItem", { index: index + 1, title: citation.title })}
            >
              <span className="brain-answer-proof-index" aria-hidden="true">{index + 1}</span>
              <span>{citation.source}</span>
              <strong>{citation.title}</strong>
              <small>{citation.snippet || proof.query}</small>
            </li>
          ))}
        </ol>
      ) : (
        <small>{t(language, "brain.answerProof.empty")}</small>
      )}
    </section>
  );
}

function BrainEmptyState({
  language,
  starterPrompts,
  onDraftChange,
}: {
  language: Language;
  starterPrompts: string[];
  onDraftChange: (value: string) => void;
}) {
  return (
    <div className="mind-empty">
      <div className="mind-empty-kicker">{t(language, "brain.empty.kicker")}</div>
      <div className="mind-empty-title">{t(language, "brain.empty.title")}</div>
      <p>{t(language, "brain.empty.body")}</p>
      <div className="mind-empty-prompts" aria-label={t(language, "brain.aria.starterPrompts")}>
        {starterPrompts.map((prompt) => (
          <button key={prompt} type="button" onClick={() => onDraftChange(prompt)}>
            {prompt}
          </button>
        ))}
      </div>
    </div>
  );
}

function navigateHash(route: string) {
  window.location.hash = route;
}

function focusComposer() {
  document.querySelector<HTMLTextAreaElement>(".brain-composer textarea")?.focus();
}
