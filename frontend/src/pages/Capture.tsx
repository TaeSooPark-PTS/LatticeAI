import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FolderPlus, Globe2, HardDrive, Upload } from "lucide-react";
import { latticeApi } from "@/api/client";
import { ActionButton, DataPanel, EntityList, OperationResult, StructuredView, Tabs } from "@/components/primitives";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { asArray } from "@/lib/utils";

type CaptureTab = "files" | "local" | "browser" | "pipeline";

const tabs: Array<{ id: CaptureTab; label: string }> = [
  { id: "files", label: "Files" },
  { id: "local", label: "Local folders" },
  { id: "browser", label: "Web capture" },
  { id: "pipeline", label: "Processing" },
];

export function CapturePage({ initialTab }: { initialTab?: string }) {
  const [tab, setTab] = React.useState<CaptureTab>((initialTab as CaptureTab) || "files");
  React.useEffect(() => {
    if (initialTab === "pipeline" || initialTab === "local" || initialTab === "files") setTab(initialTab);
  }, [initialTab]);
  return (
    <div className="space-y-5">
      <header className="page-hero">
        <div className="page-kicker"><Upload className="h-4 w-4" /> Capture</div>
        <h1 className="page-title">Bring knowledge into Lattice.</h1>
        <p className="page-copy">Drop in files, connect folders, or save a web page. Lattice keeps track of where each memory came from.</p>
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
  const upload = useMutation({
    mutationFn: (files: FileList) => Promise.all(Array.from(files).map((file) => latticeApi.uploadDocument(file))),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["documents"] }),
  });
  return (
    <div className="grid gap-4 xl:grid-cols-[0.75fr_1.25fr]">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Upload className="h-4 w-4" /> Add documents</CardTitle>
          <CardDescription>Choose files and Lattice will prepare them for search and memory.</CardDescription>
        </CardHeader>
        <CardContent>
          <label className="flex min-h-56 cursor-pointer flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-border bg-muted/30 p-6 text-center transition hover:bg-muted/50">
            <Upload className="h-7 w-7 text-primary" />
            <span className="text-lg font-semibold">Choose files</span>
            <span className="max-w-sm text-sm leading-6 text-muted-foreground">PDF, Office files, notes, markdown, text, and spreadsheets are all welcome.</span>
            <input type="file" multiple className="sr-only" onChange={(e) => e.target.files && upload.mutate(e.target.files)} />
          </label>
          {upload.data ? (
            <div className="mt-3 space-y-2">
              {upload.data.map((item, index) => <OperationResult key={index} result={item} successLabel="Upload completed" />)}
            </div>
          ) : null}
        </CardContent>
      </Card>
      <DataPanel title="Uploaded documents" result={docs.data}>
        {(data) => <EntityList items={(data as Record<string, unknown>).documents || data} titleKey="filename" metaKey="ingest_state" limit={12} />}
      </DataPanel>
    </div>
  );
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
