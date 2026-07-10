import * as React from "react";
import { FileText, FileUp, FolderOpen, FolderPlus, Globe2, Loader2, Sparkles } from "lucide-react";

import { t, type Language } from "@/i18n";
import {
  type EmergenceEvent,
  type IngestionPipelineStage,
  type IngestionSourceType,
  type IngestionState,
} from "./types";

const INGESTION_TYPE_LABEL_KEY: Record<IngestionSourceType, string> = {
  chat: "brain.ingest.type.chat",
  file: "brain.ingest.type.file",
  folder: "brain.ingest.type.folder",
  note: "brain.ingest.type.note",
  web: "brain.ingest.type.web",
};

export function BrainIngestionPanel({
  language,
  uploadingDocument,
  ingestionStates,
  onUploadDocument,
  onPickFolder,
  onConnectFolder,
  onIngestNote,
  onIngestWeb,
}: {
  language: Language;
  uploadingDocument: boolean;
  ingestionStates: Record<IngestionSourceType, IngestionState | null>;
  onUploadDocument: (file: File) => void;
  onPickFolder: () => void;
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
          <button type="button" className="brain-ingest-folder-picker" onClick={onPickFolder}>
            <FolderOpen className="h-4 w-4" aria-hidden="true" />
            {t(language, "capture.local.choose")}
          </button>
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
        <span className={`brain-ingest-stage-badge ${isError ? "is-failed" : isComplete ? "is-done" : "is-active"}`}>
          {!isError && !isComplete ? <Loader2 className="h-3 w-3 brain-ingest-spin" /> : null}
          {t(language, `brain.ingest.stage.${state.stage}`)}
        </span>
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

export function IngestionTimelineSection({
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
