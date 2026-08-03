import * as React from "react";
// Route-scoped copy: importing the namespace registers it into the shared
// table and keeps it inside this lazy chunk instead of the entry bundle.
import "@/i18n/workspace";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, CheckCircle2, ClipboardPaste, FolderOpen, FolderPlus, Globe2, HardDrive, Loader2, RotateCcw, ScanLine, Share2, Sparkles, Upload } from "lucide-react";
import { latticeApi } from "@/api/client";
import { ActionButton, DataPanel, EntityList, OperationResult, StructuredView } from "@/components/primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { t, type Language } from "@/i18n";
import { asArray, fmtNumber, isRecord } from "@/lib/utils";
import { FolderMemoryHealthCard } from "@/features/capture/FolderMemoryHealth";
import { useAppStore } from "@/store/appStore";
import { navigateHash } from "@/features/brain/navigation";

type CaptureMethod = "files" | "local" | "browser";

/**
 * One way in, three shapes of material.
 *
 * These used to be three of four page-level tabs, sitting beside "처리 과정" as
 * if choosing a *file* and inspecting the *indexer* were the same kind of
 * decision. A newcomer arriving to add something had to first work out which of
 * four equal-looking tabs held the thing they wanted, and connecting a folder —
 * the highest-value action on the screen — was hidden two tabs deep.
 *
 * Now the page has one primary station at the top that owns every way of adding
 * material, and everything that reports on material already added drops to a
 * quieter row beneath it. The deep links (`#/capture`, `#/my-computer`,
 * `#/capture-browser`) still land on their own method, and `#/pipeline` still
 * works — the status it named is simply always on screen now.
 */
const captureMethods: Array<{ id: CaptureMethod; labelKey: string; icon: typeof Upload; path: string }> = [
  { id: "files", labelKey: "capture.method.files", icon: Upload, path: "capture" },
  { id: "local", labelKey: "capture.method.local", icon: FolderPlus, path: "my-computer" },
  { id: "browser", labelKey: "capture.method.browser", icon: Globe2, path: "capture-browser" },
];

function normalizeCaptureMethod(tab?: string): CaptureMethod {
  return captureMethods.some((item) => item.id === tab) ? (tab as CaptureMethod) : "files";
}

export function CapturePage({ initialTab }: { initialTab?: string }) {
  const language = useAppStore((state) => state.language);
  const [method, setMethod] = React.useState<CaptureMethod>(() => normalizeCaptureMethod(initialTab));
  React.useEffect(() => {
    setMethod(normalizeCaptureMethod(initialTab));
  }, [initialTab]);
  const selectMethod = (next: CaptureMethod) => {
    setMethod(next);
    navigateHash("/" + (captureMethods.find((item) => item.id === next)?.path || "capture"));
  };
  return (
    <div className="product-page capture-page space-y-5">
      <header className="page-hero">
        <div className="page-kicker"><Upload className="h-4 w-4" /> {t(language, "capture.kicker")}</div>
        <h1 className="page-title">{t(language, "capture.title")}</h1>
        <p className="page-copy">{t(language, "capture.body")}</p>
      </header>

      {/* 1순위 — the single station every way in now belongs to. */}
      <section className="capture-station" aria-label={t(language, "capture.station.aria")}>
        <div className="capture-station-head">
          <h2>{t(language, "capture.station.title")}</h2>
          <p>{t(language, "capture.station.detail")}</p>
        </div>
        {/* Toggle buttons, not tabs: these pick the shape of the thing you are
            adding inside one action, rather than switching to another screen. */}
        <div className="capture-method-switch" role="group" aria-label={t(language, "capture.method.aria")}>
          {captureMethods.map(({ id, labelKey, icon: Icon }) => (
            <button
              key={id}
              type="button"
              aria-pressed={method === id}
              className={method === id ? "is-active" : ""}
              data-testid={`capture-method-${id}`}
              onClick={() => selectMethod(id)}
            >
              <Icon className="h-4 w-4" aria-hidden="true" />
              {t(language, labelKey)}
            </button>
          ))}
        </div>
        <div className="capture-station-body">
          {method === "files" ? <FileIntake /> : null}
          {method === "local" ? <FolderIntake /> : null}
          {method === "browser" ? <WebIntake /> : null}
        </div>
      </section>

      {/* 2순위 — what happened to everything already added. Two columns, both
          quieter than the station: progress on the left, the material itself on
          the right. */}
      <div className="capture-secondary">
        <PipelinePanel />
        <RecentCapturePanel />
      </div>
    </div>
  );
}

