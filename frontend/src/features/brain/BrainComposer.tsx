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
    <div className="brain-composer">
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
        placeholder={t(language, "brain.placeholder")}
      />
      <div className="brain-composer-actions">
        {streaming && onStop ? (
          <Button onClick={onStop} variant="outline" className="rounded-full px-5">
            <Square className="h-4 w-4" /> {t(language, "brain.stop")}
          </Button>
        ) : (
          <Button onClick={onSend} disabled={!draft.trim() || streaming} className="rounded-full px-5">
            <Send className="h-4 w-4" /> {t(language, "brain.send")}
          </Button>
        )}
        <label className={`brain-document-input ${uploadingDocument ? "is-disabled" : ""}`}>
          <FileUp className="h-3.5 w-3.5" />
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
          <ImagePlus className="h-3.5 w-3.5" />
          <span>{t(language, "brain.image")}</span>
          <input
            type="file"
            accept="image/*"
            className="sr-only"
            onChange={async (event) => {
              const file = event.target.files?.[0];
              if (file) onImageDataChange(await fileToDataUrl(file));
            }}
          />
        </label>
        {imageData ? <span className="brain-quiet-success">{t(language, "brain.imageAttached")}</span> : null}
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
