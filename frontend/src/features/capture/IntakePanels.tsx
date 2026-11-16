import { AlertCircle, CheckCircle2, ClipboardPaste, FolderOpen, FolderPlus, Globe2, Loader2, RotateCcw, ScanLine, Upload } from "lucide-react";
import { OperationResult } from "@/components/primitives";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { t } from "@/i18n";
import { useAppStore } from "@/store/appStore";
import { browserDirectoryInputProps, type UploadQueueItem, uploadResultDetail } from "./captureDomain";
import { useFileIntake, useFolderIntake, useWebIntake } from "./hooks/useCaptureIntake";

export function FileIntake() {
  const language = useAppStore((state) => state.language);
  const { queue, beginUpload } = useFileIntake();

  return (
    <div className="space-y-4">
      <label
        className="capture-dropzone"
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => {
          event.preventDefault();
          beginUpload(event.dataTransfer.files);
        }}
      >
        <div className="capture-dropzone-mark">
          <Upload className="h-6 w-6" />
        </div>
        <div className="space-y-1">
          <span className="text-lg font-semibold block">{t(language, "capture.files.drop")}</span>
          <span className="max-w-md text-sm text-muted-foreground block">{t(language, "capture.files.dropDetail")}</span>
        </div>
        <input type="file" multiple className="sr-only" onChange={(e) => e.target.files && beginUpload(e.target.files)} />
      </label>
      <DocumentUploadQueue queue={queue} onRetry={(file) => beginUpload([file])} />
    </div>
  );
}

function DocumentUploadQueue({ queue, onRetry }: { queue: UploadQueueItem[]; onRetry: (file: File) => void }) {
  const language = useAppStore((state) => state.language);
  if (!queue.length) return null;
  return (
    <div className="mt-4 space-y-2">
      {queue.map((item) => {
        const detail = uploadResultDetail(item, language);
        return (
          <div key={item.id} className="rounded-md border border-border bg-background/55 p-3 text-sm">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="flex items-center gap-2 font-medium">
                  {item.status === "done" ? <CheckCircle2 className="h-4 w-4 text-emerald-400" /> : item.status === "failed" ? <AlertCircle className="h-4 w-4 text-amber-400" /> : <Loader2 className="h-4 w-4 animate-spin text-primary" />}
                  {item.name}
                </div>
                <div className="mt-1 text-xs text-muted-foreground">
                  {Math.max(1, Math.round(item.size / 1024))} KB · {detail}
                </div>
              </div>
              {item.status === "failed" ? (
                <Button size="sm" variant="outline" onClick={() => onRetry(item.file)}>
                  <RotateCcw className="h-3.5 w-3.5" /> {t(language, "capture.retry")}
                </Button>
              ) : null}
            </div>
            {item.result ? <OperationResult result={item.result} successLabel={t(language, "capture.files.success")} /> : null}
          </div>
        );
      })}
    </div>
  );
}

export function FolderIntake() {
  const language = useAppStore((state) => state.language);
  const {
    folderInputRef,
    path,
    setPath,
    folderPickError,
    browserFolderName,
    choosingFolder,
    folderQueue,
    connect,
    browserFolderUpload,
    beginBrowserFolderUpload,
    chooseFolder,
    connectCurrent,
    browserFolderResultError,
    browserFolderFailed,
  } = useFolderIntake(language);

  return (
    <div className="space-y-3">
      <div className="capture-intake-lead">
        <FolderPlus className="h-4 w-4" aria-hidden="true" />
        <div>
          <strong>{t(language, "capture.local.title")}</strong>
          <p>{t(language, "capture.local.description")}</p>
        </div>
      </div>
      <form
        className="space-y-3"
        onSubmit={(event) => {
          event.preventDefault();
          connectCurrent();
        }}
      >
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input value={path} onChange={(e) => setPath(e.target.value)} placeholder={t(language, "capture.local.placeholder")} />
          <Button type="button" variant="outline" disabled={choosingFolder || connect.isPending || browserFolderUpload.isPending} onClick={() => void chooseFolder()}>
            {choosingFolder ? <Loader2 className="h-4 w-4 animate-spin" /> : <FolderOpen className="h-4 w-4" />}
            {choosingFolder ? t(language, "capture.local.choosing") : t(language, "capture.local.choose")}
          </Button>
        </div>
        <input
          ref={folderInputRef}
          type="file"
          multiple
          className="sr-only"
          aria-hidden="true"
          tabIndex={-1}
          {...browserDirectoryInputProps}
          onChange={(event) => {
            const files = Array.from(event.currentTarget.files || []);
            event.currentTarget.value = "";
            beginBrowserFolderUpload(files);
          }}
        />
        <Button type="submit" disabled={!path.trim() || connect.isPending}>
          {connect.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <ScanLine className="h-4 w-4" />}
          {t(language, "capture.local.connect")}
        </Button>
      </form>
      {folderPickError ? (
        <OperationResult
          result={{ ok: false, status: 0, data: {}, source: "unavailable", error: folderPickError }}
        />
      ) : null}
      {connect.data ? <OperationResult result={connect.data} successLabel={t(language, "capture.local.success")} /> : null}
      {browserFolderName ? (
        <OperationResult
          result={{
            ok: browserFolderUpload.isPending || !browserFolderFailed,
            status: browserFolderFailed ? 0 : 200,
            data: { folder: browserFolderName },
            source: browserFolderFailed ? "unavailable" : "live",
            error: browserFolderUpload.error ? t(language, "capture.local.browserImportFailed") : browserFolderResultError,
          }}
          successLabel={browserFolderUpload.isPending ? t(language, "capture.local.browserImporting") : t(language, "capture.local.browserImportSuccess", { folder: browserFolderName })}
        />
      ) : null}
      <DocumentUploadQueue queue={folderQueue} onRetry={(file) => beginBrowserFolderUpload([file], browserFolderName)} />
    </div>
  );
}

export function WebIntake() {
  const language = useAppStore((state) => state.language);
  const { url, setUrl, read, captureUrl, pasteUrl } = useWebIntake();

  return (
    <div className="space-y-3">
      <div className="capture-intake-lead">
        <Globe2 className="h-4 w-4" aria-hidden="true" />
        <div>
          <strong>{t(language, "capture.browser.title")}</strong>
          <p>{t(language, "capture.browser.description")}</p>
        </div>
      </div>
      <form
        className="flex flex-col gap-2 sm:flex-row"
        onSubmit={(event) => {
          event.preventDefault();
          captureUrl();
        }}
      >
        <Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder={t(language, "capture.browser.placeholder")} inputMode="url" />
        <Button type="button" variant="outline" disabled={read.isPending} onClick={() => void pasteUrl()}>
          <ClipboardPaste className="h-4 w-4" /> {t(language, "capture.browser.paste")}
        </Button>
        <Button type="submit" disabled={!url.trim() || read.isPending}>
          {read.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <ScanLine className="h-4 w-4" />}
          {t(language, "capture.browser.capture")}
        </Button>
      </form>
      {read.data ? <OperationResult result={read.data} successLabel={t(language, "capture.browser.success")} /> : null}
    </div>
  );
}
