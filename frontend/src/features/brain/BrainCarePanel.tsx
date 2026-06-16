import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, ChevronDown, DatabaseBackup, Download, Eye, RotateCcw, ShieldCheck } from "lucide-react";
import { latticeApi, type ApiResult } from "@/api/client";
import { Button } from "@/components/ui/button";
import { t, type Language } from "@/i18n";
import { isRecord, textValue } from "./brainData";

export function BrainCarePanel({ language }: { language: Language }) {
  const qc = useQueryClient();
  const [expanded, setExpanded] = React.useState(false);
  const [archivePath, setArchivePath] = React.useState("");
  const [passphrase, setPassphrase] = React.useState("");
  const [latestResult, setLatestResult] = React.useState<ApiResult | null>(null);
  const portabilityQ = useQuery({ queryKey: ["portability"], queryFn: latticeApi.graphPortability });
  const backupHealthQ = useQuery({ queryKey: ["backupHealth"], queryFn: latticeApi.backupHealth });
  const rememberResult = React.useCallback((result: ApiResult) => setLatestResult(result), []);

  const exportGraph = useCareMutation(() => latticeApi.graphExport(), undefined, rememberResult);
  const backupGraph = useCareMutation(() => latticeApi.graphBackup(), () => {
    void qc.invalidateQueries({ queryKey: ["backupHealth"] });
    void qc.invalidateQueries({ queryKey: ["portability"] });
  }, rememberResult);
  const archiveBrain = useCareMutation(
    () => latticeApi.brainArchive({ path: archivePath.trim() || null, passphrase }),
    () => void qc.invalidateQueries({ queryKey: ["backupHealth"] }),
    rememberResult,
  );
  const inspectArchive = useCareMutation(() => latticeApi.brainArchiveInspect({
    path: archivePath.trim(),
    passphrase: passphrase || null,
  }), undefined, rememberResult);
  const restorePreview = useCareMutation(() => latticeApi.brainArchiveRestore({
    path: archivePath.trim(),
    passphrase,
    dry_run: true,
    confirm: false,
  }), undefined, rememberResult);

  const portableFormat = portabilityLabel(portabilityQ.data?.data);
  const backupStatus = backupHealthLabel(backupHealthQ.data?.data);

  return (
    <section className={`brain-care-panel ${expanded ? "is-expanded" : "is-collapsed"}`} aria-label={t(language, "care.title")}>
      <button
        className="brain-care-summary"
        type="button"
        aria-expanded={expanded}
        aria-controls="brain-care-details"
        onClick={() => setExpanded((value) => !value)}
      >
        <span className="brain-care-summary-main">
          <span><ShieldCheck className="h-3.5 w-3.5" /> {t(language, "care.title")}</span>
          <strong>{t(language, "care.subtitle")}</strong>
        </span>
        <div className="brain-care-proof" aria-label={t(language, "care.ownershipModel")}>
          <span>{t(language, "care.private")}</span>
          <span>{portableFormat}</span>
          <span>{backupStatus}</span>
        </div>
        <ChevronDown className="brain-care-toggle h-4 w-4" aria-hidden="true" />
      </button>

      {expanded ? (
        <div id="brain-care-details" className="brain-care-details">
          <div className="brain-care-actions">
            <CareButton
              icon={<Download className="h-3.5 w-3.5" />}
              label={t(language, "care.export")}
              detail={t(language, "care.export.detail")}
              pendingLabel={t(language, "care.working")}
              pending={exportGraph.isPending}
              onClick={() => exportGraph.mutate()}
            />
            <CareButton
              icon={<DatabaseBackup className="h-3.5 w-3.5" />}
              label={t(language, "care.backup")}
              detail={t(language, "care.backup.detail")}
              pendingLabel={t(language, "care.working")}
              pending={backupGraph.isPending}
              onClick={() => backupGraph.mutate()}
            />
            <CareButton
              icon={<Archive className="h-3.5 w-3.5" />}
              label={t(language, "care.archive")}
              detail={t(language, "care.archive.detail")}
              pendingLabel={t(language, "care.working")}
              pending={archiveBrain.isPending}
              disabled={!passphrase.trim()}
              onClick={() => archiveBrain.mutate()}
            />
          </div>

          <div className="brain-care-archive">
            <input
              value={archivePath}
              onChange={(event) => setArchivePath(event.target.value)}
              placeholder={t(language, "care.path.placeholder")}
              aria-label={t(language, "care.path.label")}
            />
            <input
              type="password"
              value={passphrase}
              onChange={(event) => setPassphrase(event.target.value)}
              placeholder={t(language, "care.passphrase.placeholder")}
              aria-label={t(language, "care.passphrase.label")}
            />
            <div className="brain-care-archive-actions">
              <Button
                variant="outline"
                size="sm"
                disabled={!archivePath.trim() || inspectArchive.isPending}
                onClick={() => inspectArchive.mutate()}
              >
                <Eye className="h-3.5 w-3.5" /> {t(language, "care.inspect")}
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={!archivePath.trim() || !passphrase.trim() || restorePreview.isPending}
                onClick={() => restorePreview.mutate()}
              >
                <RotateCcw className="h-3.5 w-3.5" /> {t(language, "care.restorePreview")}
              </Button>
            </div>
          </div>

          {latestResult ? (
            <div className={`brain-care-result ${latestResult.ok ? "is-ok" : "is-error"}`} role="status">
              {summarizeCareResult(latestResult)}
            </div>
          ) : (
            <p className="brain-care-note">
              {t(language, "care.note")}
            </p>
          )}
        </div>
      ) : null}
    </section>
  );
}

function useCareMutation<T extends ApiResult>(
  mutationFn: () => Promise<T>,
  onSuccess?: () => void,
  onResult?: (result: T) => void,
) {
  return useMutation({
    mutationFn,
    onSuccess: (result) => {
      onResult?.(result);
      onSuccess?.();
    },
  });
}

function CareButton({
  icon,
  label,
  detail,
  pendingLabel,
  pending,
  disabled,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  detail: string;
  pendingLabel: string;
  pending?: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button className="brain-care-button" type="button" disabled={disabled || pending} onClick={onClick}>
      {icon}
      <span>
        <strong>{pending ? pendingLabel : label}</strong>
        <small>{detail}</small>
      </span>
    </button>
  );
}

function portabilityLabel(data: unknown) {
  const record = isRecord(data) ? data : {};
  return textValue(record, ["archive_format", "format", "graph_schema_version", "schema_version"], ".latticebrain");
}

function backupHealthLabel(data: unknown) {
  const record = isRecord(data) ? data : {};
  const count = record.count || record.backups || record.available;
  if (count !== undefined && count !== null && count !== "") return `${count} backups`;
  return "Backups ready";
}

function summarizeCareResult(result: ApiResult) {
  if (!result.ok) return result.error || "Brain care action could not complete.";
  const data = isRecord(result.data) ? result.data : {};
  const directMessage = textValue(data, ["message", "status", "path", "archive_path", "backup_path", "export_path"]);
  if (directMessage) return directMessage;
  return "Brain care action completed.";
}
