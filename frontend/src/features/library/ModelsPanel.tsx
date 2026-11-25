import * as React from "react";
import { CheckCircle2, Cpu, Download, PackagePlus, PlayCircle, ShieldAlert } from "lucide-react";
import { latticeApi } from "@/api/client";
import { ActionButton, DataPanel, EmptyState, EntityList, StatGrid, StructuredView, ValuePreview } from "@/components/primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { t, type Language } from "@/i18n";
import { asArray, humanizeModelId } from "@/lib/utils";
import { useAppStore } from "@/store/appStore";
import {
  asRecord,
  describeComputer,
  embeddingStateLabel,
  formatModelProvenanceChain,
  localizeModelMessage,
  MODEL_STATUS_KEYS,
} from "./libraryDomain";
import { useLibraryModels } from "./hooks/useLibraryModels";

export function ModelsPanel() {
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  const {
    models,
    recs,
    emb,
    consent,
    setConsent,
    activeModel,
    latestProgress,
    latestMetrics,
    lastResult,
    lastError,
    cancelledNotice,
    busy,
    prepareModel,
    cancelPrepare,
    unloadCurrentModel,
    currentId,
    catalog,
    recommendationById,
    current,
    topPick,
    embeddingRecId,
    embeddingRecLoadId,
    embeddingRecEngine,
    embeddingRecName,
    embeddingRecSize,
    embeddingFallbackRepo,
    embeddingLoaded,
  } = useLibraryModels(language);

  const modelMessage = React.useCallback(
    (message: unknown, fallbackKey = "library.model.notReady") =>
      localizeModelMessage(message, language, mode, fallbackKey),
    [language, mode],
  );

  return (
    <div className="flex flex-col gap-4">
      <div className="space-y-4">
        <DataPanel title={t(language, "library.models.setup.title")} description={t(language, "library.models.setup.desc")} result={recs.data}>
          {(data) => {
            const recommendation = (data as Record<string, unknown>).recommendations as Record<string, unknown> | undefined;
            const profile = (data as Record<string, unknown>).profile as Record<string, unknown> | undefined;
            return (
              <div className="space-y-4">
                <ModelRuntimeSummary
                  language={language}
                  mode={mode}
                  profile={profile || {}}
                  recommendation={recommendation || {}}
                  current={current}
                  currentId={currentId}
                  latestProgress={latestProgress}
                  lastResult={lastResult}
                  onReload={() => {
                    if (current) {
                      const loadId = String(current.recommended_load_id || current.id);
                      const engine = String(current.recommended_engine || current.engine || "local_mlx");
                      void prepareModel(loadId, engine, consent);
                    }
                  }}
                  onUnload={unloadCurrentModel}
                />
                <ol className="library-setup-track" aria-label={t(language, "library.steps.aria")}>
                  {[
                    [t(language, "library.step.environment"), true, Cpu],
                    [t(language, "library.step.recommend"), Boolean(topPick || catalog.length), CheckCircle2],
                    [t(language, "library.step.install"), Boolean(current || latestProgress?.stage === "engine"), PackagePlus],
                    [t(language, "library.step.download"), Boolean(current || latestProgress?.stage === "download"), Download],
                    [t(language, "library.step.validate"), Boolean(current || latestProgress?.stage === "smoke_test"), ShieldAlert],
                    [t(language, "library.step.ready"), Boolean(current || lastResult), PlayCircle],
                  ].map(([label, done, Icon]) => (
                    <li key={String(label)} className={done ? "is-done" : ""}>
                      <span className="library-setup-mark" aria-hidden="true">
                        {React.createElement(Icon as typeof Cpu, { className: "h-3.5 w-3.5" })}
                      </span>
                      <span className="library-setup-label">{String(label)}</span>
                      <span className="library-setup-state">
                        {done ? t(language, "library.step.done") : t(language, "library.step.pending")}
                      </span>
                    </li>
                  ))}
                </ol>
                <label className="flex items-start gap-2 rounded-lg border border-border bg-background/55 p-3 text-sm leading-6">
                  <input className="mt-1" type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />
                  <span>
                    {t(language, "library.consent")}
                  </span>
                </label>
                {latestProgress ? (
                  <div className="rounded-lg border border-border bg-background/55 p-3 text-sm" data-testid="library-download-progress">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="font-medium">{String(latestProgress.message || t(language, "library.preparing"))}</div>
                      <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                        <span data-testid="library-progress-percent">{t(language, "library.progress.percent", { percent: latestMetrics.percent })}</span>
                        {latestMetrics.bytesLabel ? (
                          <span data-testid="library-progress-bytes">{latestMetrics.bytesLabel}</span>
                        ) : null}
                        {latestMetrics.speedMbps ? (
                          <span data-testid="library-progress-speed">{t(language, "library.progress.speedMbps", { speed: latestMetrics.speedMbps })}</span>
                        ) : null}
                        {latestMetrics.etaSeconds !== null ? (
                          <span data-testid="library-progress-eta">{t(language, "library.progress.etaSeconds", { seconds: latestMetrics.etaSeconds })}</span>
                        ) : null}
                        {busy ? (
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            data-testid="library-cancel-prepare"
                            onClick={cancelPrepare}
                          >
                            {t(language, "library.progress.cancel")}
                          </Button>
                        ) : null}
                      </div>
                    </div>
                    <div className="mt-2 h-2 overflow-hidden rounded-full bg-muted">
                      <div className="h-full bg-primary" style={{ width: `${Number(latestProgress.percent || 8)}%` }} />
                    </div>
                    {latestProgress.detail ? <div className="mt-2 text-xs text-muted-foreground">{String(latestProgress.detail)}</div> : null}
                    {latestMetrics.gatedFallback ? (
                      <div className="mt-2 text-xs text-muted-foreground" data-testid="library-progress-fallback">
                        {t(language, "library.progress.fallbackNotice", { repo: latestMetrics.fallbackRepo })}
                      </div>
                    ) : null}
                  </div>
                ) : cancelledNotice ? (
                  <div className="rounded-lg border border-border bg-background/55 p-3 text-xs text-muted-foreground" role="status" data-testid="library-download-cancelled">
                    {t(language, "library.progress.cancelled")}
                  </div>
                ) : null}
                {lastError ? <ModelRecovery error={lastError} /> : null}
              </div>
            );
          }}
        </DataPanel>
        <DataPanel title={mode === "basic" ? t(language, "library.panel.recommended.basic") : t(language, "library.panel.recommended.other")} result={models.data}>
          {(data) => (
            <div className="grid gap-2">
              {(catalog.length ? catalog : asArray<Record<string, unknown>>((data as Record<string, unknown>).loaded)).slice(0, mode === "basic" ? 3 : 14).map((model, index) => {
                const id = String(model.id || model.model_id || model.name || index);
                const loaded = asArray<string>((data as Record<string, unknown>).loaded).includes(id) || (data as Record<string, unknown>).current === id || model.state === "loaded";
                const loadId = String(model.recommended_load_id || id);
                const engine = String(model.recommended_engine || model.engine || "");
                const recommendation = recommendationById.get(id) || recommendationById.get(loadId) || {};
                const modelVerification = asRecord(model.verification);
                const recommendationVerification = asRecord(recommendation.verification);
                const modelHardware = asRecord(model.hardware);
                const recommendationHardware = asRecord(recommendation.hardware);
                const downloadRequired = Boolean(model.download_required);
                const installRequired = Boolean(model.install_required);
                const recommendedRam = modelHardware.recommended_ram_gb || recommendationHardware.recommended_ram_gb;
                const hardwareNote = recommendedRam
                  ? t(language, "library.model.ramRecommended", { ram: String(recommendedRam) })
                  : mode === "basic"
                    ? ""
                    : String(modelHardware.notes || recommendationHardware.notes || "");
                const safetyNotes = model.safety_notes || recommendation.safety_notes;
                const licenseText = model.license || recommendation.license;
                const compatibility = (model.runtime_compatibility || recommendation.runtime_compatibility || {}) as Record<string, unknown>;
                const fallbackAvailable = String(compatibility.status || "") === "fallback_available";
                const unsupported = model.load_status === "unsupported" || compatibility.supported === false;
                const loadAvailable = (Boolean(model.load_available) || loaded) && !unsupported;
                const loadStatus = String(model.load_status || (loaded ? "loaded" : "unavailable"));
                const unavailableReason = downloadRequired
                  ? t(language, "library.model.needsDownloadFirst")
                  : modelMessage(model.unavailable_reason || t(language, "library.model.notReady"));
                const runtimeLabel = String(model.runtime_label || compatibility.preferred_runtime || engine || "local_mlx");
                const actionLabel = String(compatibility.action || loadStatus.replace(/_/g, " "));
                const statusLabel = MODEL_STATUS_KEYS.has(loadStatus)
                  ? t(language, `library.model.status.${loadStatus}`)
                  : t(language, "library.model.status.unknown");
                const badgeLabel = unsupported && mode === "basic"
                  ? t(language, "library.model.needsAttention")
                  : statusLabel;
                const canPrepare = loadAvailable || downloadRequired || installRequired;
                const needsConsentGate = downloadRequired || installRequired;
                const downloadSize = model.download_size || model.estimated_size || recommendation.download_size || recommendation.estimated_size || model.size || recommendation.size;
                const provenanceChain = formatModelProvenanceChain(model, recommendation);
                return (
                  <div key={id} className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 rounded-lg border border-border bg-background/55 p-4">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <div className="text-base font-semibold">{String(model.name || id)}</div>
                        {topPick?.id === id || model.recommended_default ? <Badge variant="success">{t(language, "library.model.recommended")}</Badge> : null}
                        {String(model.modality || recommendation.modality || "").includes("multi") || String(model.modality || "") === "multimodal" ? <Badge variant="muted">{t(language, "library.model.multimodal")}</Badge> : null}
                        {modelVerification.verified || recommendationVerification.verified ? <Badge variant="success" title={t(language, "library.model.hfVerified")}>{t(language, "library.model.sourceVerified")}</Badge> : null}
                      </div>
                      <div className="mt-1 text-sm text-muted-foreground">
                        {mode === "basic"
                          ? [
                            modelMessage(model.family || recommendation.family || t(language, "library.model.local")),
                            /mlx|gguf|ollama/i.test(String(model.size || recommendation.size || "")) ? "" : model.size || recommendation.size,
                          ].filter(Boolean).map(String).join(" · ")
                          : [model.family || recommendation.family || t(language, "library.value.local"), model.size || recommendation.size].filter(Boolean).map(String).join(" · ")}
                      </div>
                      {(model.hardware || recommendation.hardware) ? (
                        <div className="mt-1 text-[11px] text-muted-foreground/80">
                          {String(hardwareNote)}
                        </div>
                      ) : null}
                      <div className="mt-2 flex flex-wrap gap-1 text-[11px] text-muted-foreground">
                        <Badge variant="muted">{downloadRequired
                          ? t(language, "library.model.downloadSize", { size: String(downloadSize || t(language, "library.model.downloadRequired")) })
                          : t(language, "library.model.noDownload")}</Badge>
                        <Badge variant="muted">{downloadRequired ? t(language, "library.model.internetDuringDownload") : t(language, "library.model.runsLocally")}</Badge>
                        {provenanceChain ? <Badge variant="muted" data-testid="model-provenance-chain">{provenanceChain}</Badge> : null}
                      </div>
                      {unsupported ? (
                        <div className="mt-3 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm">
                          <div className="font-medium">{mode === "basic" ? t(language, "library.model.attentionBeforeLoad") : actionLabel}</div>
                          <div className="text-muted-foreground">{modelMessage(compatibility.user_message || unavailableReason, "library.model.unsupportedHere")}</div>
                        </div>
                      ) : fallbackAvailable ? (
                        <div className="mt-3 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm">
                          <div className="font-medium">{mode === "basic" ? t(language, "library.model.compatiblePath") : t(language, "library.model.runtimeFallback")}</div>
                          <div className="text-muted-foreground">{modelMessage(compatibility.user_message || t(language, "library.model.compatibilityFallback"), "library.model.compatibilityFallback")}</div>
                        </div>
                      ) : !loaded && !loadAvailable ? <div className="mt-1 text-xs text-muted-foreground">{unavailableReason}</div> : null}
                      {mode !== "basic" ? (
                        <div className="mt-2 space-y-1 text-xs text-muted-foreground">
                          <div>
                            {runtimeLabel} · {loadId}
                            {model.load_strategy || recommendation.load_strategy ? ` · ${String(model.load_strategy || recommendation.load_strategy)}` : ""}
                            {modelVerification.notes ? ` · ${String(modelVerification.notes).slice(0, 60)}` : ""}
                          </div>
                          {safetyNotes || licenseText ? (
                            <div>{[licenseText ? t(language, "library.model.license", { license: String(licenseText) }) : "", safetyNotes ? String(safetyNotes) : ""].filter(Boolean).join(" · ")}</div>
                          ) : null}
                        </div>
                      ) : null}
                      {unsupported || fallbackAvailable ? <AlternativeModels compatibility={compatibility} /> : null}
                    </div>
                    <div className="flex flex-wrap items-center gap-2 md:justify-end">
                      <Badge variant={loaded ? "success" : loadAvailable ? "muted" : "warning"}>{loaded ? t(language, "library.model.loaded") : badgeLabel}</Badge>
                      {loaded ? (
                        <ActionButton label={t(language, "library.runtime.unload")} action={() => latticeApi.unloadModel(loadId)} invalidate={["models"]} />
                      ) : (
                        <Button
                          variant="outline"
                          disabled={busy || unsupported || !canPrepare || (needsConsentGate && !consent)}
                          title={
                            unsupported ? t(language, "library.model.status.unsupported")
                            : needsConsentGate && !consent ? t(language, "library.btn.needsConsent")
                            : !canPrepare ? unavailableReason
                            : undefined
                          }
                          onClick={() => prepareModel(loadId, engine, consent)}
                        >
                          {activeModel === loadId && busy ? t(language, "library.btn.preparing") : needsConsentGate ? t(language, "library.btn.installLoad") : t(language, "library.btn.validateLoad")}
                        </Button>
                      )}
                    </div>
                  </div>
                );
              })}
              {mode === "basic" && catalog.length > 3 ? (
                <div className="rounded-lg border border-border bg-background/55 p-3 text-sm text-muted-foreground">
                  {t(language, "library.model.shortListHint")}
                </div>
              ) : null}
            </div>
          )}
        </DataPanel>
      </div>
      <div className="space-y-4">
        <DataPanel title={mode === "basic" ? t(language, "library.models.embedding.basic") : t(language, "library.models.embedding.advanced")} result={emb.data}>
          {(data) => (
            <div className="space-y-3">
              <ValuePreview value={embeddingStateLabel((data as Record<string, unknown>).state ?? (data as Record<string, unknown>).grade, language)} />
              {["fallback", "hash"].includes(String((data as Record<string, unknown>).state || (data as Record<string, unknown>).grade || "").toLowerCase()) ? (
                <p className="text-sm text-muted-foreground">{t(language, "library.embedding.fallbackHint")}</p>
              ) : null}
              <div
                className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 rounded-lg border border-border bg-background/55 p-3"
                data-testid="library-embedding-recommendation"
              >
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-semibold">{embeddingRecName}</span>
                    <Badge variant="success">{t(language, "library.embedding.recommend.badge")}</Badge>
                    <Badge variant="muted">{embeddingRecId}</Badge>
                    <Badge variant="muted">{embeddingRecSize}</Badge>
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {t(language, "library.embedding.recommend.desc")}
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground" data-testid="library-embedding-mirror-notice">
                    {t(language, "library.embedding.recommend.mirrorNotice", { repo: embeddingFallbackRepo })}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  {embeddingLoaded ? (
                    <Badge variant="success">{t(language, "library.embedding.recommend.active")}</Badge>
                  ) : (
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={busy}
                      data-testid="library-activate-embeddinggemma-2"
                      onClick={() => void prepareModel(embeddingRecLoadId, embeddingRecEngine, true)}
                    >
                      {activeModel === embeddingRecLoadId && busy
                        ? t(language, "library.btn.preparing")
                        : t(language, "library.embedding.recommend.activate")}
                    </Button>
                  )}
                </div>
              </div>
              {mode === "basic" ? null : <StructuredView value={data} />}
            </div>
          )}
        </DataPanel>
        <DataPanel title={mode === "basic" ? t(language, "library.models.validation") : t(language, "library.models.validationAdvanced")} result={models.data}>
          {(data) => {
            const profiles = asArray<Record<string, unknown>>((data as Record<string, unknown>).compat_profiles);
            return profiles.length ? (
              <EntityList items={profiles.map((profile) => ({
                ...profile,
                name: mode === "basic" ? profile.name || profile.display_name || t(language, "library.model.loadedName") : profile.name || profile.display_name || profile.model_id,
                status: profile.quality_status || profile.load_status || t(language, "library.model.checked"),
              }))} titleKey="name" metaKey="status" limit={6} />
            ) : (
              <EmptyState title={t(language, "library.model.noneChecked")} detail={t(language, "library.model.noneCheckedHint")} />
            );
          }}
        </DataPanel>
      </div>
    </div>
  );
}

