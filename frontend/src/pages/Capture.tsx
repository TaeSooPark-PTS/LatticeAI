import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FolderPlus, Globe2, HardDrive, Upload } from "lucide-react";
import { latticeApi } from "@/api/client";
import { ActionButton, DataPanel, EntityList, JsonView, Tabs } from "@/components/primitives";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { asArray } from "@/lib/utils";

type CaptureTab = "files" | "local" | "browser" | "pipeline";

const tabs: Array<{ id: CaptureTab; label: string }> = [
  { id: "files", label: "Files" },
  { id: "local", label: "Local folders" },
  { id: "browser", label: "Web capture" },
  { id: "pipeline", label: "Pipeline" },
];

export function CapturePage({ initialTab }: { initialTab?: string }) {
  const [tab, setTab] = React.useState<CaptureTab>((initialTab as CaptureTab) || "files");
  React.useEffect(() => {
    if (initialTab === "pipeline" || initialTab === "local" || initialTab === "files") setTab(initialTab);
  }, [initialTab]);
  return (
    <div className="space-y-4">
      <header>
        <div className="flex items-center gap-2 text-sm text-primary"><Upload className="h-4 w-4" /> One ingestion door</div>
        <h1 className="mt-2 text-3xl font-semibold">Capture</h1>
        <p className="mt-2 max-w-3xl text-sm text-muted-foreground">Documents, folders, and URLs enter the brain through existing ingestion endpoints with provenance.</p>
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
          <CardTitle className="flex items-center gap-2"><Upload className="h-4 w-4" /> Upload documents</CardTitle>
          <CardDescription>Multipart upload to `/upload/document`; accepted files are parsed and indexed by the backend.</CardDescription>
        </CardHeader>
        <CardContent>
          <label className="flex min-h-44 cursor-pointer flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-border bg-muted/30 p-5 text-center">
            <Upload className="h-7 w-7 text-primary" />
            <span className="font-medium">Choose files</span>
            <span className="text-sm text-muted-foreground">PDF, DOCX, XLSX, PPTX, TXT, MD, CSV according to backend policy.</span>
            <input type="file" multiple className="sr-only" onChange={(e) => e.target.files && upload.mutate(e.target.files)} />
          </label>
          {upload.data ? <JsonView value={upload.data.map((item) => item.data)} /> : null}
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
          <CardTitle className="flex items-center gap-2"><FolderPlus className="h-4 w-4" /> Connect folder</CardTitle>
          <CardDescription>The click is explicit consent; the backend still enforces its permission workflow.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Input value={path} onChange={(e) => setPath(e.target.value)} placeholder="/Users/me/Documents/project" />
          <Button disabled={!path.trim() || connect.isPending} onClick={() => connect.mutate()}>Connect and watch</Button>
          {connect.data ? <JsonView value={connect.data.data || connect.data.error} /> : null}
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
      <DataPanel title="Local runtime probe" result={agent.data} className="xl:col-span-2">
        {(data) => <JsonView value={data} />}
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
        <CardTitle className="flex items-center gap-2"><Globe2 className="h-4 w-4" /> URL capture</CardTitle>
        <CardDescription>Fetches a URL locally through `/api/browser/read-url` and ingests the content with provenance.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://example.com/article" />
          <Button disabled={!url.trim() || read.isPending} onClick={() => read.mutate()}>Capture URL</Button>
        </div>
        {read.data ? <JsonView value={read.data.data || read.data.error} /> : null}
      </CardContent>
    </Card>
  );
}

function PipelinePanel() {
  const index = useQuery({ queryKey: ["index"], queryFn: latticeApi.indexStatus });
  const stats = useQuery({ queryKey: ["graphStats"], queryFn: latticeApi.graphStats });
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <DataPanel title="Index pipeline" result={index.data}>
        {(data) => <JsonView value={data} />}
      </DataPanel>
      <DataPanel title="Graph totals" result={stats.data}>
        {(data) => <JsonView value={data} />}
      </DataPanel>
      <Card className="xl:col-span-2">
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><HardDrive className="h-4 w-4" /> Rebuild controls</CardTitle>
          <CardDescription>Rebuild calls the existing index endpoint. No background work is implied unless the API accepts it.</CardDescription>
        </CardHeader>
        <CardContent>
          <ActionButton label="Rebuild retrieval index" action={() => latticeApi.rebuildIndex()} invalidate={["index"]} />
        </CardContent>
      </Card>
    </div>
  );
}
