import * as React from "react";
// Route-scoped copy: importing the namespace registers it into the shared
// table and keeps it inside this lazy chunk instead of the entry bundle.
import "@/i18n/workspace";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Boxes, CheckCircle2, Cpu, Download, Loader2, PackagePlus, PlayCircle, Plug, ShieldAlert } from "lucide-react";
import { latticeApi } from "@/api/client";
import { ActionButton, DataPanel, EmptyState, EntityList, OperationResult, StatGrid, StructuredView, Tabs, ValuePreview } from "@/components/primitives";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { t, type Language } from "@/i18n";
import { useAppStore } from "@/store/appStore";
import { asArray, humanizeModelId } from "@/lib/utils";
import { navigateHash } from "@/features/brain/navigation";

/** Say what the search engine can do, not which provider is wired in. */
function embeddingStateLabel(state: unknown, language: Language) {
  const value = String(state || "").toLowerCase();
  if (value === "production") return t(language, "library.embedding.state.production");
  if (value === "fallback" || value === "hash" || value === "local") {
    return t(language, "library.embedding.state.fallback");
  }
  return t(language, "library.embedding.state.unknown");
}

/** Name the machine the way its owner would, not the way `uname` does. */
function describeComputer(profile: Record<string, unknown> | undefined, language: Language) {
  const os = String(profile?.os || "").toLowerCase();
  const arch = String(profile?.arch || "").toLowerCase();
  if (!os) return t(language, "library.value.detected");
  if (os === "darwin" && arch.startsWith("arm")) return t(language, "library.runtime.appleSilicon");
  if (os === "darwin") return "Mac";
  if (os === "win32" || os.startsWith("win")) return "Windows";
  if (os === "linux") return "Linux";
  return t(language, "library.runtime.thisComputer");
}

/**
 * English sentences the server never localized. A model *name* is short and
 * may legitimately be Latin script; a multi-word English sentence with no
 * Hangul in it is copy that was never translated.
 */
function isUntranslatedProse(text: string) {
  if (/[ㄱ-힝]/u.test(text)) return false;
  return text.trim().split(/\s+/).length >= 6;
}

// Statuses the server is known to emit; anything else is shown neutrally.
const MODEL_STATUS_KEYS = new Set([
  "loaded", "ready", "download_required", "unavailable", "unsupported",
]);

type LibraryTab = "models" | "skills" | "mcp" | "marketplace";

const tabs: Array<{ id: LibraryTab; labelKey: string }> = [
  { id: "models", labelKey: "library.tab.models" },
  { id: "skills", labelKey: "library.tab.skills" },
  { id: "mcp", labelKey: "library.tab.mcp" },
  { id: "marketplace", labelKey: "library.tab.marketplace" },
];

export function LibraryPage({ initialTab }: { initialTab?: string }) {
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  const [tab, setTab] = React.useState<LibraryTab>((initialTab as LibraryTab) || "models");
  React.useEffect(() => {
    if (tabs.some((item) => item.id === initialTab)) setTab(initialTab as LibraryTab);
  }, [initialTab]);
  const visibleTabs = (mode === "basic" ? tabs.filter((item) => item.id === "models") : tabs)
    .map((item) => ({ id: item.id, label: t(language, item.labelKey) }));
  const selectTab = (next: LibraryTab) => {
    setTab(next);
    navigateHash("/" + next);
  };
  return (
    <div className="product-page library-page space-y-5">
      <header className="page-hero">
        <div className="page-kicker"><Boxes className="h-4 w-4" /> {t(language, "library.kicker")}</div>
        <h1 className="page-title">{t(language, "library.title")}</h1>
        <p className="page-copy">{t(language, "library.body")}</p>
      </header>
      {/* The one question this screen exists to answer, answered before the
          tabs rather than six cards down the catalogue: which model is running
          right now, and how do I change it. */}
      <ActiveModelCard />
      <Tabs tabs={visibleTabs} value={tab} onChange={(id) => selectTab(id as LibraryTab)} />
      {tab === "models" ? <ModelsPanel /> : null}
      {tab === "skills" ? <SkillsPanel /> : null}
      {tab === "mcp" ? <McpPanel /> : null}
      {tab === "marketplace" ? <MarketplacePanel /> : null}
    </div>
  );
}