function FileIntake() {
  const language = useAppStore((state) => state.language);
  const qc = useQueryClient();
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
    <div className="space-y-4">
      <label
        className="flex min-h-48 cursor-pointer flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed border-primary/30 bg-muted/20 p-6 text-center transition hover:border-primary/60 hover:bg-muted/40"
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => {
          event.preventDefault();
          beginUpload(event.dataTransfer.files);
        }}
      >
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-primary/10 text-primary">
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

/**
 * What came in, and how the connected folders are doing — the second read, in
 * its own column. The folder health card renders nothing until a folder is
 * connected, so on a first run this column is just the document list.
 */
function RecentCapturePanel() {
  const language = useAppStore((state) => state.language);
  const mode = useAppStore((state) => state.mode);
  const docs = useQuery({ queryKey: ["documents"], queryFn: () => latticeApi.documents(200) });
  const local = useQuery({ queryKey: ["localSources"], queryFn: latticeApi.localSources });
  const agent = useQuery({ queryKey: ["localAgent"], queryFn: latticeApi.localAgent });
  return (
    <div className="capture-secondary-column space-y-4">
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
      <FolderMemoryHealthCard language={language} />
      <DataPanel title={t(language, "capture.local.sources")} result={local.data}>
        {(data) => (
          <div className="space-y-3">
            <EntityList items={(data as Record<string, unknown>).sources} titleKey="path" metaKey="status" />
            {asArray<Record<string, unknown>>((data as Record<string, unknown>).sources).map((source) => (
              <ActionButton
                key={String(source.id || source.source_id || source.path)}
                label={t(language, "capture.local.stop", { source: String(source.path || source.id || t(language, "capture.local.source")) })}
                action={() => latticeApi.localWatchStop(String(source.id || source.source_id))}
                invalidate={["localSources"]}
              />
            ))}
          </div>
        )}
      </DataPanel>
      {/* Raw folder-permission payload: still here, still one scroll away, but
          no longer sharing a row with the thing you came to do. */}
      {mode === "basic" ? null : (
        <DataPanel title={t(language, "capture.local.access")} result={agent.data}>
          {(data) => <StructuredView value={data} />}
        </DataPanel>
      )}
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

function FolderIntake() {
  const language = useAppStore((state) => state.language);
  const qc = useQueryClient();
  const folderInputRef = React.useRef<HTMLInputElement>(null);
  const [path, setPath] = React.useState("");
  const [folderPickError, setFolderPickError] = React.useState<string | null>(null);
  const [browserFolderName, setBrowserFolderName] = React.useState("");
  const [choosingFolder, setChoosingFolder] = React.useState(false);
  const [folderQueue, setFolderQueue] = React.useState<UploadQueueItem[]>([]);
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

function WebIntake() {
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

function normalizeWebUrl(value: string) {
  const raw = value.trim();
  if (!raw) return "";
  if (/^https?:\/\//i.test(raw)) return raw;
  if (/^[\w.-]+\.[a-z]{2,}([/:?#].*)?$/i.test(raw)) return `https://${raw}`;
  return raw;
}

/**
 * What a person can actually see of their own material.
 *
 * The two counts and the three states below are read tolerantly from whichever
 * shape the server sends: `/api/index/status` reports per-stage pipelines, and
 * older/simpler builds report a flat `{ pending, total }`. Neither is shown as
 * a number the reader cannot check — every count says what it counted.
 */
type JourneyState = "done" | "working" | "waiting";

function readJourney(pipelineStatus: unknown, index: unknown, stats: unknown) {
  const statusData = isRecord(pipelineStatus) ? pipelineStatus : {};
  const indexData = isRecord(index) ? index : {};
  const statsData = isRecord(stats) ? stats : {};
  const pipelines = isRecord(indexData.pipelines) ? indexData.pipelines : {};

  const num = (...values: unknown[]) => {
    for (const value of values) {
      if (typeof value === "number" && Number.isFinite(value) && value >= 0) return Math.round(value);
      const parsed = Number(value);
      if (Number.isFinite(parsed) && parsed > 0) return Math.round(parsed);
    }
    return undefined;
  };

  const received = num(statusData.received);
  const extracted = num(statusData.extracted);
  const connected = num(statusData.connected);

  const remembered = num(extracted, indexData.total, indexData.total_items, statsData.nodes, statsData.total_nodes) || 0;
  const connections = num(connected, statsData.edges, statsData.total_edges) || 0;
  const waiting = num(indexData.pending, indexData.pending_items) || 0;

  const readState: JourneyState = (received !== undefined ? (received > 0 ? "done" : "waiting") : (waiting ? "working" : (remembered ? "done" : "waiting")));
  const understandState: JourneyState = (extracted !== undefined ? (extracted > 0 ? "done" : "waiting") : (remembered ? "done" : "waiting"));
  const connectState: JourneyState = (connected !== undefined ? (connected > 0 ? "done" : "waiting") : (connections ? "done" : "waiting"));

  return {
    remembered,
    connections,
    waiting,
    received: received ?? waiting,
    extracted: extracted ?? remembered,
    connected: connected ?? connections,
    steps: [
      { key: "read", state: readState, count: received ?? (remembered || waiting) },
      { key: "understand", state: understandState, count: extracted ?? remembered },
      { key: "connect", state: connectState, count: connected ?? connections },
    ] as Array<{ key: string; state: JourneyState; count?: number }>,
  };
}

function PipelinePanel() {
  const language = useAppStore((state) => state.language);
  const mode = useAppStore((state) => state.mode);
  const index = useQuery({ queryKey: ["index"], queryFn: latticeApi.indexStatus });
  const stats = useQuery({ queryKey: ["graphStats"], queryFn: latticeApi.graphStats });
  const pipelineStatus = useQuery({ queryKey: ["pipelineStatus"], queryFn: latticeApi.pipelineStatus });
  const statusData = (pipelineStatus.data?.data || pipelineStatus.data);

  const journey = readJourney(statusData, index.data?.data, stats.data?.data);
  const stepIcon = { read: ScanLine, understand: Sparkles, connect: Share2 } as const;

  const hasPipelineData = Boolean(
    journey.remembered ||
    journey.connections ||
    (journey.received && journey.received > 0)
  );

  return (
    <div className="capture-secondary-column space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><ScanLine className="h-4 w-4" /> {t(language, "capture.pipeline.journey.title")}</CardTitle>
          <CardDescription>{t(language, "capture.pipeline.journey.detail")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <ol className="capture-journey flex flex-col md:flex-row gap-3 items-stretch" aria-label={t(language, "capture.pipeline.journey.aria")}>
            {journey.steps.map(({ key, state, count }) => {
              const Icon = stepIcon[key as keyof typeof stepIcon];
              return (
                <li key={key} className={`capture-journey-step is-${state} flex-1 p-3 rounded-lg border border-border/60 bg-muted/20 flex flex-col justify-between`}>
                  <div className="capture-journey-step-header flex items-center gap-2">
                    <span className="capture-journey-mark" aria-hidden="true"><Icon className="h-4 w-4" /></span>
                    <div className="capture-journey-copy min-w-0 flex-1">
                      <strong className="block text-sm">{t(language, `capture.pipeline.step.${key}`)}</strong>
                      <small className="block text-xs text-muted-foreground">{t(language, `capture.pipeline.step.${key}.detail`)}</small>
                    </div>
                  </div>
                  <div className="capture-journey-step-meta flex items-center justify-between gap-2 mt-3 pt-2 border-t border-border/40 text-xs">
                    <Badge variant="muted" className="capture-journey-count">
                      {count !== undefined ? count : "—"}
                    </Badge>
                    <span className="capture-journey-state">{t(language, `capture.pipeline.step.${state}`)}</span>
                  </div>
                </li>
              );
            })}
          </ol>
          {hasPipelineData ? (
            <div className="flex flex-wrap gap-2 text-sm pt-2">
              <Badge variant="success">{t(language, "capture.pipeline.count.remembered", { count: fmtNumber(journey.extracted ?? journey.remembered) })}</Badge>
              <Badge variant="muted">{t(language, "capture.pipeline.count.connections", { count: fmtNumber(journey.connected ?? journey.connections) })}</Badge>
              <Badge variant={journey.waiting ? "warning" : "muted"}>
                {journey.waiting
                  ? t(language, "capture.pipeline.count.waiting", { count: fmtNumber(journey.waiting) })
                  : t(language, "capture.pipeline.count.none")}
              </Badge>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">{t(language, "capture.pipeline.empty")}</p>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><HardDrive className="h-4 w-4" /> {t(language, "capture.pipeline.refresh")}</CardTitle>
          <CardDescription>{t(language, "capture.pipeline.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <ActionButton label={t(language, "capture.pipeline.rebuild")} action={() => latticeApi.rebuildIndex()} invalidate={["index"]} />
        </CardContent>
      </Card>
      {/* The raw payloads are still one click away — nothing was removed, it
          just stopped being the first thing on the screen. */}
      {mode === "basic" ? null : (
        <>
          <DataPanel title={t(language, "capture.pipeline.status")} result={index.data}>
            {(data) => <StructuredView value={data} />}
          </DataPanel>
          <DataPanel title={t(language, "capture.pipeline.growth")} result={stats.data}>
            {(data) => <StructuredView value={data} />}
          </DataPanel>
        </>
      )}
    </div>
  );
}
