import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, CheckCircle2, ClipboardPaste, FolderOpen, FolderPlus, Globe2, HardDrive, Loader2, RotateCcw, ScanLine, Upload } from "lucide-react";
import { latticeApi } from "@/api/client";
import { ActionButton, DataPanel, EntityList, OperationResult, StructuredView, Tabs } from "@/components/primitives";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { t, type Language } from "@/i18n";
import { asArray } from "@/lib/utils";
import { useAppStore } from "@/store/appStore";
import { navigateHash } from "@/features/brain/navigation";

type CaptureTab = "files" | "local" | "browser" | "pipeline";

export function CapturePage({ initialTab }: { initialTab?: string }) {
  const language = useAppStore((state) => state.language);
  const mode = useAppStore((state) => state.mode);
  const [tab, setTab] = React.useState<CaptureTab>((initialTab as CaptureTab) || "files");
  const tabs: Array<{ id: CaptureTab; label: string }> = [
    { id: "files", label: t(language, "capture.tab.files") },
    { id: "local", label: t(language, "capture.tab.local") },
    { id: "browser", label: t(language, "capture.tab.browser") },
    { id: "pipeline", label: t(language, "capture.tab.pipeline") },
  ];
  React.useEffect(() => {
    if (initialTab === "pipeline" || initialTab === "local" || initialTab === "browser" || initialTab === "files") setTab(initialTab);
  }, [initialTab]);
  const selectTab = (next: CaptureTab) => {
    setTab(next);
    navigateHash("/" + ({ files: "capture", local: "my-computer", browser: "capture-browser", pipeline: "pipeline" } as const)[next]);
  };
  return (
    <div className="product-page capture-page space-y-5">
      <header className="page-hero">
        <div className="page-kicker"><Upload className="h-4 w-4" /> {t(language, "capture.kicker")}</div>
        <h1 className="page-title">{t(language, "capture.title")}</h1>
        <p className="page-copy">{t(language, "capture.body")}</p>
      </header>
      <Tabs tabs={mode === "basic" ? tabs.filter((item) => item.id !== "pipeline") : tabs} value={tab} onChange={(id) => selectTab(id as CaptureTab)} />
      {tab === "files" ? <FilesPanel /> : null}
      {tab === "local" ? <LocalPanel /> : null}
      {tab === "browser" ? <BrowserPanel /> : null}
      {tab === "pipeline" ? <PipelinePanel /> : null}
    </div>
  );
}

function FilesPanel() {
  const language = useAppStore((state) => state.language);
  const qc = useQueryClient();
  const docs = useQuery({ queryKey: ["documents"], queryFn: () => latticeApi.documents(200) });
  const [queue, setQueue] = React.useState<UploadQueueItem[]>([]);
  const upload = useMutation({
    mutationFn: (files: File[]) => uploadFiles(files, setQueue),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["documents"] });
      void qc.invalidateQueries({ queryKey: ["graphStats"] });
      void qc.invalidateQueries({ queryKey: ["memoryManager"] });
    },
  });
  const beginUpload = React.useCallback((files: FileList | File[]) => {
    const nextFiles = Array.from(files);
    if (!nextFiles.length) return;
    upload.mutate(nextFiles);
  }, [upload]);
  return (
    <div className="capture-files-flow grid gap-4 xl:grid-cols-[0.75fr_1.25fr]">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Upload className="h-4 w-4" /> {t(language, "capture.files.title")}</CardTitle>
          <CardDescription>{t(language, "capture.files.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <label
            className="flex min-h-56 cursor-pointer flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-border bg-muted/30 p-6 text-center transition hover:bg-muted/50"
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => {
              event.preventDefault();
              beginUpload(event.dataTransfer.files);
            }}
          >
            <Upload className="h-7 w-7 text-primary" />
            <span className="text-lg font-semibold">{t(language, "capture.files.drop")}</span>
            <span className="max-w-sm text-sm leading-6 text-muted-foreground">{t(language, "capture.files.dropDetail")}</span>
            <input type="file" multiple className="sr-only" onChange={(e) => e.target.files && beginUpload(e.target.files)} />
          </label>
          <DocumentUploadQueue queue={queue} onRetry={(file) => beginUpload([file])} />
        </CardContent>
      </Card>
      <DataPanel title={t(language, "capture.files.uploaded")} result={docs.data}>
        {(data) => (
          <div className="space-y-3">
            <EntityList items={(data as Record<string, unknown>).documents || data} titleKey="filename" metaKey="ingest_state" limit={12} />
            <div className="rounded-md border border-border bg-background/55 p-3 text-sm text-muted-foreground">
              {t(language, "capture.files.completed")}
            </div>
          </div>
        )}
      </DataPanel>
    </div>
  );
}