/**
 * Which model is running, and how to change it — first, and on every tab.
 *
 * This screen used to open on a hero, then a tab strip, then a six-step setup
 * track, and only somewhere below that did it say which model was actually
 * loaded. The question that brings people here ("is my AI working, and can I
 * use a different one?") was answered by a stat cell in the middle of a
 * catalogue. It is the first block on the page now, and the alternatives it
 * offers are only ones a single click can really switch to: already
 * downloaded, no consent prompt, not the one already running.
 */
function ActiveModelCard() {
  const qc = useQueryClient();
  const language = useAppStore((state) => state.language);
  const models = useQuery({ queryKey: ["models"], queryFn: latticeApi.models });
  const [switching, setSwitching] = React.useState<string | null>(null);
  const [switchError, setSwitchError] = React.useState<string | null>(null);

  const payload = (models.data?.data || {}) as Record<string, unknown>;
  const loadedIds = asArray<string>(payload.loaded);
  const currentId = String(payload.current || loadedIds[0] || "");
  const catalog = [
    ...asArray<Record<string, unknown>>(payload.catalog),
    ...asArray<Record<string, unknown>>(payload.recommended),
    ...asArray<Record<string, unknown>>(payload.models),
  ];
  const modelId = (model: Record<string, unknown>) => String(model.id || model.model_id || "");
  const activeEntry = currentId ? catalog.find((model) => modelId(model) === currentId) : undefined;
  // Never the raw registry coordinate: the catalogue name first, a humanised id
  // second. `mlx-community/gemma-4-26b-a4b-it-4bit` is not a model name.
  const activeName = String(activeEntry?.name || "") || (currentId ? humanizeModelId(currentId) : "");
  // Only models a single click can really switch to. "Downloaded" is not the
  // same as "loadable": the registry ships entries that are present on disk but
  // whose runtime cannot load them (`runtime_compatibility.supported === false`),
  // and offering one of those as a one-click switch would be a button that
  // always fails. Consent-gated downloads stay in the catalogue below, where
  // the size and the checkbox are.
  const alternatives = catalog
    .filter((model) => {
      const id = modelId(model);
      if (!id || id === currentId || loadedIds.includes(id)) return false;
      if (model.download_required) return false;
      if (asRecord(model.runtime_compatibility).supported === false) return false;
      if (model.load_status === "unsupported") return false;
      return Boolean(model.load_available) || model.load_status === "ready";
    })
    .slice(0, 3);

  async function switchTo(model: Record<string, unknown>) {
    const id = modelId(model);
    setSwitching(id);
    setSwitchError(null);
    const engine = String(model.recommended_engine || model.engine || "") || undefined;
    const result = await latticeApi.loadModel(String(model.recommended_load_id || id), engine, false);
    setSwitching(null);
    if (!result.ok) setSwitchError(result.error || t(language, "library.active.switchFailed"));
    await qc.invalidateQueries({ queryKey: ["models"] });
  }

  // A failed request must not read as "no model is loaded" — that is a claim
  // about the machine this response cannot support.
  const unavailable = Boolean(models.data && !models.data.ok);

  return (
    <section className="library-active" aria-label={t(language, "library.active.title")} data-testid="library-active-model">
      <div className="library-active-main">
        <span className="library-active-mark" aria-hidden="true">
          {activeName && !unavailable ? <PlayCircle className="h-5 w-5" /> : <Cpu className="h-5 w-5" />}
        </span>
        <div className="library-active-copy">
          <span className="library-active-kicker">{t(language, "library.active.title")}</span>
          {unavailable ? (
            <strong>{t(language, "library.active.unknown")}</strong>
          ) : activeName ? (
            <>
              <strong>{activeName}</strong>
              <small>{t(language, "library.active.ready")}</small>
            </>
          ) : (
            <>
              <strong>{t(language, "library.active.none")}</strong>
              <small>{t(language, "library.active.noneHint")}</small>
            </>
          )}
        </div>
        {activeName && !unavailable ? (
          <Badge variant="success">{t(language, "library.model.loaded")}</Badge>
        ) : null}
      </div>

      {alternatives.length && !unavailable ? (
        <div className="library-active-switch">
          <span className="library-active-switch-label">
            {t(language, "library.active.switchTitle")}
            <small>{t(language, "library.active.switchHint")}</small>
          </span>
          <div className="library-active-switch-options">
            {alternatives.map((model) => {
              const id = modelId(model);
              const name = String(model.name || "") || humanizeModelId(id);
              return (
                <button
                  key={id}
                  type="button"
                  disabled={Boolean(switching)}
                  data-testid={`library-switch-${id}`}
                  onClick={() => void switchTo(model)}
                >
                  {switching === id ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />}
                  {switching === id ? t(language, "library.active.switching") : name}
                </button>
              );
            })}
          </div>
        </div>
      ) : null}

      {switchError ? <p className="library-active-error" role="status">{switchError}</p> : null}
    </section>
  );
}

