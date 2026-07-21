import * as React from "react";
import { Download, ShieldCheck, X } from "lucide-react";

import { latticeApi } from "@/api/client";
import { t, type Language } from "@/i18n";
import { useFocusTrap } from "@/lib/useFocusTrap";
import { MessageBody } from "./MessageMarkdown";
import type { MessageFile } from "./types";

// Mirrors the backend PREVIEWABLE_EXTENSIONS contract (core/file_generation)
// as a client-side fallback for responses that predate the artifacts[] meta.
const PREVIEWABLE_FALLBACK = new Set([
  "html", "htm", "md", "markdown", "txt", "json", "css", "js",
  "csv", "py", "yaml", "yml", "xml", "sql", "sh",
]);

export type PreviewKind = "html" | "markdown" | "json" | "text";

export function fileExtension(filename: string): string {
  const dot = filename.lastIndexOf(".");
  return dot >= 0 ? filename.slice(dot + 1).toLowerCase() : "";
}

export function isPreviewableFile(file: MessageFile): boolean {
  if (typeof file.previewable === "boolean") return file.previewable;
  return PREVIEWABLE_FALLBACK.has(fileExtension(file.filename));
}

export function previewKind(filename: string): PreviewKind {
  const ext = fileExtension(filename);
  if (ext === "html" || ext === "htm") return "html";
  if (ext === "md" || ext === "markdown") return "markdown";
  if (ext === "json") return "json";
  return "text";
}

// Sandboxed HTML preview: `sandbox=""` (no allow-same-origin, no allow-scripts)
// plus an injected restrictive CSP so even non-script vectors (remote images,
// external styles, form posts) cannot leave the preview frame.
const PREVIEW_CSP_META =
  '<meta http-equiv="Content-Security-Policy" ' +
  "content=\"default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:;\">";

export function htmlWithPreviewCsp(content: string): string {
  if (/<head[^>]*>/i.test(content)) {
    return content.replace(/<head[^>]*>/i, (match) => `${match}${PREVIEW_CSP_META}`);
  }
  return `${PREVIEW_CSP_META}${content}`;
}

export function prettyJson(content: string): string {
  try {
    return JSON.stringify(JSON.parse(content), null, 2);
  } catch {
    return content;
  }
}

// Inline preview for chat file cards: HTML in a sandboxed iframe, md/txt/json
// rendered in a modal. Keeps the download action, traps focus, closes on
// Escape and backdrop click.
export function FilePreviewModal({
  language,
  file,
  onClose,
}: {
  language: Language;
  file: MessageFile;
  onClose: () => void;
}) {
  const trapRef = useFocusTrap<HTMLDivElement>(onClose);
  const [state, setState] = React.useState<
    { status: "loading" } | { status: "error"; reason: string } | { status: "ready"; content: string }
  >({ status: "loading" });
  const [downloading, setDownloading] = React.useState(false);
  const kind = previewKind(file.filename);

  React.useEffect(() => {
    let cancelled = false;
    void latticeApi.readWorkspaceFile(file.path).then((result) => {
      if (cancelled) return;
      if (result.ok) setState({ status: "ready", content: result.data.content });
      else setState({ status: "error", reason: result.error || String(result.status || "") });
    });
    return () => {
      cancelled = true;
    };
  }, [file.path]);

  async function download() {
    if (downloading) return;
    setDownloading(true);
    await latticeApi.downloadWorkspaceFile(file.path, file.filename);
    setDownloading(false);
  }

  return (
    <div
      className="file-preview-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={trapRef}
        className="file-preview-modal"
        role="dialog"
        aria-modal="true"
        aria-label={t(language, "brain.preview.aria", { name: file.filename })}
        data-testid="file-preview-modal"
      >
        <header className="file-preview-head">
          <strong className="file-preview-name">{file.filename}</strong>
          <div className="file-preview-actions">
            <button type="button" onClick={() => void download()} disabled={downloading}>
              <Download className="h-3.5 w-3.5" aria-hidden="true" />
              {downloading ? t(language, "brain.files.downloading") : t(language, "brain.files.download")}
            </button>
            <button
              type="button"
              className="file-preview-close"
              aria-label={t(language, "brain.preview.close")}
              onClick={onClose}
            >
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        </header>
        <div className="file-preview-body">
          {state.status === "loading" ? (
            <p className="file-preview-note" role="status">{t(language, "brain.preview.loading")}</p>
          ) : state.status === "error" ? (
            <p className="file-preview-note is-error" role="alert">
              {t(language, "brain.preview.error", { reason: state.reason })}
            </p>
          ) : kind === "html" ? (
            <iframe
              className="file-preview-frame"
              sandbox=""
              title={t(language, "brain.preview.aria", { name: file.filename })}
              srcDoc={htmlWithPreviewCsp(state.content)}
            />
          ) : kind === "markdown" ? (
            <div className="file-preview-markdown" data-testid="file-preview-markdown">
              <MessageBody language={language} content={state.content} />
            </div>
          ) : (
            <pre className="file-preview-code" data-testid="file-preview-code">
              {kind === "json" ? prettyJson(state.content) : state.content}
            </pre>
          )}
        </div>
        {kind === "html" ? (
          <footer className="file-preview-foot" role="note">
            <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
            {t(language, "brain.preview.sandboxNote")}
          </footer>
        ) : null}
      </div>
    </div>
  );
}
