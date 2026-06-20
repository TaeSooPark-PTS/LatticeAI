import * as React from "react";
import { CheckCircle2, Cpu, DatabaseZap, Download, FileText, FileUp, FolderPlus, Globe2, Loader2, Search, Settings, ShieldCheck } from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { t, type Language } from "@/i18n";
import { latticeApi } from "@/api/client";
import { asArray } from "@/lib/utils";
import {
  INGESTION_STAGE_ORDER,
  type BrainDepth,
  type BrainReadiness,
  type IngestionPipelineStage,
  type IngestionSourceType,
  type IngestionState,
  type KnowledgeConcept,
  type KnowledgeGraphModel,
  type Message,
} from "./types";
import { BrainCarePanel } from "./BrainCarePanel";
import { BrainComposer } from "./BrainComposer";
import { BrainGraphLayer } from "./BrainGraphLayer";

export function BrainConversation({
  language,
  explorationDepth,
  modelName,
  messages,
  starterPrompts,
  memoryFeedback,
  ingestionStates,
  draft,
  streaming,
  imageData,
  streamRef,
  concepts,
  graphModel,
  graphSearch,
  selectedGraphId,
  readiness,
  uploadingDocument,
  onGraphSearch,
  onSelectGraphNode,
  onDraftChange,
  onImageDataChange,
  onUploadDocument,
  onConnectFolder,
  onIngestNote,
  onIngestWeb,
  onVerifyModelContinuity,
  onSend,
}: {
  language: Language;
  explorationDepth: BrainDepth;
  modelName: string;
  messages: Message[];
  starterPrompts: string[];
  memoryFeedback: string | null;
  ingestionStates: Record<IngestionSourceType, IngestionState | null>;
  draft: string;
  streaming: boolean;
  imageData: string | null;
  streamRef: React.RefObject<HTMLDivElement | null>;
  concepts: KnowledgeConcept[];
  graphModel: KnowledgeGraphModel;
  graphSearch: string;
  selectedGraphId: string | null;
  readiness: BrainReadiness;
  uploadingDocument: boolean;
  onGraphSearch: (value: string) => void;
  onSelectGraphNode: (id: string | null) => void;
  onDraftChange: (value: string) => void;
  onImageDataChange: (value: string | null) => void;
  onUploadDocument: (file: File) => void;
  onConnectFolder: (path: string) => void;
  onIngestNote: (note: string) => void;
  onIngestWeb: (url: string) => void;
  onVerifyModelContinuity: () => void;
  onSend: () => void;
}) {
  return (
    <section className="brain-conversation" aria-label={t(language, "brain.aria.conversation")}>
      <div className="brain-conversation-header">
        <div>
          <h1>{t(language, "brain.title")}</h1>
          <span>{t(language, `brain.depth.${explorationDepth}`)}</span>
        </div>
        <LanguageSwitcher compact />
        <div className="brain-ownership-strip" aria-label={t(language, "brain.aria.ownership")}>
          <span>{t(language, "brain.local")}</span>
          <span>{t(language, "brain.portable")}</span>
          <span>{t(language, "brain.private")}</span>
        </div>
        <div className="brain-flow-actions" aria-label={t(language, "brain.aria.actions")}>
          <button type="button" onClick={() => navigateHash("/capture")}>
            <FileUp className="h-3.5 w-3.5" />
            {t(language, "brain.action.add")}
          </button>
          <button type="button" onClick={() => navigateHash("/knowledge-graph")}>
            <Search className="h-3.5 w-3.5" />
            {t(language, "brain.action.find")}
          </button>
          <button type="button" onClick={() => document.getElementById("brain-model-setup")?.scrollIntoView({ behavior: "smooth", block: "center" })}>
            <Cpu className="h-3.5 w-3.5" />
            {t(language, "brain.action.model")}
          </button>
          <button type="button" onClick={() => navigateHash("/settings")}>
            <Settings className="h-3.5 w-3.5" />
            {t(language, "brain.action.settings")}
          </button>
        </div>
        <div className="brain-model-pill">{modelName}</div>
        <button className="brain-admin-link" type="button" onClick={() => navigateHash("/admin")}>
          <ShieldCheck className="h-3.5 w-3.5" />
          {t(language, "brain.admin")}
        </button>
      </div>

      <div ref={streamRef} className="brain-stream">
        <BrainIngestionPanel
          language={language}
          uploadingDocument={uploadingDocument}
          ingestionStates={ingestionStates}
          onUploadDocument={onUploadDocument}
          onConnectFolder={onConnectFolder}
          onIngestNote={onIngestNote}
          onIngestWeb={onIngestWeb}
        />
        <section className="brain-deep-graph-panel" aria-label="Deep Graph">
          <div className="brain-deep-graph-summary">
            <div>
              <span>Deep Graph</span>
              <strong>{graphModel.nodes.length} nodes · {graphModel.edges.length} links</strong>
            </div>
            <div>
              <span>Brain readiness</span>
              <strong>{readiness.score}% · {concepts.length} concepts</strong>
            </div>
          </div>
          <BrainGraphLayer
            model={graphModel}
            search={graphSearch}
            selectedId={selectedGraphId}
            onSearch={onGraphSearch}
            onSelect={onSelectGraphNode}
          />
        </section>
        <BrainModelSetupPanel
          language={language}
          modelName={modelName}
          onVerify={onVerifyModelContinuity}
        />
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

      {memoryFeedback ? (
        <div className="brain-save-feedback" role="status">
          <DatabaseZap className="h-3.5 w-3.5" />
          <span>{memoryFeedback}</span>
          <small>{t(language, "brain.saved.detail")}</small>
        </div>
      ) : null}

      <BrainCarePanel language={language} />

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

function BrainModelSetupPanel({
  language,
  modelName,
  onVerify,
}: {
  language: Language;
  modelName: string;
  onVerify: () => void;
}) {
  const qc = useQueryClient();
  const models = useQuery({ queryKey: ["models"], queryFn: latticeApi.models });
  const recs = useQuery({ queryKey: ["modelRecommendations", "local_mlx"], queryFn: () => latticeApi.modelRecommendations("local_mlx") });
  const [consent, setConsent] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [progress, setProgress] = React.useState("");
  const data = (models.data?.data || {}) as Record<string, unknown>;
  const recommendationData = (recs.data?.data as Record<string, unknown> | undefined)?.recommendations as Record<string, unknown> | undefined;
  const topPick = (recommendationData?.top_pick || null) as Record<string, unknown> | null;
  const recommended = asArray<Record<string, unknown>>(data.recommended);
  const picked =
    recommended.find((item) => String(item.id || "").includes("gemma-4-26b-a4b-it-4bit"))
    || recommended.find((item) => String(item.id || "") === String(topPick?.id || ""))
    || recommended[0]
    || topPick
    || null;
  const current = String(data.current || modelName || "");
  const loaded = asArray<string>(data.loaded);
  const pickedId = String(picked?.recommended_load_id || picked?.id || topPick?.id || "");
  const engine = String(picked?.recommended_engine || "local_mlx");
  const isLoaded = Boolean(current && (current === pickedId || loaded.includes(pickedId)));
  const downloadRequired = Boolean(picked?.download_required);
  const canRun = Boolean(pickedId) && !busy && (!downloadRequired || consent);

  async function prepare() {
    if (!pickedId || busy) return;
    setBusy(true);
    setProgress("Preparing local model...");
    const result = await latticeApi.streamModelPrepare(
      { model: pickedId, engine, allow_download: consent },
      {
        onProgress: (item) => setProgress(String(item.message || item.stage || "Preparing local model...")),
        onDone: () => setProgress("Model is ready."),
        onError: (item) => setProgress(String(item.user_message || item.reason || "Model setup failed.")),
      },
    );
    if (!result.ok && result.error) setProgress(String(result.error));
    await qc.invalidateQueries({ queryKey: ["models"] });
    await qc.invalidateQueries({ queryKey: ["modelRecommendations", "local_mlx"] });
    setBusy(false);
  }

  return (
    <section id="brain-model-setup" className="brain-model-demo brain-model-setup" aria-label={t(language, "brain.modelDemo.aria")}>
      <div>
        <span>Local model</span>
        <strong>{current || "No model loaded"}</strong>
        <small>{picked ? `Recommended: ${String(picked.name || picked.id)}${downloadRequired ? " · download required" : " · ready locally"}` : "Scanning local model catalog..."}</small>
      </div>
      {downloadRequired ? (
        <label className="brain-model-consent">
          <input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />
          <span>Allow download</span>
        </label>
      ) : null}
      <button type="button" onClick={onVerify}>
        <CheckCircle2 className="h-3.5 w-3.5" />
        Verify
      </button>
      <button type="button" disabled={!canRun || isLoaded} onClick={() => void prepare()}>
        {downloadRequired ? <Download className="h-3.5 w-3.5" /> : <Cpu className="h-3.5 w-3.5" />}
        {busy ? "Preparing" : isLoaded ? "Loaded" : downloadRequired ? "Install & Load" : "Load"}
      </button>
      {progress ? <small className="brain-model-progress">{progress}</small> : null}
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
  uploadingDocument,
  onDraftChange,
  onUploadDocument,
}: {
  language: Language;
  starterPrompts: string[];
  uploadingDocument: boolean;
  onDraftChange: (value: string) => void;
  onUploadDocument: (file: File) => void;
}) {
  return (
    <div className="mind-empty">
      <div className="mind-empty-kicker">{t(language, "brain.empty.kicker")}</div>
      <div className="mind-empty-title">{t(language, "brain.empty.title")}</div>
      <p>{t(language, "brain.empty.body")}</p>
      <label className={`mind-empty-upload ${uploadingDocument ? "is-disabled" : ""}`}>
        <DatabaseZap className="h-3.5 w-3.5" />
        <span>{uploadingDocument ? t(language, "brain.upload.uploading") : t(language, "brain.upload.cta")}</span>
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
      </label>
      <small className="mind-empty-upload-hint">{t(language, "brain.upload.hint")}</small>
      <div className="mind-empty-prompts" aria-label={t(language, "brain.aria.starterPrompts")}>
        {starterPrompts.map((prompt) => (
          <button key={prompt} type="button" onClick={() => onDraftChange(prompt)}>
            {prompt}
          </button>
        ))}
      </div>
      <div className="mind-empty-trail" aria-label={t(language, "brain.empty.trail.label")}>
        <span>{t(language, "brain.empty.trail.save")}</span>
        <span>{t(language, "brain.empty.trail.recall")}</span>
        <span>{t(language, "brain.empty.trail.backup")}</span>
      </div>
    </div>
  );
}

function navigateHash(route: string) {
  window.location.hash = route;
}
