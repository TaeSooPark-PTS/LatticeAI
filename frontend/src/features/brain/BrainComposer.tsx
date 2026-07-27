import * as React from "react";
import { FileUp, ImagePlus, Send, Square } from "lucide-react";
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
  /** Extra capture controls (file · folder · note · web) shown beside 문서/이미지. */
  attachments?: React.ReactNode;
}) {
  const textareaRef = React.useRef<HTMLTextAreaElement>(null);

  // Auto-grow with the draft up to the CSS max-height (9.5rem = 152px),
  // so multi-line questions stay fully visible while typing.
  React.useEffect(() => {
    const element = textareaRef.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, 152)}px`;
  }, [draft]);

  return (
    <div className="brain-composer" aria-busy={streaming}>
      <textarea
        ref={textareaRef}
        rows={1}
        value={draft}
        onChange={(event) => onDraftChange(event.target.value)}
        onKeyDown={(event) => {
          // IME guard: while Korean/Japanese/Chinese text is still composing,
          // Enter commits the composition — sending here would fire early and
          // duplicate the last syllable.
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
      <div className="brain-composer-actions">
        <div className="brain-composer-attachments" aria-label={t(language, "brain.composer.attachments")}>
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

function fileToDataUrl(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}
