import { CheckCircle2, Sparkles } from "lucide-react";
import { latticeApi } from "@/api/client";
import { NetworkBoundaryPanel } from "@/components/NetworkBoundaryPanel";
import { PermissionModePanel } from "@/components/PermissionModePanel";
import { ActionButton, DataPanel, OperationResult, StructuredView } from "@/components/primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { t } from "@/i18n";
import { useAppStore } from "@/store/appStore";
import { BackupHealthView, HealthView, StorageView } from "./SystemViews";
import { useSettingsPanel } from "./hooks/useSystemPanels";

export function SettingsPanel() {
  const { theme, setTheme, mode, setMode } = useAppStore();
  const language = useAppStore((state) => state.language);
  const {
    health,
    sys,
    comp,
    storage,
    backupHealth,
    dsn,
    setDsn,
    schema,
    setSchema,
    dockerConsent,
    setDockerConsent,
    archivePath,
    setArchivePath,
    restorePath,
    setRestorePath,
    archivePassphrase,
    setArchivePassphrase,
    restoreConfirm,
    setRestoreConfirm,
    importConfirm,
    setImportConfirm,
    docker,
    migration,
    archiveCreate,
    archiveInspect,
    archiveVerify,
    archiveDryRun,
    archiveRestore,
    archiveImportDryRun,
    archiveImport,
  } = useSettingsPanel();

  return (
    <div className="flex flex-col gap-4 max-w-[54rem]">
      <Card>
        <CardHeader>
          <CardTitle>{t(language, "system.panel.appearance")}</CardTitle>
          <CardDescription>{t(language, "system.panel.appearance.hint")}</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          <Button variant={theme === "dark" ? "default" : "outline"} onClick={() => setTheme("dark")}>{t(language, "system.theme.dark")}</Button>
          <Button variant={theme === "light" ? "default" : "outline"} onClick={() => setTheme("light")}>{t(language, "system.theme.light")}</Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t(language, "system.panel.detailLevel")}</CardTitle>
          <CardDescription>{t(language, "system.panel.detailLevel.hint")}</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          {(["basic", "advanced", "admin"] as const).map((item) => (
            <Button key={item} variant={mode === item ? "default" : "outline"} onClick={() => setMode(item)}>{t(language, `shell.mode.${item}`)}</Button>
          ))}
        </CardContent>
      </Card>
      <Card data-testid="system-tools-panel">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Sparkles className="h-4 w-4" aria-hidden="true" />
            {t(language, "system.panel.tools")}
          </CardTitle>
          <CardDescription>{t(language, "system.panel.tools.hint")}</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          {(["search", "read", "skill", "mcp"] as const).map((item) => (
            <Badge key={item} variant="muted">{t(language, `system.panel.tools.${item}`)}</Badge>
          ))}
        </CardContent>
      </Card>
      <PermissionModePanel />
      <NetworkBoundaryPanel />
      <DataPanel title={mode === "basic" ? t(language, "system.panel.brainStatus") : t(language, "system.panel.serverHealth")} result={health.data}>
        {(data) => <HealthView data={data as Record<string, unknown>} />}
      </DataPanel>
      <DataPanel title={mode === "basic" ? t(language, "system.panel.readiness") : t(language, "system.panel.hostTelemetry")} result={sys.data}>
        {(data) => {
          if (mode === "basic") {
            const readiness = String((data as Record<string, unknown>)?.readiness || "roomy");
            const key = (readiness === "tight" || readiness === "low" || readiness === "roomy")
              ? `system.readiness.${readiness}`
              : "system.readiness.plenty";
            const iconColor = readiness === "low" ? "text-rose-500" : readiness === "tight" ? "text-amber-500" : "text-emerald-500";
            return (
              <div className="flex items-center gap-2.5 p-3 rounded-lg border border-border/60 bg-muted/20 text-sm text-foreground">
                <CheckCircle2 className={`h-4 w-4 ${iconColor} flex-shrink-0`} aria-hidden="true" />
                <span>{t(language, key)}</span>
              </div>
            );
          }
          return <StructuredView value={data} />;
        }}
      </DataPanel>
      <DataPanel title={mode === "basic" ? t(language, "system.storage.title") : t(language, "system.panel.brainStorage")} result={storage.data}>
        {(data) => <StorageView data={data as Record<string, unknown>} mode={mode} language={language} />}
      </DataPanel>
      {mode === "basic" ? null : (
        <DataPanel title={t(language, "system.backup.health")} result={backupHealth.data}>
          {(data) => <BackupHealthView data={data as Record<string, unknown>} />}
        </DataPanel>
      )}
      {mode === "basic" ? null : (
        <Card>
          <CardHeader>
            <CardTitle>{t(language, "system.archive.title")}</CardTitle>
            <CardDescription>{t(language, "system.archive.detail")}</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <div className="flex flex-col sm:flex-row gap-2">
              <Input value={archivePath} onChange={(e) => setArchivePath(e.target.value)} placeholder={t(language, "system.archive.exportPath")} />
              <Input value={restorePath} onChange={(e) => setRestorePath(e.target.value)} placeholder={t(language, "system.archive.restorePath")} />
            </div>
            <Input type="password" value={archivePassphrase} onChange={(e) => setArchivePassphrase(e.target.value)} placeholder={t(language, "system.archive.passphrase")} />
            <div className="flex flex-wrap gap-2">
              <Button onClick={() => archiveCreate.mutate()} disabled={!archivePassphrase || archiveCreate.isPending}>{t(language, "system.archive.export")}</Button>
              <Button variant="outline" onClick={() => archiveInspect.mutate()} disabled={!restorePath || archiveInspect.isPending}>{t(language, "system.archive.inspect")}</Button>
              <Button variant="outline" onClick={() => archiveVerify.mutate()} disabled={!restorePath || !archivePassphrase || archiveVerify.isPending}>{t(language, "system.archive.verify")}</Button>
              <Button variant="outline" onClick={() => archiveDryRun.mutate()} disabled={!restorePath || !archivePassphrase || archiveDryRun.isPending}>{t(language, "system.archive.restoreDryRun")}</Button>
              <Button variant="outline" onClick={() => archiveImportDryRun.mutate()} disabled={!restorePath || !archivePassphrase || archiveImportDryRun.isPending}>{t(language, "system.archive.importDryRun")}</Button>
              <label className="flex items-center gap-2 rounded-md border border-border px-3 py-2 text-sm">
                <input type="checkbox" checked={restoreConfirm} onChange={(e) => setRestoreConfirm(e.target.checked)} />
                {t(language, "system.archive.confirmRestore")}
              </label>
              <Button variant="destructive" onClick={() => archiveRestore.mutate()} disabled={!restorePath || !archivePassphrase || !restoreConfirm || archiveRestore.isPending}>{t(language, "system.archive.restore")}</Button>
              <label className="flex items-center gap-2 rounded-md border border-border px-3 py-2 text-sm">
                <input type="checkbox" checked={importConfirm} onChange={(e) => setImportConfirm(e.target.checked)} />
                {t(language, "system.archive.confirmImport")}
              </label>
              <Button variant="outline" onClick={() => archiveImport.mutate()} disabled={!restorePath || !archivePassphrase || !importConfirm || archiveImport.isPending}>{t(language, "system.archive.import")}</Button>
            </div>
            {[archiveCreate.data, archiveInspect.data, archiveVerify.data, archiveDryRun.data, archiveRestore.data, archiveImportDryRun.data, archiveImport.data].filter(Boolean).map((item, i) => (
              <OperationResult key={i} result={item} successLabel={t(language, "system.archive.requestDone")} />
            ))}
          </CardContent>
        </Card>
      )}
      {mode !== "basic" ? <Card>
        <CardHeader>
          <CardTitle>{t(language, "system.scale.title")}</CardTitle>
          <CardDescription>{t(language, "system.scale.detail")}</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="flex flex-col sm:flex-row gap-2">
            <Input value={dsn} onChange={(e) => setDsn(e.target.value)} placeholder={t(language, "system.scale.dsn")} />
            <Input value={schema} onChange={(e) => setSchema(e.target.value)} placeholder={t(language, "system.scale.schema")} />
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={() => docker.mutate(false)} disabled={docker.isPending}>{t(language, "system.scale.dockerPlan")}</Button>
            <label className="flex items-center gap-2 rounded-md border border-border px-3 py-2 text-sm">
              <input type="checkbox" checked={dockerConsent} onChange={(e) => setDockerConsent(e.target.checked)} />
              {t(language, "system.scale.dockerConsent")}
            </label>
            <Button onClick={() => docker.mutate(true)} disabled={!dockerConsent || docker.isPending}>{t(language, "system.scale.dockerStart")}</Button>
            <Button variant="outline" onClick={() => migration.mutate()} disabled={!dsn || migration.isPending}>{t(language, "system.scale.migrationPlan")}</Button>
          </div>
          {docker.data ? <OperationResult result={docker.data} successLabel={t(language, "system.scale.dockerDone")} /> : null}
          {migration.data ? <OperationResult result={migration.data} successLabel={t(language, "system.scale.migrationDone")} /> : null}
        </CardContent>
      </Card> : null}
      {mode === "basic" ? null : (
        <DataPanel title={t(language, "system.computerMemory.title")} result={comp.data}>
          {(data) => (
            <div className="space-y-3">
              <StructuredView value={data} />
              <div className="flex gap-2">
                <ActionButton label={t(language, "system.computerMemory.enable")} action={() => latticeApi.setComputerMemory(true)} invalidate={["computerMemory"]} />
                <ActionButton label={t(language, "system.computerMemory.disable")} action={() => latticeApi.setComputerMemory(false)} invalidate={["computerMemory"]} variant="destructive" />
              </div>
            </div>
          )}
        </DataPanel>
      )}
    </div>
  );
}
