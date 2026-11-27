import * as React from "react";
import "@/i18n/workspace";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { FolderOpen, Loader2 } from "lucide-react";

import { latticeApi } from "@/api/client";
import type { ImportResponse, ImportSource } from "@/api/imports";
import { EmptyState } from "@/components/primitives";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { t, type Language } from "@/i18n";
import { hasDesktopFolderPicker, pickFolder } from "@/lib/folderPicker";
import { useAppStore } from "@/store/appStore";

const SOURCES: ImportSource[] = ["obsidian", "notion", "git", "mail"];

type Step =
  | { name: "form" }
  | { name: "consent"; token: string; needsAdmin: boolean }
  | { name: "preview"; token: string; data: ImportResponse }
  | { name: "done"; data: ImportResponse };

export default function ImportPanel() {
  const language = useAppStore((state) => state.language);
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["interopStatus"], queryFn: latticeApi.interopStatus });
  const [source, setSource] = React.useState<ImportSource>("obsidian");
  const [path, setPath] = React.useState("");
  const [step, setStep] = React.useState<Step>({ name: "form" });
  const [busy, setBusy] = React.useState<"checking" | "previewing" | "importing" | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [pickerFailed, setPickerFailed] = React.useState(false);

  const statusFailed = status.data?.ok === false;
  const needsStatus = source !== "obsidian";
  const toolMissing = source !== "obsidian" && status.data?.data.sources[source]?.available === false;
  const blocked = needsStatus && (statusFailed || toolMissing);
  const trimmed = path.trim();

  const restart = () => {
    setStep({ name: "form" });
    setError(null);
  };

  const failure = (reason?: string) => reason || t(language, "capture.import.unknownError");

  const request = (extra: { approved: boolean; approval_token?: string; dry_run?: boolean }) =>
    latticeApi.importFromTool({ source, path: trimmed, ...extra });

  const check = async () => {
    setBusy("checking");
    setError(null);
    const result = await request({ approved: false });
    setBusy(null);
    const token = result.data.approval_token;
    if (!result.ok || !token) {
      setError(failure(result.error));
      return;
    }
    setStep({ name: "consent", token, needsAdmin: false });
  };

  const preview = async (token: string) => {
    setBusy("previewing");
    setError(null);
    const result = await request({ approved: true, approval_token: token, dry_run: true });
    setBusy(null);
    if (!result.ok) {
      setError(failure(result.error));
      return;
    }
    setStep({ name: "preview", token, data: result.data });
  };

  const allow = async (token: string) => {
    setBusy("previewing");
    setError(null);
    const approval = await latticeApi.approvePermission(token);
    if (!approval.ok) {
      setBusy(null);
      setError(failure(approval.error));
      setStep({ name: "consent", token, needsAdmin: approval.status === 403 });
      return;
    }
    await preview(token);
  };

  const runImport = async (token: string) => {
    setBusy("importing");
    setError(null);
    const result = await request({ approved: true, approval_token: token, dry_run: false });
    setBusy(null);
    if (!result.ok) {
      setError(failure(result.error));
      return;
    }
    setStep({ name: "done", data: result.data });
    for (const key of ["documents", "graphStats", "memoryManager", "pipelineStatus"]) {
      void qc.invalidateQueries({ queryKey: [key] });
    }
  };

  const choose = async () => {
    setPickerFailed(false);
    const selection = await pickFolder();
    if (selection.kind === "path") {
      setPath(selection.path);
      restart();
      return;
    }
    if (selection.kind !== "cancelled") setPickerFailed(true);
  };

  return (
    <Card className="capture-import" data-testid="capture-import">
      <CardHeader>
        <CardTitle>{t(language, "capture.import.title")}</CardTitle>
        <CardDescription>{t(language, "capture.import.description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="capture-method-switch" role="group" aria-label={t(language, "capture.import.sourceAria")}>
          {SOURCES.map((id) => (
            <button
              key={id}
              type="button"
              aria-pressed={source === id}
              className={source === id ? "is-active" : ""}
              disabled={busy !== null}
              onClick={() => {
                setSource(id);
                setPath("");
                setPickerFailed(false);
                restart();
              }}
            >
              {t(language, `capture.import.source.${id}`)}
            </button>
          ))}
        </div>
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">{t(language, `capture.import.hint.${source}`)}</p>
          {needsStatus && status.isPending ? (
            <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> {t(language, "capture.import.statusLoading")}
            </p>
          ) : null}
          {needsStatus && statusFailed ? (
            <div className="space-y-2">
              <EmptyState title={t(language, "capture.import.unavailableTitle")} detail={failure(status.data?.error)} />
              <Button variant="outline" size="sm" onClick={() => void status.refetch()}>{t(language, "capture.retry")}</Button>
            </div>
          ) : null}
          {needsStatus && !statusFailed && toolMissing ? (
            <EmptyState title={t(language, "capture.import.unavailableTitle")} detail={t(language, "capture.import.toolMissing")} />
          ) : null}
          {blocked ? null : (
            <form
              className="flex flex-col gap-2 sm:flex-row"
              onSubmit={(event) => {
                event.preventDefault();
                if (trimmed && !busy) void check();
              }}
            >
              <Input
                aria-label={t(language, "capture.import.pathLabel")}
                placeholder={t(language, `capture.import.placeholder.${source}`)}
                value={path}
                disabled={busy !== null}
                spellCheck={false}
                onChange={(event) => {
                  setPath(event.target.value);
                  restart();
                }}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && event.nativeEvent.isComposing) event.preventDefault();
                }}
              />
              {hasDesktopFolderPicker() ? (
                <Button type="button" variant="outline" disabled={busy !== null} onClick={() => void choose()}>
                  <FolderOpen className="h-4 w-4" aria-hidden="true" /> {t(language, "capture.import.browse")}
                </Button>
              ) : null}
              <Button type="submit" disabled={!trimmed || busy !== null || step.name !== "form"}>
                {t(language, "capture.import.continue")}
              </Button>
            </form>
          )}
          {pickerFailed ? <p role="status" className="text-sm text-muted-foreground">{t(language, "capture.local.pickUnavailable")}</p> : null}
        </div>

        {busy ? (
          <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> {t(language, `capture.import.busy.${busy}`)}
          </p>
        ) : null}
        {error ? <p role="alert" className="break-words text-sm text-destructive">{error}</p> : null}

        {step.name === "consent" ? (
          <section className="space-y-3 border-t border-border pt-4" aria-label={t(language, "capture.import.consentTitle")}>
            <h3 className="text-sm font-semibold">{t(language, "capture.import.consentTitle")}</h3>
            <p className="break-all text-sm text-muted-foreground">{t(language, "capture.import.consentBody", { path: trimmed })}</p>
            {step.needsAdmin ? <p className="text-sm text-muted-foreground">{t(language, "capture.import.needsAdmin")}</p> : null}
            <div className="flex flex-wrap gap-2">
              <Button disabled={busy !== null} onClick={() => void (step.needsAdmin ? preview(step.token) : allow(step.token))}>
                {t(language, step.needsAdmin ? "capture.import.afterApproval" : "capture.import.allow")}
              </Button>
              <Button variant="outline" disabled={busy !== null} onClick={restart}>{t(language, "capture.import.cancel")}</Button>
            </div>
          </section>
        ) : null}

        {step.name === "preview" ? (
          <PreviewStep
            language={language}
            source={source}
            data={step.data}
            busy={busy !== null}
            onImport={() => void runImport(step.token)}
            onCancel={restart}
          />
        ) : null}

        {step.name === "done" ? (
          <ResultStep
            language={language}
            data={step.data}
            onAgain={() => {
              setPath("");
              restart();
            }}
          />
        ) : null}
      </CardContent>
    </Card>
  );
}

