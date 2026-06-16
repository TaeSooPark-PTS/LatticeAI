import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, CheckCircle2, FolderPlus, Globe2, HardDrive, Loader2, RotateCcw, Upload } from "lucide-react";
import { latticeApi } from "@/api/client";
import { ActionButton, DataPanel, EntityList, OperationResult, StructuredView, Tabs } from "@/components/primitives";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { asArray } from "@/lib/utils";

type CaptureTab = "files" | "local" | "browser" | "pipeline";

const tabs: Array<{ id: CaptureTab; label: string }> = [
  { id: "files", label: "Files" },
  { id: "local", label: "Folders" },
  { id: "browser", label: "Web" },
  { id: "pipeline", label: "Flow" },
];

export function CapturePage({ initialTab }: { initialTab?: string }) {
  const [tab, setTab] = React.useState<CaptureTab>((initialTab as CaptureTab) || "files");
  React.useEffect(() => {
    if (initialTab === "pipeline" || initialTab === "local" || initialTab === "files") setTab(initialTab);
  }, [initialTab]);
  return (
    <div className="space-y-5">
      <header className="page-hero">
        <div className="page-kicker"><Upload className="h-4 w-4" /> Add</div>
        <h1 className="page-title">Feed the brain what matters.</h1>
        <p className="page-copy">Drop in files, connect folders, or save a page. Lattice remembers the origin of every idea it learns.</p>
      </header>
      <Tabs tabs={tabs} value={tab} onChange={(id) => setTab(id as CaptureTab)} />
      {tab === "files" ? <FilesPanel /> : null}
      {tab === "local" ? <LocalPanel /> : null}
      {tab === "browser" ? <BrowserPanel /> : null}
      {tab === "pipeline" ? <PipelinePanel /> : null}
    </div>
  );
}

function FilesPanel() {
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
    <div className="grid gap-4 xl:grid-cols-[0.75fr_1.25fr]">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Upload className="h-4 w-4" /> Add documents</CardTitle>
          <CardDescription>Choose files and Lattice will prepare them for search and memory.</CardDescription>
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
            <span className="text-lg font-semibold">Drop files or choose documents</span>
            <span className="max-w-sm text-sm leading-6 text-muted-foreground">Each file is queued, parsed, written to Memory, and linked into the Brain graph with source metadata.</span>
            <input type="file" multiple className="sr-only" onChange={(e) => e.target.files && beginUpload(e.target.files)} />
          </label>
          <DocumentUploadQueue queue={queue} onRetry={(file) => beginUpload([file])} />
        </CardContent>
      </Card>
      <DataPanel title="Uploaded documents" result={docs.data}>
        {(data) => (
          <div className="space-y-3">
            <EntityList items={(data as Record<string, unknown>).documents || data} titleKey="filename" metaKey="ingest_state" limit={12} />
            <div className="rounded-md border border-border bg-background/55 p-3 text-sm text-muted-foreground">
              Completed uploads appear here after they enter Memory. Graph links may continue building briefly after parsing finishes.
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
    name: file.name,
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
  if (!queue.length) return null;
  return (
    <div className="mt-4 space-y-2">
      {queue.map((item) => {
        const detail = uploadResultDetail(item);
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
                  <RotateCcw className="h-3.5 w-3.5" /> Retry
                </Button>
              ) : null}
            </div>
            {item.result ? <OperationResult result={item.result} successLabel="Entered Brain and graph queue" /> : null}
          </div>
        );
      })}
    </div>
  );
}

function uploadResultDetail(item: UploadQueueItem) {
  if (item.status === "queued") return "Waiting in the ingest queue";
  if (item.status === "uploading") return "Parsing and sending to Memory";
  if (!item.result?.ok) return item.result?.error || "Ingest failed before it entered the Brain";
  const data = item.result.data || {};
  const node = String(data.node_id || data.graph_node || data.provenance_id || "");
  return node ? `Captured with source metadata · ${node}` : "Captured with source metadata";
}

function LocalPanel() {
  const qc = useQueryClient();
  const [path, setPath] = React.useState("");
  const local = useQuery({ queryKey: ["localSources"], queryFn: latticeApi.localSources });
  const agent = useQuery({ queryKey: ["localAgent"], queryFn: latticeApi.localAgent });
  const connect = useMutation({
    mutationFn: () => latticeApi.connectFolder(path),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["localSources"] }),
  });
  return (
    <div className="grid gap-4 xl:grid-cols-[0.9fr_1.1fr]">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><FolderPlus className="h-4 w-4" /> Connect a folder</CardTitle>
          <CardDescription>Point Lattice at a folder you want it to remember.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Input value={path} onChange={(e) => setPath(e.target.value)} placeholder="Folder path on this Mac" />
          <Button disabled={!path.trim() || connect.isPending} onClick={() => connect.mutate()}>Connect Folder</Button>
          {connect.data ? <OperationResult result={connect.data} successLabel="Folder connection requested" /> : null}
        </CardContent>
      </Card>
      <DataPanel title="Connected sources" result={local.data}>
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
      <DataPanel title="Folder access" result={agent.data} className="xl:col-span-2">
        {(data) => <StructuredView value={data} />}
      </DataPanel>
    </div>
  );
}

function BrowserPanel() {
  const [url, setUrl] = React.useState("");
  const read = useMutation({ mutationFn: () => latticeApi.browserReadUrl(url) });
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Globe2 className="h-4 w-4" /> Save a web page</CardTitle>
        <CardDescription>Capture a page so Lattice can remember the useful parts.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://example.com/article" />
          <Button disabled={!url.trim() || read.isPending} onClick={() => read.mutate()}>Capture URL</Button>
        </div>
        {read.data ? <OperationResult result={read.data} successLabel="URL capture requested" /> : null}
      </CardContent>
    </Card>
  );
}

function PipelinePanel() {
  const index = useQuery({ queryKey: ["index"], queryFn: latticeApi.indexStatus });
  const stats = useQuery({ queryKey: ["graphStats"], queryFn: latticeApi.graphStats });
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <DataPanel title="Processing status" result={index.data}>
        {(data) => <StructuredView value={data} />}
      </DataPanel>
      <DataPanel title="Brain growth" result={stats.data}>
        {(data) => <StructuredView value={data} />}
      </DataPanel>
      <Card className="xl:col-span-2">
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><HardDrive className="h-4 w-4" /> Refresh memory</CardTitle>
          <CardDescription>Refresh search when you want Lattice to re-check captured material.</CardDescription>
        </CardHeader>
        <CardContent>
          <ActionButton label="Rebuild retrieval index" action={() => latticeApi.rebuildIndex()} invalidate={["index"]} />
        </CardContent>
      </Card>
    </div>
  );
}