function AlternativeModels({ compatibility }: { compatibility: Record<string, unknown> }) {
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  const alternatives = asArray<Record<string, unknown>>(compatibility.alternatives);
  if (!alternatives.length) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-1">
      {alternatives.slice(0, 3).map((item) => (
        <Badge key={String(item.id || item.name)} variant="muted">
          {mode === "basic" && /mlx|gguf|ollama|lm studio/i.test(String(item.name || item.id)) ? t(language, "library.model.compatibleAlternative") : String(item.name || item.id)}
        </Badge>
      ))}
    </div>
  );
}

function ModelRuntimeSummary({
  language = "en",
  mode = "advanced",
  profile,
  recommendation,
  current,
  currentId,
  latestProgress,
  lastResult,
  onReload,
  onUnload,
}: {
  language?: Language;
  mode?: string;
  profile: Record<string, unknown>;
  recommendation: Record<string, unknown>;
  current?: Record<string, unknown>;
  currentId: string;
  latestProgress: Record<string, unknown> | null;
  lastResult: Record<string, unknown> | null;
  onReload: () => void;
  onUnload: () => void;
}) {
  const loadedRaw = String(current?.id || currentId || "");
  const loadedName = String(current?.name || "") || (loadedRaw ? humanizeModelId(loadedRaw) : "");
  const engine = String(current?.engine || current?.recommended_engine || latestProgress?.engine || "local_mlx");
  const cachePath = String(current?.local_path || current?.storage_location || recommendation.cache_path || recommendation.storage_location || "~/.cache/huggingface / ~/.latticeai/models");
  const progressStage = String(latestProgress?.stage || lastResult?.stage || (loadedName ? t(language, "library.value.ready") : t(language, "library.value.idle")));
  const basic = mode === "basic";
  return (
    <div className="space-y-3">
      <StatGrid stats={basic ? [
        { label: t(language, "library.runtime.computer"), value: describeComputer(profile, language) },
        { label: t(language, "library.runtime.loaded"), value: loadedName || t(language, "library.runtime.noneShort") },
      ] : [
        { label: t(language, "library.runtime.computer"), value: describeComputer(profile, language) },
        { label: t(language, "library.runtime.engine"), value: engine },
        { label: t(language, "library.runtime.loaded"), value: loadedName || t(language, "library.runtime.noLoaded") },
        { label: t(language, "library.runtime.state"), value: progressStage },
      ]} />
      <div className="rounded-lg border border-border bg-background/55 p-3 text-sm">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="font-medium">{loadedName ? t(language, "library.models.runtime.available") : t(language, "library.models.runtime.none")}</div>
            {basic ? null : <div className="mt-1 text-muted-foreground">{t(language, "library.runtime.cacheStorage", { path: cachePath })}</div>}
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" size="sm" disabled={!loadedName} onClick={onReload}>{t(language, "library.runtime.reload")}</Button>
            <Button variant="outline" size="sm" disabled={!loadedName} onClick={onUnload}>{t(language, "library.runtime.unload")}</Button>
          </div>
        </div>
        {latestProgress ? (
          <div className="mt-3">
            <div className="flex justify-between text-xs text-muted-foreground">
              <span>{String(latestProgress.message || t(language, "library.preparing"))}</span>
              <span>{Number(latestProgress.percent || 0)}%</span>
            </div>
            <div className="mt-1 h-2 overflow-hidden rounded-full bg-muted">
              <div className="h-full bg-primary" style={{ width: `${Number(latestProgress.percent || 8)}%` }} />
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function ModelRecovery({ error }: { error: Record<string, unknown> }) {
  const language = useAppStore((state) => state.language);
  const guidance = asArray<string>(error.recovery_guidance);
  return (
    <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm">
      <div className="font-medium">{String(error.user_message || t(language, "library.model.setupAttention"))}</div>
      {guidance.length ? (
        <ul className="mt-2 list-inside list-disc text-muted-foreground">
          {guidance.slice(0, 3).map((item) => <li key={item}>{item}</li>)}
        </ul>
      ) : null}
    </div>
  );
}