function Counts({ language, entries }: { language: Language; entries: Array<[string, number | undefined]> }) {
  return (
    <dl className="flex flex-wrap gap-x-8 gap-y-2">
      {entries.map(([key, value]) => (
        <div key={key}>
          <dt className="text-xs text-muted-foreground">{t(language, `capture.import.count.${key}`)}</dt>
          <dd className="text-xl font-semibold tabular-nums">{value ?? 0}</dd>
        </div>
      ))}
    </dl>
  );
}

function PreviewStep({
  language,
  source,
  data,
  busy,
  onImport,
  onCancel,
}: {
  language: Language;
  source: ImportSource;
  data: ImportResponse;
  busy: boolean;
  onImport: () => void;
  onCancel: () => void;
}) {
  const found = (source === "obsidian" ? data.notes_found : data.items_found) ?? 0;
  const samples = data.sample_files ?? data.sample_titles ?? [];
  const counts: Array<[string, number | undefined]> = source === "obsidian"
    ? [["scanned", data.files_scanned], ["notes", found], ["links", data.wikilinks_detected]]
    : [["items", found]];
  return (
    <section className="space-y-3 border-t border-border pt-4" aria-label={t(language, "capture.import.previewTitle")}>
      <h3 className="text-sm font-semibold">{t(language, "capture.import.previewTitle")}</h3>
      {found === 0 ? (
        <EmptyState title={t(language, "capture.import.emptyTitle")} detail={t(language, "capture.import.emptyDetail")} />
      ) : (
        <>
          <Counts language={language} entries={counts} />
          <ul className="max-h-56 divide-y divide-border overflow-auto rounded-md border border-border text-sm">
            {samples.map((sample, index) => <li key={`${index}-${sample}`} className="break-all px-3 py-2">{sample}</li>)}
          </ul>
          {found > samples.length ? (
            <p className="text-xs text-muted-foreground">{t(language, "capture.import.more", { count: found - samples.length })}</p>
          ) : null}
        </>
      )}
      <div className="flex flex-wrap gap-2">
        {found > 0 ? <Button disabled={busy} onClick={onImport}>{t(language, "capture.import.run")}</Button> : null}
        <Button variant="outline" disabled={busy} onClick={onCancel}>{t(language, "capture.import.cancel")}</Button>
      </div>
    </section>
  );
}

function ResultStep({ language, data, onAgain }: { language: Language; data: ImportResponse; onAgain: () => void }) {
  const errors = data.errors ?? [];
  return (
    <section className="space-y-3 border-t border-border pt-4" aria-label={t(language, "capture.import.resultTitle")}>
      <h3 className="text-sm font-semibold" role="status">{t(language, "capture.import.resultTitle")}</h3>
      <Counts
        language={language}
        entries={[["ingested", data.ingested], ["duplicates", data.duplicates], ["failed", data.failed]]}
      />
      {errors.length ? (
        <ul
          aria-label={t(language, "capture.import.errorsAria")}
          className="max-h-56 divide-y divide-border overflow-auto rounded-md border border-border text-sm"
        >
          {errors.map((item, index) => (
            <li key={index} className="break-all px-3 py-2">
              <span className="font-medium">{item.file ?? item.title}</span>
              <span className="block text-destructive">{item.error}</span>
            </li>
          ))}
        </ul>
      ) : null}
      <Button variant="outline" onClick={onAgain}>{t(language, "capture.import.again")}</Button>
    </section>
  );
}
