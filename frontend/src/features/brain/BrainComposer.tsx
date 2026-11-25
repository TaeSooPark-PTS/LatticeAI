import * as React from "react";
import { FileUp, ImagePlus, Plus, Send, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { t, type Language } from "@/i18n";

export function BrainComposer({
  language,
  draft,
  streaming,
  imageData,
  uploadingDocument,
  onDraftChange,
  onImageDataChange,
  onUploadDocument,
  onSend,
  onStop,
  attachments,
}: {
  language: Language;
  draft: string;
  streaming: boolean;
  imageData: string | null;
  uploadingDocument: boolean;
  onDraftChange: (value: string) => void;
  onImageDataChange: (value: string | null) => void;
  onUploadDocument: (file: File) => void;
  onSend: () => void;
  onStop?: () => void;
  attachments?: React.ReactNode;
}) {
  const textareaRef = React.useRef<HTMLTextAreaElement>(null);
  const attachRef = React.useRef<HTMLDivElement>(null);
  const [attachOpen, setAttachOpen] = React.useState(false);
  const attachMenuId = React.useId();
  const closeTimer = React.useRef<number | null>(null);
  const openedAt = React.useRef(0);

  React.useEffect(() => {
    const element = textareaRef.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, 152)}px`;
  }, [draft]);

  const cancelScheduledClose = () => {
    if (closeTimer.current !== null) {
      window.clearTimeout(closeTimer.current);
      closeTimer.current = null;
    }
  };

  const scheduleClose = () => {
    cancelScheduledClose();
    closeTimer.current = window.setTimeout(() => {
      const root = attachRef.current;
      if (!root) return;
      if (root.contains(document.activeElement)) return;
      if (root.querySelector(".brain-ingestion-dock-popover")) return;
      if (uploadingDocument) return;
      setAttachOpen(false);
    }, 240);
  };

  React.useEffect(() => cancelScheduledClose, []);

  React.useEffect(() => {
    if (!attachOpen) return;
    const dismiss = (event: Event) => {
      const target = event.target as Node | null;
      if (target && attachRef.current?.contains(target)) return;
      setAttachOpen(false);
    };
    document.addEventListener("pointerdown", dismiss, true);
    return () => document.removeEventListener("pointerdown", dismiss, true);
  }, [attachOpen]);

  return (
    <div className="brain-composer" aria-busy={streaming}>
      <textarea
        ref={textareaRef}
        rows={1}
        value={draft}
        onChange={(event) => onDraftChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.nativeEvent.isComposing) return;
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            onSend();
          }
        }}
        aria-label={t(language, "brain.placeholder")}
        aria-describedby="brain-composer-hint"
        aria-keyshortcuts="Control+K Meta+K"
        placeholder={t(language, "brain.placeholder")}
      />
      <nav className="brain-composer-can" aria-label={t(language, "brain.composer.can.aria")}>
        {(
          [
            ["brain.composer.can.search", "brain.composer.can.search.prompt"],
            ["brain.composer.can.read", "brain.composer.can.read.prompt"],
            ["brain.composer.can.memory", "brain.composer.can.memory.prompt"],
            ["brain.composer.can.skill", "brain.composer.can.skill.prompt"],
          ] as const
        ).map(([labelKey, promptKey]) => (
          <button
            key={labelKey}
            type="button"
            className="brain-composer-can-chip"
            disabled={streaming}
            onClick={() => onDraftChange(t(language, promptKey))}
          >
            {t(language, labelKey)}
          </button>
        ))}
      </nav>
      <div className="brain-composer-actions">
        <div
          ref={attachRef}
          className={`brain-attach ${attachOpen ? "is-open" : ""}`}
          onPointerEnter={(event) => {
            if (event.pointerType !== "mouse") return;
            cancelScheduledClose();
            setAttachOpen((current) => {
              if (!current) openedAt.current = Date.now();
              return true;
            });
          }}
          onPointerLeave={(event) => {
            if (event.pointerType !== "mouse") return;
            scheduleClose();
          }}
          onKeyDown={(event) => {
            if (event.key !== "Escape" || !attachOpen) return;
            if (attachRef.current?.querySelector(".brain-ingestion-dock-popover")) return;
            event.preventDefault();
            event.stopPropagation();
            setAttachOpen(false);
          }}
        >
          <button
            type="button"
            className="brain-attach-toggle"
            aria-expanded={attachOpen}
            aria-controls={attachOpen ? attachMenuId : undefined}
            aria-label={t(language, "brain.composer.attach.aria")}
            data-testid="brain-attach-toggle"
            onClick={() => {
              setAttachOpen((current) => {
                if (!current) {
                  openedAt.current = Date.now();
                  return true;
                }
                return Date.now() - openedAt.current < 350;
              });
            }}
          >
            <Plus className="h-4 w-4" aria-hidden="true" />
            <span>{t(language, "brain.composer.attach")}</span>
          </button>
          {attachOpen ? (
            <div
              id={attachMenuId}
              className="brain-composer-attachments"
              data-testid="brain-attach-menu"
              role="group"
              aria-label={t(language, "brain.composer.attachments")}
            >
              <label className={`brain-document-input ${uploadingDocument ? "is-disabled" : ""}`}>
                <FileUp className="h-4 w-4" aria-hidden="true" />
                <span>{uploadingDocument ? t(language, "brain.upload.uploading") : t(language, "brain.upload.ctaShort")}</span>
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
              <label className="brain-image-input">
                <ImagePlus className="h-4 w-4" aria-hidden="true" />
                <span>{t(language, "brain.image")}</span>
                <input
                  type="file"
                  accept="image/*"
                  className="sr-only"
                  onChange={async (event) => {
                    const file = event.target.files?.[0];
                    event.currentTarget.value = "";
                    if (file) onImageDataChange(await fileToDataUrl(file));
                  }}
                />
              </label>
              {attachments}
            </div>
          ) : null}
          {imageData ? <span className="brain-quiet-success">{t(language, "brain.imageAttached")}</span> : null}
        </div>
        <span id="brain-composer-hint" className="brain-composer-hint">{t(language, "brain.composer.hint")}</span>
        {streaming && onStop ? (
          <Button onClick={onStop} variant="outline" className="brain-composer-submit">
            <Square className="h-4 w-4" aria-hidden="true" /> {t(language, "brain.stop")}
          </Button>
        ) : (
          <Button onClick={onSend} disabled={!draft.trim() || streaming} className="brain-composer-submit">
            <Send className="h-4 w-4" aria-hidden="true" /> {t(language, "brain.send")}
          </Button>
        )}
      </div>
    </div>
  );
}

export function fileToDataUrl(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}