function ModelsPanel() {
  const qc = useQueryClient();
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  const models = useQuery({ queryKey: ["models"], queryFn: latticeApi.models });
  const recs = useQuery({ queryKey: ["modelRecommendations", "local_mlx"], queryFn: () => latticeApi.modelRecommendations("local_mlx") });
  const emb = useQuery({ queryKey: ["embeddings"], queryFn: latticeApi.embeddingsStatus });
  const [consent, setConsent] = React.useState(false);
  const [activeModel, setActiveModel] = React.useState<string | null>(null);
  const [progress, setProgress] = React.useState<Record<string, unknown>[]>([]);
  const [lastResult, setLastResult] = React.useState<Record<string, unknown> | null>(null);
  const [lastError, setLastError] = React.useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = React.useState(false);
  const loadedIds = asArray<string>((models.data?.data as Record<string, unknown> | undefined)?.loaded);
  const currentId = String((models.data?.data as Record<string, unknown> | undefined)?.current || "");
  // Order by what the person can actually do right now. The catalog arrives in
  // recommendation order, which buried the one downloaded, ready model behind
  // five cards whose only button was disabled — the screen read as "nothing
  // here works". Loaded first, then ready to use, then everything else.
  const catalogRank = (model: Record<string, unknown>) => {
    const id = String(model.id || model.model_id || "");
    if (loadedIds.includes(id) || id === currentId) return 0;
    if (model.load_available || model.load_status === "ready") return 1;
    if (model.pulled) return 2;
    return 3;
  };
  const catalog = [
    ...asArray<Record<string, unknown>>((models.data?.data as Record<string, unknown>)?.catalog),
    ...asArray<Record<string, unknown>>((models.data?.data as Record<string, unknown>)?.recommended),
  ]
    .map((model, index) => ({ model, index }))
    .sort((a, b) => catalogRank(a.model) - catalogRank(b.model) || a.index - b.index)
    .map((entry) => entry.model);
  const recommendationRows = asArray<Record<string, unknown>>(
    ((recs.data?.data as Record<string, unknown>)?.recommendations as Record<string, unknown> | undefined)?.models,
  );
  const recommendationById = new Map(recommendationRows.map((item) => [String(item.id), item]));
  const current = catalog.find((model) => loadedIds.includes(String(model.id)) || String(model.id) === currentId);
  const topPick = (((recs.data?.data as Record<string, unknown> | undefined)?.recommendations as Record<string, unknown> | undefined)?.top_pick || null) as Record<string, unknown> | null;
  const latestProgress = progress[progress.length - 1] || null;

  const modelMessage = React.useCallback((message: unknown, fallbackKey = "library.model.notReady") => {
    const text = String(message || t(language, fallbackKey));
    // The registry ships compatibility prose in English. Substituting terms
    // inside it produced word salad — a released screenshot read "uses the 이
    // 로컬 모델 형식 로컬 모델 지원 format. The installed 로컬 모델 지원 모델
    // 지원 does not include that loader…". A sentence cannot be translated a
    // token at a time, so a Korean reader gets the localized sentence for this
    // situation instead. Short values (a family name like "Gemma 4") are not
    // prose and pass through untouched.
    if (language === "ko" && isUntranslatedProse(text)) return t(language, fallbackKey);
    if (mode !== "basic") return text;
    const localized = text
      .replace(/gemma4_unified/gi, t(language, "library.model.localFormat"))
      .replace(/mlx[-_ ]?vlm|mlx[-_ ]?lm|local_mlx|\bmlx\b|\bgguf\b|\bollama\b|hugging face/gi, t(language, "library.model.localSupport"))
      .replace(/runtime/gi, t(language, "library.model.support"))
      .replace(/model_type/gi, t(language, "library.model.format"));
    return language === "en"
      ? localized
        .replace(/this local model format local model support format/gi, t(language, "library.model.localFormat"))
        .replace(/local model support model support/gi, t(language, "library.model.localSupport"))
      : localized;
  }, [language, mode]);

  async function prepareModel(loadId: string, engine: string, allowDownload: boolean) {
    setBusy(true);
    setActiveModel(loadId);
    setProgress([]);
    setLastResult(null);
    setLastError(null);
    const result = await latticeApi.streamModelPrepare(
      { model: loadId, engine: engine || "local_mlx", allow_download: allowDownload },
      {
        onProgress: (event) => setProgress((items) => [...items.slice(-8), event]),
        onDone: (event) => setLastResult(event),
        onError: (event) => setLastError(event),
      },
    );
    setBusy(false);
    await qc.invalidateQueries({ queryKey: ["models"] });
    await qc.invalidateQueries({ queryKey: ["modelRecommendations", "local_mlx"] });
    if (!result.ok && !lastError) setLastError(result.data as Record<string, unknown>);
  }

  return (
    <div className="grid gap-4 xl:grid-cols-[1.2fr_0.8fr]">
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
                    if (current) void prepareModel(String(current.recommended_load_id || current.id || currentId), String(current.recommended_engine || current.engine || "local_mlx"), consent);
                  }}
                  onUnload={() => {
                    if (currentId) void latticeApi.unloadModel(currentId).then(() => qc.invalidateQueries({ queryKey: ["models"] }));
                  }}
                />
                {/* These six are a progress track, not choices. As bordered cards they
                    looked tappable and invited clicks that do nothing, so they read as a
                    connected sequence now: where setup has got to, at a glance. */}
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
                  <div className="rounded-lg border border-border bg-background/55 p-3 text-sm">
                    <div className="font-medium">{String(latestProgress.message || t(language, "library.preparing"))}</div>
                    <div className="mt-2 h-2 overflow-hidden rounded-full bg-muted">
                      <div className="h-full bg-primary" style={{ width: `${Number(latestProgress.percent || 8)}%` }} />
                    </div>
                    {latestProgress.detail ? <div className="mt-2 text-xs text-muted-foreground">{String(latestProgress.detail)}</div> : null}
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
              // `hardware.notes` is untranslated engineering commentary from the model
              // registry ("Tiny but capable vision; great first local VLM."), and it
              // was winning over the localized sentence built from the RAM figures.
              // Lead with the sentence people can act on; keep the raw note only as a
              // last resort, and never in the simplified view.
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
              // The server's `unavailable_reason` is English prose ("Model files are not
              // present locally…") and `modelMessage` only rewrites tokens, so it reached
              // the screen untranslated. When the reason is simply "not downloaded yet" —
              // already said by the badge and the size chip — use plain localized copy.
              const unavailableReason = downloadRequired
                ? t(language, "library.model.needsDownloadFirst")
                : modelMessage(model.unavailable_reason || t(language, "library.model.notReady"));
              const runtimeLabel = String(model.runtime_label || compatibility.preferred_runtime || engine || "local_mlx");
              const actionLabel = String(compatibility.action || loadStatus.replace(/_/g, " "));
              // `load_status` is a server enum (`download_required`, `unsupported`, …).
              // It used to be printed straight onto the badge, so the screen showed
              // people raw identifiers. Map it to real copy and fall back to a neutral
              // phrase rather than leaking an unknown token.
              const statusLabel = MODEL_STATUS_KEYS.has(loadStatus)
                ? t(language, `library.model.status.${loadStatus}`)
                : t(language, "library.model.status.unknown");
              const badgeLabel = unsupported && mode === "basic"
                ? t(language, "library.model.needsAttention")
                : statusLabel;
              const canPrepare = loadAvailable || downloadRequired;
              const downloadSize = model.download_size || model.estimated_size || recommendation.download_size || recommendation.estimated_size || model.size || recommendation.size;
              const maker = model.provider || recommendation.provider || model.organization || recommendation.organization;
              return (
                <div key={id} className="grid gap-3 rounded-lg border border-border bg-background/55 p-4 md:grid-cols-[1fr_auto]">
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
                      {maker ? <Badge variant="muted">{String(maker)}</Badge> : null}
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
                          {modelVerification.notes ? ` · ${String(modelVerification.notes).slice(0,60)}` : ""}
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
                        disabled={busy || unsupported || !canPrepare || (downloadRequired && !consent)}
                        // A disabled primary action has to say why. The most common
                        // reason here is the download consent box a few rows up, which
                        // left people staring at a greyed button with no explanation.
                        title={
                          unsupported ? t(language, "library.model.status.unsupported")
                          : downloadRequired && !consent ? t(language, "library.btn.needsConsent")
                          : !canPrepare ? unavailableReason
                          : undefined
                        }
                        onClick={() => prepareModel(loadId, engine || "local_mlx", consent)}
                      >
                        {activeModel === loadId && busy ? t(language, "library.btn.preparing") : downloadRequired ? t(language, "library.btn.installLoad") : t(language, "library.btn.validateLoad")}
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
            // `state` is the provider enum ("fallback" | "production"). Advanced
            // mode used to show only the raw table, so the answer to "is search
            // any good right now?" was spelled `Grade: fallback`. Lead with the
            // plain sentence in every mode; the table stays underneath for
            // anyone who wants the detail.
            <div className="space-y-3">
              <ValuePreview value={embeddingStateLabel((data as Record<string, unknown>).state, language)} />
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
  // Fall back through the catalog name, then a humanised id — never the raw
  // registry coordinate. 10.0.0 replaced `mlx-community/gemma-4-26b-a4b-it-4bit`
  // with "Gemma 4 26B A4B Instruct" everywhere the Brain surface shows a model;
  // this panel kept printing the coordinate because it fell straight to `id`.
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

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function SkillsPanel() {
  const qc = useQueryClient();
  const language = useAppStore((state) => state.language);
  const skills = useQuery({ queryKey: ["skills"], queryFn: latticeApi.skills });
  const market = useQuery({ queryKey: ["skillsMarketplace"], queryFn: latticeApi.skillsMarketplace });
  const install = useMutation({
    mutationFn: (skill: Record<string, unknown>) => latticeApi.skillInstall(String(skill.name || skill.id), String(skill.plugin || "")),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["skills"] }),
  });
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <DataPanel title={t(language, "library.skills.installed")} result={skills.data}>
        {(data) => (
          <div className="grid gap-2">
            {asArray<Record<string, unknown>>((data as Record<string, unknown>).skills).map((skill) => (
              <div key={String(skill.name || skill.id)} className="flex items-center justify-between gap-3 rounded-md border border-border p-3">
                <div>
                  <div className="font-medium">{String(skill.name || skill.id)}</div>
                  <div className="text-sm text-muted-foreground">{String(skill.plugin || skill.description || "")}</div>
                </div>
                <ActionButton label={t(language, skill.enabled ? "library.skills.disable" : "library.skills.enable")} action={() => latticeApi.skillToggle(String(skill.name || skill.id), Boolean(skill.enabled))} invalidate={["skills"]} />
              </div>
            ))}
          </div>
        )}
      </DataPanel>
      <DataPanel title={t(language, "library.skills.marketplace")} result={market.data}>
        {(data) => (
          <div className="grid gap-2">
            {asArray<Record<string, unknown>>((data as Record<string, unknown>).skills).slice(0, 10).map((skill) => (
              <div key={String(skill.name || skill.id)} className="flex items-center justify-between gap-3 rounded-md border border-border p-3">
                <div>
                  <div className="font-medium">{String(skill.name || skill.id)}</div>
                  <div className="text-sm text-muted-foreground">{String(skill.description || skill.category || "")}</div>
                </div>
                <Button variant="outline" disabled={install.isPending} onClick={() => install.mutate(skill)}>{t(language, "library.skills.install")}</Button>
              </div>
            ))}
          </div>
        )}
      </DataPanel>
    </div>
  );
}

