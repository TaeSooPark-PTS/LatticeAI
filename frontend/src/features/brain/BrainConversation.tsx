import * as React from "react";
import { Cpu, DatabaseZap, FileText, FileUp, FolderPlus, Globe2, Repeat2, Search, Settings, ShieldCheck } from "lucide-react";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { t, type Language } from "@/i18n";
import type { BrainDepth, BrainProof, BrainReadiness, KnowledgeConcept, MemoryFragment, Message } from "./types";
import { BrainCarePanel } from "./BrainCarePanel";
import { BrainComposer } from "./BrainComposer";
import { BrainOverviewPanel } from "./BrainOverviewPanel";

export function BrainConversation({
  language,
  explorationDepth,
  modelName,
  messages,
  starterPrompts,
  memoryFeedback,
  draft,
  streaming,
  imageData,
  streamRef,
  memories,
  concepts,
  readiness,
  proof,
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
}: {
  language: Language;
  explorationDepth: BrainDepth;
  modelName: string;
  messages: Message[];
  starterPrompts: string[];
  memoryFeedback: string | null;
  draft: string;
  streaming: boolean;
  imageData: string | null;
  streamRef: React.RefObject<HTMLDivElement | null>;
  memories: MemoryFragment[];
  concepts: KnowledgeConcept[];
  readiness: BrainReadiness;
  proof: BrainProof;
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
          <button type="button" onClick={() => navigateHash("/models")}>
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
          onUploadDocument={onUploadDocument}
          onConnectFolder={onConnectFolder}
          onIngestNote={onIngestNote}
          onIngestWeb={onIngestWeb}
        />
        <BrainOverviewPanel
          memories={memories}
          concepts={concepts}
          readiness={readiness}
          proof={proof}
          onOpenDepth={onOpenDepth}
        />
        <ModelContinuityDemo
          language={language}
          proof={proof}
          modelName={modelName}
          onVerify={onVerifyModelContinuity}
        />
        {messages.length === 0 ? (
          <BrainEmptyState
            language={language}
            starterPrompts={starterPrompts}
            uploadingDocument={uploadingDocument}
            onDraftChange={onDraftChange}
            onUploadDocument={onUploadDocument}
          />
        ) : (
          messages.map((message, index) => (
            <div key={`${message.role}-${index}`} className={`brain-message ${message.role}`}>
              <div className="brain-message-bubble">{message.content}</div>
              {message.role === "assistant" && message.proof ? (
                <AnswerProofCard language={language} proof={message.proof} />
              ) : null}
            </div>
          ))
        )}
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
  onUploadDocument,
  onConnectFolder,
  onIngestNote,
  onIngestWeb,
}: {
  language: Language;
  uploadingDocument: boolean;
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
        <label className={`brain-ingest-tile is-primary ${uploadingDocument ? "is-disabled" : ""}`}>
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
        </label>
        <form
          className="brain-ingest-tile"
          onSubmit={(event) => {
            event.preventDefault();
            onConnectFolder(folderPath);
            setFolderPath("");
          }}
        >
          <FolderPlus className="h-4 w-4" />
          <span>{t(language, "brain.ingest.folder")}</span>
          <input value={folderPath} onChange={(event) => setFolderPath(event.target.value)} placeholder={t(language, "brain.ingest.folder.placeholder")} />
        </form>
        <form
          className="brain-ingest-tile"
          onSubmit={(event) => {
            event.preventDefault();
            onIngestNote(note);
            setNote("");
          }}
        >
          <FileText className="h-4 w-4" />
          <span>{t(language, "brain.ingest.note")}</span>
          <input value={note} onChange={(event) => setNote(event.target.value)} placeholder={t(language, "brain.ingest.note.placeholder")} />
        </form>
        <form
          className="brain-ingest-tile"
          onSubmit={(event) => {
            event.preventDefault();
            onIngestWeb(url);
            setUrl("");
          }}
        >
          <Globe2 className="h-4 w-4" />
          <span>{t(language, "brain.ingest.web")}</span>
          <input value={url} onChange={(event) => setUrl(event.target.value)} placeholder={t(language, "brain.ingest.web.placeholder")} />
        </form>
      </div>
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

function AnswerProofCard({ language, proof }: { language: Language; proof: NonNullable<Message["proof"]> }) {
  return (
    <div className="brain-answer-proof" aria-label={t(language, "brain.answerProof.aria")}>
      <div className="brain-answer-proof-head">
        <span>{t(language, "brain.answerProof.title")}</span>
        <strong>{proof.provenAcrossModels ? t(language, "brain.answerProof.modelProven", { model: proof.model }) : t(language, "brain.answerProof.modelPending", { model: proof.model })}</strong>
      </div>
      {proof.citations.length ? (
        <ol>
          {proof.citations.map((citation) => (
            <li key={citation.id}>
              <span>{citation.source}</span>
              <strong>{citation.title}</strong>
              <small>{citation.snippet || proof.query}</small>
            </li>
          ))}
        </ol>
      ) : (
        <small>{t(language, "brain.answerProof.empty")}</small>
      )}
    </div>
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