type UploadQueueItem = {
  id: string;
  file: File;
  name: string;
  size: number;
  status: "queued" | "uploading" | "done" | "failed";
  result?: Awaited<ReturnType<typeof latticeApi.uploadDocument>>;
};

async function uploadFiles(files: File[], setQueue: React.Dispatch<React.SetStateAction<UploadQueueItem[]>>) {
  const rows = files.map((file) => ({
    id: `${file.name}-${file.size}-${file.lastModified}-${Date.now()}`,
    file,
    name: displayFileName(file),
    size: file.size,
    status: "queued" as const,
  }));
  setQueue((items) => [...rows, ...items].slice(0, 12));
  const results = [];
  for (const row of rows) {
    setQueue((items) => items.map((item) => item.id === row.id ? { ...item, status: "uploading" } : item));
    const result = await latticeApi.uploadDocument(row.file);
    results.push(result);
    setQueue((items) => items.map((item) => item.id === row.id ? { ...item, status: result.ok ? "done" : "failed", result } : item));
  }
  return results;
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

function uploadResultDetail(item: UploadQueueItem, language: Language) {
  if (item.status === "queued") return t(language, "capture.files.queued");
  if (item.status === "uploading") return t(language, "capture.files.uploading");
  if (!item.result?.ok) return item.result?.error || t(language, "capture.files.failed");
  const data = item.result.data || {};
  const node = String(data.node_id || data.graph_node || data.provenance_id || "");
  return node ? t(language, "capture.files.capturedWithNode", { node }) : t(language, "capture.files.captured");
}

type BrowserFileHandle = {
  kind: "file";
  name: string;
  getFile: () => Promise<File>;
};

type BrowserDirectoryHandle = {
  kind: "directory";
  name: string;
  values?: () => AsyncIterable<BrowserFileHandle | BrowserDirectoryHandle>;
  entries?: () => AsyncIterable<[string, BrowserFileHandle | BrowserDirectoryHandle]>;
};

type BrowserDirectoryPickerWindow = Window & {
  showDirectoryPicker?: () => Promise<BrowserDirectoryHandle>;
};

type DesktopFolderPickerWindow = Window & {
  __TAURI_INTERNALS__?: unknown;
  __TAURI__?: {
    core?: {
      invoke?: <T>(command: string, args?: Record<string, unknown>) => Promise<T>;
    };
  };
  latticeDesktop?: {
    selectFolder?: () => Promise<string | null>;
  };
};

const browserDirectoryInputProps: React.InputHTMLAttributes<HTMLInputElement> & {
  webkitdirectory: string;
  directory: string;
} = {
  webkitdirectory: "",
  directory: "",
};

function displayFileName(file: File) {
  return (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
}

function browserFolderNameFromFiles(files: File[]) {
  const firstPath = (files[0] as (File & { webkitRelativePath?: string }) | undefined)?.webkitRelativePath || "";
  return firstPath.split("/").filter(Boolean)[0] || "";
}

function hasDesktopFolderPicker() {
  const shell = window as DesktopFolderPickerWindow;
  return Boolean(shell.__TAURI__?.core?.invoke || shell.latticeDesktop?.selectFolder || (shell.__TAURI_INTERNALS__ && window.location.protocol === "tauri:"));
}

function isAbortError(error: unknown) {
  return error instanceof DOMException && error.name === "AbortError";
}

async function browserDirectoryEntries(handle: BrowserDirectoryHandle) {
  const entries: Array<BrowserFileHandle | BrowserDirectoryHandle> = [];
  if (typeof handle.values === "function") {
    for await (const entry of handle.values()) entries.push(entry);
    return entries;
  }
  if (typeof handle.entries === "function") {
    for await (const [, entry] of handle.entries()) entries.push(entry);
  }
  return entries;
}

async function filesFromBrowserDirectory(handle: BrowserDirectoryHandle): Promise<File[]> {
  const files: File[] = [];
  for (const entry of await browserDirectoryEntries(handle)) {
    if (entry.kind === "file") {
      files.push(await entry.getFile());
      continue;
    }
    files.push(...await filesFromBrowserDirectory(entry));
  }
  return files;
}

function LocalPanel() {
  const language = useAppStore((state) => state.language);
  const qc = useQueryClient();
  const folderInputRef = React.useRef<HTMLInputElement>(null);
  const [path, setPath] = React.useState("");
  const [folderPickError, setFolderPickError] = React.useState<string | null>(null);
  const [browserFolderName, setBrowserFolderName] = React.useState("");
  const [choosingFolder, setChoosingFolder] = React.useState(false);
  const [folderQueue, setFolderQueue] = React.useState<UploadQueueItem[]>([]);
  const local = useQuery({ queryKey: ["localSources"], queryFn: latticeApi.localSources });
  const agent = useQuery({ queryKey: ["localAgent"], queryFn: latticeApi.localAgent });
  const connect = useMutation({
    mutationFn: (targetPath?: string) => latticeApi.connectFolder((targetPath || path).trim()),
    onSuccess: () => {
      setFolderPickError(null);
      void qc.invalidateQueries({ queryKey: ["localSources"] });
      void qc.invalidateQueries({ queryKey: ["graphStats"] });
      void qc.invalidateQueries({ queryKey: ["memoryManager"] });
    },
  });
  const browserFolderUpload = useMutation({
    mutationFn: (files: File[]) => uploadFiles(files, setFolderQueue),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["documents"] });
      void qc.invalidateQueries({ queryKey: ["graphStats"] });
      void qc.invalidateQueries({ queryKey: ["memoryManager"] });
    },
  });
  const beginBrowserFolderUpload = React.useCallback((files: File[], folderName?: string) => {
    if (!files.length) {
      setFolderPickError(t(language, "capture.local.emptyFolder"));
      return;
    }
    setFolderPickError(null);
    setBrowserFolderName(folderName || browserFolderNameFromFiles(files) || t(language, "capture.local.browserFolder"));
    browserFolderUpload.mutate(files);
  }, [browserFolderUpload, language]);
  const chooseFolder = React.useCallback(async () => {
    if (choosingFolder) return;
    setChoosingFolder(true);
    setFolderPickError(null);
    try {
      const browserPicker = (window as BrowserDirectoryPickerWindow).showDirectoryPicker;
      if (!hasDesktopFolderPicker() && typeof browserPicker === "function") {
        const handle = await browserPicker.call(window);
        beginBrowserFolderUpload(await filesFromBrowserDirectory(handle), handle.name);
        return;
      }
      if (hasDesktopFolderPicker()) {
        const selectedPath = await latticeApi.selectFolder();
        if (selectedPath) {
          setBrowserFolderName("");
          setPath(selectedPath);
          connect.mutate(selectedPath);
          return;
        }
      }
      if (typeof browserPicker === "function") {
        const handle = await browserPicker.call(window);
        beginBrowserFolderUpload(await filesFromBrowserDirectory(handle), handle.name);
        return;
      }
      if (folderInputRef.current) {
        folderInputRef.current.click();
        return;
      }
      setFolderPickError(t(language, "capture.local.pickUnavailable"));
    } catch (error) {
      if (!isAbortError(error)) setFolderPickError(t(language, "capture.local.pickUnavailable"));
    } finally {
      setChoosingFolder(false);
    }
  }, [beginBrowserFolderUpload, choosingFolder, connect, language]);
  const connectCurrent = React.useCallback(() => {
    const target = path.trim();
    if (!target) return;
    setFolderPickError(null);
    setBrowserFolderName("");
    connect.mutate(target);
  }, [connect, path]);
  const browserFolderResults = browserFolderUpload.data || [];
  const browserFolderResultError = browserFolderResults.find((result) => !result.ok)?.error;
  const browserFolderFailed = Boolean(browserFolderUpload.error || browserFolderResultError);
  return (
    <div className="grid gap-4 xl:grid-cols-[0.9fr_1.1fr]">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><FolderPlus className="h-4 w-4" /> {t(language, "capture.local.title")}</CardTitle>
          <CardDescription>{t(language, "capture.local.description")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
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
        </CardContent>
      </Card>
      <DataPanel title={t(language, "capture.local.sources")} result={local.data}>
        {(data) => (
          <div className="space-y-3">
            <EntityList items={(data as Record<string, unknown>).sources} titleKey="path" metaKey="status" />
            {asArray<Record<string, unknown>>((data as Record<string, unknown>).sources).map((source) => (
              <ActionButton
                key={String(source.id || source.source_id || source.path)}
                label={`Stop ${String(source.path || source.id || "source")}`}
                action={() => latticeApi.localWatchStop(String(source.id || source.source_id))}
                invalidate={["localSources"]}
              />
            ))}
          </div>
        )}
      </DataPanel>
      <DataPanel title={t(language, "capture.local.access")} result={agent.data} className="xl:col-span-2">
        {(data) => <StructuredView value={data} />}
      </DataPanel>
    </div>
  );
}