function McpPanel() {
  const mode = useAppStore((state) => state.mode);
  const language = useAppStore((state) => state.language);
  const [query, setQuery] = React.useState("github");
  const tools = useQuery({ queryKey: ["mcpTools"], queryFn: latticeApi.mcpTools });
  const rec = useMutation({ mutationFn: () => latticeApi.mcpRecommend(query) });
  return (
    <div className="grid gap-4 xl:grid-cols-[1fr_1fr]">
      <DataPanel title={t(language, mode === "basic" ? "library.connector.connections" : "library.connector.mcpTools")} result={tools.data}>
        {(data) => <EntityList items={(data as Record<string, unknown>).tools || (data as Record<string, unknown>).installed_mcps} titleKey="name" metaKey="status" />}
      </DataPanel>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Plug className="h-4 w-4" /> {t(language, "library.connector.recommend")}</CardTitle>
          <CardDescription>{t(language, "library.connector.recommendHint")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex gap-2">
            <Input value={query} onChange={(e) => setQuery(e.target.value)} />
            <Button onClick={() => rec.mutate()} disabled={!query.trim() || rec.isPending}>{t(language, "library.connector.recommendAction")}</Button>
          </div>
          {rec.data ? <OperationResult result={rec.data} successLabel={t(language, "library.connector.recommendDone")} /> : null}
        </CardContent>
      </Card>
    </div>
  );
}

function MarketplacePanel() {
  const language = useAppStore((state) => state.language);
  const templates = useQuery({ queryKey: ["templates"], queryFn: latticeApi.templates });
  const plugins = useQuery({ queryKey: ["plugins"], queryFn: latticeApi.pluginsRegistry });
  const dir = useQuery({ queryKey: ["pluginsDirectory"], queryFn: latticeApi.pluginsDirectory });
  return (
    <div className="grid gap-4 xl:grid-cols-3">
      <DataPanel title={t(language, "library.market.templates")} result={templates.data}>
        {(data) => <EntityList items={(data as Record<string, unknown>).templates} titleKey="name" metaKey="kind" />}
      </DataPanel>
      <DataPanel title={t(language, "library.market.installedPlugins")} result={plugins.data}>
        {(data) => <EntityList items={(data as Record<string, unknown>).plugins} titleKey="name" metaKey="status" />}
      </DataPanel>
      <DataPanel title={t(language, "library.market.pluginDirectory")} result={dir.data}>
        {(data) => <EntityList items={(data as Record<string, unknown>).plugins} titleKey="name" metaKey="category" />}
      </DataPanel>
      <Card className="xl:col-span-3">
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><PackagePlus className="h-4 w-4" /> {t(language, "library.market.templateInstall")}</CardTitle>
          <CardDescription>{t(language, "library.market.templateInstallHint")}</CardDescription>
        </CardHeader>
        <CardContent>
          {asArray<Record<string, unknown>>((templates.data?.data as Record<string, unknown>)?.templates).length ? (
            <div className="flex flex-wrap gap-2">
              {asArray<Record<string, unknown>>((templates.data?.data as Record<string, unknown>)?.templates).slice(0, 6).map((template) => (
                <ActionButton key={String(template.id || template.name)} label={t(language, "library.market.installTemplate", { name: String(template.name || template.id) })} action={() => latticeApi.installTemplate(template)} />
              ))}
            </div>
          ) : <EntityList items={[]} />}
        </CardContent>
      </Card>
    </div>
  );
}