function BrowserPanel() {
  const language = useAppStore((state) => state.language);
  const qc = useQueryClient();
  const [url, setUrl] = React.useState("");
  const read = useMutation({
    mutationFn: (targetUrl: string) => latticeApi.browserReadUrl(targetUrl),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["graphStats"] });
      void qc.invalidateQueries({ queryKey: ["memoryManager"] });
    },
  });
  const captureUrl = React.useCallback(() => {
    const target = normalizeWebUrl(url);
    if (!target) return;
    setUrl(target);
    read.mutate(target);
  }, [read, url]);
  const pasteUrl = React.useCallback(async () => {
    const text = await navigator.clipboard?.readText().catch(() => "");
    const target = normalizeWebUrl(text || "");
    if (!target) return;
    setUrl(target);
    read.mutate(target);
  }, [read]);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Globe2 className="h-4 w-4" /> {t(language, "capture.browser.title")}</CardTitle>
        <CardDescription>{t(language, "capture.browser.description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
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
      </CardContent>
    </Card>
  );
}

function normalizeWebUrl(value: string) {
  const raw = value.trim();
  if (!raw) return "";
  if (/^https?:\/\//i.test(raw)) return raw;
  if (/^[\w.-]+\.[a-z]{2,}([/:?#].*)?$/i.test(raw)) return `https://${raw}`;
  return raw;
}

function PipelinePanel() {
  const language = useAppStore((state) => state.language);
  const index = useQuery({ queryKey: ["index"], queryFn: latticeApi.indexStatus });
  const stats = useQuery({ queryKey: ["graphStats"], queryFn: latticeApi.graphStats });
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <DataPanel title={t(language, "capture.pipeline.status")} result={index.data}>
        {(data) => <StructuredView value={data} />}
      </DataPanel>
      <DataPanel title={t(language, "capture.pipeline.growth")} result={stats.data}>
        {(data) => <StructuredView value={data} />}
      </DataPanel>
      <Card className="xl:col-span-2">
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><HardDrive className="h-4 w-4" /> {t(language, "capture.pipeline.refresh")}</CardTitle>
          <CardDescription>{t(language, "capture.pipeline.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <ActionButton label={t(language, "capture.pipeline.rebuild")} action={() => latticeApi.rebuildIndex()} invalidate={["index"]} />
        </CardContent>
      </Card>
    </div>
  );
}
