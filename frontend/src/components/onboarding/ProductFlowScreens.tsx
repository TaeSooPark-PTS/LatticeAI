import * as React from "react";
import { CheckCircle2, Cpu, Download, LockKeyhole, MonitorCog, Sparkles } from "lucide-react";
import { latticeApi } from "@/api/client";
import { LivingBrain } from "@/components/LivingBrain";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn, asArray } from "@/lib/utils";
import { LANGUAGE_LABELS, t, type Language } from "@/i18n";
import { useAppStore } from "@/store/appStore";

const FLOW_USER_KEY = "lattice.productFlow.user";

export type FlowStep = "login" | "analysis" | "recommend" | "install";
type ApiData = Record<string, unknown>;

export type FlowAnalysis = {
  setup?: ApiData | null;
  models?: ApiData | null;
  recommendations?: ApiData | null;
  sysinfo?: ApiData | null;
};

export type RecommendedModel = {
  id: string;
  loadId: string;
  engine: string;
  name: string;
  shortName: string;
  family: string;
  size: string;
  role: "best" | "faster" | "advanced";
  reason: string;
  supported: boolean;
  downloadRequired: boolean;
  downloadSize: string;
  storageLocation: string;
  externalHost: string;
};

type InstallStage = "idle" | "install" | "download" | "validate" | "load" | "done" | "error";

function readSavedFlowUser(): { email?: string; name?: string } | null {
  try {
    const raw = localStorage.getItem(FLOW_USER_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {}
  return null;
}

export function LanguageChooser() {
  const language = useAppStore((state) => state.language);
  const setLanguage = useAppStore((state) => state.setLanguage);

  return (
    <div className="language-switcher ritual-language" aria-label={t(language, "language.label")}>
      {(["ko", "en"] as Language[]).map((item) => (
        <button
          key={item}
          type="button"
          className={language === item ? "is-active" : ""}
          onClick={() => setLanguage(item)}
          aria-pressed={language === item}
        >
          {LANGUAGE_LABELS[item]}
        </button>
      ))}
    </div>
  );
}

export function LoginScreen({ onSuccess }: { onSuccess: () => void }) {
  const language = useAppStore((state) => state.language);
  const [email, setEmail] = React.useState(() => {
    return readSavedFlowUser()?.email || "you@local";
  });
  const [password, setPassword] = React.useState("");
  const [name, setName] = React.useState(() => readSavedFlowUser()?.name || "You");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const cleanEmail = email.trim();
    const cleanPassword = password.trim();
    const cleanName = name.trim() || cleanEmail.split("@")[0] || "You";
    if (!cleanEmail || !cleanPassword) {
      setError(t(language, "flow.login.missing"));
      return;
    }
    setBusy(true);
    setError(null);
    const savedUser = readSavedFlowUser();
    let result = await latticeApi.login(cleanEmail, cleanPassword);
    if (!result.ok) {
      const profile = await latticeApi.profile();
      if (profile.ok && (!savedUser?.email || savedUser.email === cleanEmail)) {
        try { localStorage.setItem(FLOW_USER_KEY, JSON.stringify({ email: cleanEmail, name: cleanName })); } catch {}
        setBusy(false);
        onSuccess();
        return;
      }
      if (savedUser?.email && savedUser.email !== cleanEmail) {
        setBusy(false);
        setError(t(language, "flow.login.otherEmail"));
        return;
      }
      if (savedUser?.email === cleanEmail) {
        setBusy(false);
        setError(t(language, "flow.login.wrongPassword"));
        return;
      }
      const registered = await latticeApi.register({
        email: cleanEmail,
        password: cleanPassword,
        name: cleanName,
        nickname: cleanName,
      });
      if (registered.ok) result = await latticeApi.login(cleanEmail, cleanPassword);
    }
    if (!result.ok) {
      setBusy(false);
      setError(t(language, "flow.login.unavailable"));
      return;
    }
    try { localStorage.setItem(FLOW_USER_KEY, JSON.stringify({ email: cleanEmail, name: cleanName })); } catch {}
    setBusy(false);
    onSuccess();
  }

  return (
    <div>
      <div className="ritual-title">{t(language, "flow.login.title")}</div>
      <div className="ritual-subtitle">{t(language, "flow.login.body")}</div>

      <ProductPromise />

      <form onSubmit={submit} className="ritual-card ritual-form">
        <div className="ritual-field-stack">
          <div>
            <div className="ritual-field-label">{t(language, "flow.name")}</div>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="You" />
          </div>
          <div>
            <div className="ritual-field-label">{t(language, "flow.email")}</div>
            <Input value={email} onChange={(e) => setEmail(e.target.value)} type="email" placeholder="you@local" />
          </div>
          <div>
            <div className="ritual-field-label">{t(language, "flow.password")}</div>
            <Input value={password} onChange={(e) => setPassword(e.target.value)} type="password" placeholder={t(language, "flow.password.placeholder")} />
          </div>
        </div>

        {error && <div className="ritual-error" role="alert">{error}</div>}

        <Button type="submit" disabled={busy || !email.trim() || !password.trim()} className="ritual-full-button">
          {busy ? t(language, "flow.login.busy") : t(language, "flow.login.submit")}
        </Button>
        <div className="ritual-note">
          {t(language, "flow.login.note")}
        </div>
      </form>
    </div>
  );
}

function ProductPromise() {
  const language = useAppStore((state) => state.language);
  return (
    <div className="ritual-promise" aria-label="Lattice AI product promise">
      <div>
        <span>{t(language, "flow.promise.memory.k")}</span>
        <strong>{t(language, "flow.promise.memory.v")}</strong>
      </div>
      <div>
        <span>{t(language, "flow.promise.model.k")}</span>
        <strong>{t(language, "flow.promise.model.v")}</strong>
      </div>
      <div>
        <span>{t(language, "flow.promise.ownership.k")}</span>
        <strong>{t(language, "flow.promise.ownership.v")}</strong>
      </div>
    </div>
  );
}

export function AnalysisScreen({
  analysis,
  error,
  onContinue,
}: {
  analysis: FlowAnalysis | null;
  error: string | null;
  onContinue: () => void;
}) {
  const language = useAppStore((state) => state.language);
  const detected = buildDetectedFacts(analysis, language);
  return (
    <div>
      <div className="ritual-title">{t(language, "flow.analysis.title")}</div>
      <div className="ritual-subtitle">{t(language, "flow.analysis.body")}</div>

      <div className="ritual-fact-grid">
        {detected.map((item, idx) => (
          <div key={idx} className="ritual-fact">
            <div className="ritual-fact-label">{item.label}</div>
            <div className="ritual-fact-value">{item.value}</div>
            <div className="ritual-fact-detail">{item.detail}</div>
          </div>
        ))}
      </div>

      <div className="ritual-card ritual-analysis-card">
        <div className="ritual-inline-row">
          <Sparkles className="ritual-core-icon" />
          <div>
            <div className="ritual-strong-text">{analysis ? recommendedSummary(analysis, language) : t(language, "flow.analysis.finding")}</div>
            <div className="ritual-muted-text">
              {analysis ? t(language, "flow.analysis.ready") : t(language, "flow.analysis.wait")}
            </div>
          </div>
        </div>
      </div>

      {error && <div className="ritual-card ritual-error-card" role="alert">{error}</div>}

      <div className="ritual-centered-actions">
        <Button onClick={onContinue} disabled={!analysis && !error} className="ritual-wide-button">
          {t(language, "flow.analysis.continue")}
        </Button>
      </div>
    </div>
  );
}

export function RecommendationScreen({
  recommendations,
  onBack,
  onSkipModel,
  onSelect,
}: {
  recommendations: RecommendedModel[];
  onBack: () => void;
  onSkipModel: () => void;
  onSelect: (model: RecommendedModel) => void;
}) {
  const language = useAppStore((state) => state.language);
  const items = recommendations.length ? recommendations : [fallbackModel()];
  return (
    <div>
      <div className="ritual-title">{t(language, "flow.recommend.title")}</div>
      <div className="ritual-subtitle">{t(language, "flow.recommend.body")}</div>

      <div className="ritual-model-list">
        {items[0]?.supported ? (
          <Button onClick={() => onSelect(items[0])} className="ritual-primary-model-button">
            {t(language, "flow.recommend.primary")}
          </Button>
        ) : null}
        {items.slice(0, 3).map((model, index) => (
          <button
            key={`${model.role}-${model.id}`}
            className="ritual-model-card"
            onClick={() => model.supported && onSelect(model)}
            disabled={!model.supported}
          >
            <div className="role">{rankLabel(model.role, index, language)}</div>
            <div className="name">{model.shortName}</div>
            <div className="reason">{model.reason} · {model.size || t(language, "flow.recommend.sizeReady")}</div>
            {!model.supported && <div className="ritual-model-warning">{t(language, "flow.recommend.unsupported")}</div>}
          </button>
        ))}
      </div>

      <div className="ritual-action-row">
        <Button variant="ghost" onClick={onBack}>{t(language, "flow.recommend.back")}</Button>
        <Button variant="outline" onClick={onSkipModel}>{t(language, "flow.recommend.skip")}</Button>
        <div className="ritual-muted-hint">{t(language, "flow.recommend.hint")}</div>
      </div>
    </div>
  );
}

export function InstallScreen({
  model,
  onBack,
  onComplete,
  onLater,
}: {
  model: RecommendedModel;
  onBack: () => void;
  onComplete: () => void;
  onLater: () => void;
}) {
  const language = useAppStore((state) => state.language);
  const [busy, setBusy] = React.useState(false);
  const [stage, setStage] = React.useState<InstallStage>("idle");
  const [percent, setPercent] = React.useState(0);
  const [message, setMessage] = React.useState(t(language, "flow.install.wait"));
  const [error, setError] = React.useState<string | null>(null);

  async function start() {
    setBusy(true);
    setError(null);
    setStage("install");
    setPercent(8);
    setMessage(t(language, "flow.install.prepare"));
    const result = await latticeApi.streamModelPrepare(
      { model: model.loadId, engine: model.engine || "local_mlx", allow_download: true },
      {
        onProgress: (event) => {
          const nextStage = friendlyInstallStage(String(event.stage || ""));
          setStage(nextStage);
          setPercent(Number(event.percent || percentForStage(nextStage)));
          setMessage(friendlyInstallMessage(event, nextStage, language));
        },
        onDone: () => {
          setStage("done");
          setPercent(100);
          setMessage(t(language, "flow.install.done"));
        },
        onError: (event) => {
          setStage("error");
          setError(consumerError(event));
        },
      },
    );
    setBusy(false);
    if (result.ok) {
      setStage("done");
      setPercent(100);
      setMessage(t(language, "flow.install.done"));
      window.setTimeout(onComplete, 700);
    } else {
      setStage("error");
      setError(consumerError(result.data as ApiData));
    }
  }

  const brainStateForStage: any = 
    stage === "download" ? "thinking" :
    stage === "validate" ? "recalling" :
    stage === "load" ? "synthesizing" :
    stage === "done" ? "idle" : "listening";

  return (
    <div>
      <div className="ritual-title">{t(language, "flow.install.title")}</div>
      <div className="ritual-subtitle">
        <strong>{model.shortName}</strong> — {model.reason}.<br />
        {t(language, "flow.install.body")}
      </div>

      {/* Living Brain reacts to the ceremony of installation */}
      <div className="ritual-install-brain">
        <LivingBrain 
          state={brainStateForStage} 
          intensity={stage === "download" || stage === "load" ? 0.96 : 0.82} 
          size="normal" 
        />
      </div>

      <DownloadConsentPanel model={model} />

      <div className="ritual-progress">
        <div className="ritual-stage-list">
          {(["install", "download", "validate", "load"] as const).map((item) => (
            <div key={item} className={`ritual-stage ${installStepState(stage, item)}`}>
              <CheckCircle2 className="ritual-stage-icon" />
              <span>{installLabel(item, language)}</span>
            </div>
          ))}
        </div>

        <div className="ritual-bar">
          <span className={`ritual-bar-fill ${progressClass(percent)}`} />
        </div>
      </div>

      <div className="ritual-status">{message}</div>
      <div className="ritual-card ritual-status-card">
        {t(language, "flow.install.note")}
      </div>

      {error && (
        <div className="ritual-card ritual-error-card ritual-install-error" role="alert">
          {error}
          <div className="ritual-error-detail">{t(language, "flow.install.retry")}</div>
        </div>
      )}

      <div className="ritual-button-row">
        <Button variant="ghost" onClick={onBack} disabled={busy}>{t(language, "flow.install.back")}</Button>
        <Button variant="outline" onClick={onLater} disabled={busy}>{t(language, "flow.install.later")}</Button>

        {stage !== "done" ? (
          <Button 
            onClick={start} 
            disabled={busy || !model.supported}
          >
            {busy ? t(language, "flow.install.busy") : t(language, "flow.install.start")}
          </Button>
        ) : (
          <Button onClick={onComplete}>{t(language, "flow.install.enter")}</Button>
        )}
      </div>

      <div className="ritual-local-note">
        {t(language, "flow.install.local")}
      </div>
    </div>
  );
}

function DownloadConsentPanel({ model }: { model: RecommendedModel }) {
  const language = useAppStore((state) => state.language);
  const items = [
    { label: t(language, "flow.consent.size"), value: model.downloadSize || t(language, "flow.consent.sizeUnknown") },
    { label: t(language, "flow.consent.location"), value: model.storageLocation },
    { label: t(language, "flow.consent.external"), value: model.externalHost || t(language, "flow.consent.externalNone") },
  ];

  return (
    <section className="ritual-consent-panel" aria-label={t(language, "flow.consent.title")}>
      <div>
        <strong>{t(language, "flow.consent.title")}</strong>
        <p>{model.downloadRequired ? t(language, "flow.consent.body") : t(language, "flow.consent.ready")}</p>
      </div>
      <dl>
        {items.map((item) => (
          <div key={item.label}>
            <dt>{item.label}</dt>
            <dd>{item.value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function buildDetectedFacts(analysis: FlowAnalysis | null, language: Language) {
  if (!analysis) {
    return [
      { label: t(language, "flow.analysis.fact.computer"), value: t(language, "flow.analysis.checking"), detail: t(language, "flow.analysis.fact.computerDetail") },
      { label: t(language, "flow.analysis.fact.memory"), value: t(language, "flow.analysis.checking"), detail: t(language, "flow.analysis.fact.memoryDetail") },
      { label: t(language, "flow.analysis.fact.graphics"), value: t(language, "flow.analysis.checking"), detail: t(language, "flow.analysis.fact.graphicsDetail") },
      { label: t(language, "flow.analysis.fact.support"), value: t(language, "flow.analysis.checking"), detail: t(language, "flow.analysis.fact.supportDetail") },
      { label: t(language, "flow.analysis.fact.models"), value: t(language, "flow.analysis.checking"), detail: t(language, "flow.analysis.fact.modelsDetail") },
    ];
  }
  const setupEnv = asRecord(analysis.setup?.environment);
  const recProfile = asRecord(analysis.recommendations?.profile);
  const recs = asRecord(analysis.recommendations?.recommendations);
  const models = asRecord(analysis.models);
  const sysinfo = asRecord(analysis.sysinfo);
  const profile = { ...setupEnv, ...recProfile };
  const ramGb = Number(recs.ram_gb || Number(profile.ram_mb || 0) / 1024 || 0);
  const appleSilicon = Boolean(recs.apple_silicon || String(profile.arch || "").includes("arm"));
  const loadedModels = asArray(models.loaded);
  const gpu = asRecord(profile.gpu);
  const installedRuntimes = [
    ...asArray(setupEnv.installed_runtimes),
    ...asArray(profile.installed_runtimes),
    ...asArray(recs.installed_runtimes),
  ];
  return [
    {
      label: t(language, "flow.analysis.fact.computer"),
      value: appleSilicon ? t(language, "flow.analysis.apple") : friendlyOs(profile.os, language),
      detail: t(language, "flow.analysis.readyDetail"),
    },
    {
      label: t(language, "flow.analysis.fact.memory"),
      value: ramGb ? `${Math.round(ramGb)} GB` : t(language, "flow.analysis.detected"),
      detail: t(language, "flow.analysis.memoryReadyDetail"),
    },
    {
      label: t(language, "flow.analysis.fact.graphics"),
      value: gpu.vendor || sysinfo.gpu_mem_gb ? t(language, "flow.analysis.localReady") : t(language, "flow.analysis.standardLocal"),
      detail: t(language, "flow.analysis.graphicsReadyDetail"),
    },
    {
      label: t(language, "flow.analysis.fact.support"),
      value: installedRuntimes.length ? t(language, "flow.analysis.supportReady") : t(language, "flow.analysis.supportInstall"),
      detail: installedRuntimes.length ? t(language, "flow.analysis.supportReadyDetail") : t(language, "flow.analysis.supportInstallDetail"),
    },
    {
      label: t(language, "flow.analysis.fact.models"),
      value: loadedModels.length ? t(language, "flow.analysis.modelsInstalled", { count: loadedModels.length }) : t(language, "flow.analysis.noModels"),
      detail: loadedModels.length ? t(language, "flow.analysis.modelsReadyDetail") : t(language, "flow.analysis.modelsInstallDetail"),
    },
  ];
}

export function buildRecommendations(analysis: FlowAnalysis | null): RecommendedModel[] {
  const models = asRecord(analysis?.models);
  const modelRows = [
    ...asArray<ApiData>(models.recommended),
    ...asArray<ApiData>(models.catalog),
  ];
  const recommendationRoot = asRecord(analysis?.recommendations?.recommendations);
  const recRows = asArray<ApiData>(recommendationRoot.models);
  const topPick = asRecord(recommendationRoot.top_pick);
  const merged = new Map<string, ApiData>();
  for (const row of [...recRows, ...modelRows]) {
    const id = String(row.id || row.model_id || row.recommended_load_id || "");
    if (!id) continue;
    merged.set(id, { ...(merged.get(id) || {}), ...row });
  }
  if (topPick.id && !merged.has(String(topPick.id))) merged.set(String(topPick.id), topPick);
  const all = Array.from(merged.values()).map(toRecommendedModel).filter((item) => item.id);
  const supported = all.filter((item) => item.supported);
  const pool = supported.length ? supported : all;
  const byName = (pattern: RegExp) => pool.find((item) => pattern.test(`${item.name} ${item.id}`));
  const byId = (id?: unknown) => pool.find((item) => item.id === String(id));
  const best = byId(topPick.id) || byName(/gemma.*12|12b/i) || pool[0];
  const faster = pool.find((item) => item.id !== best?.id && /qwen|8b|7b/i.test(`${item.name} ${item.id}`)) || pool.find((item) => item.id !== best?.id);
  const advanced = pool.find((item) => item.id !== best?.id && item.id !== faster?.id && /26b|32b|70b|advanced/i.test(`${item.name} ${item.id}`))
    || pool.find((item) => item.id !== best?.id && item.id !== faster?.id);
  return [
    best ? { ...best, role: "best" as const, reason: best.reason || "Best Experience" } : null,
    faster ? { ...faster, role: "faster" as const, reason: faster.reason || "Faster" } : null,
    advanced ? { ...advanced, role: "advanced" as const, reason: advanced.reason || "Advanced" } : null,
  ].filter(Boolean) as RecommendedModel[];
}

function toRecommendedModel(row: ApiData): RecommendedModel {
  const compatibility = asRecord(row.runtime_compatibility);
  const id = String(row.id || row.model_id || row.recommended_load_id || "");
  const loadId = String(row.recommended_load_id || row.load_id || id);
  const name = String(row.display_name || row.name || id || "Recommended Brain");
  const supported = row.load_status !== "unsupported"
    && row.load_status !== "runtime_update_needed"
    && row.status !== "not_recommended"
    && compatibility.supported !== false;
  return {
    id,
    loadId,
    engine: String(row.recommended_engine || row.engine || "local_mlx"),
    name,
    shortName: friendlyModelName(name || id),
    family: friendlyModelName(String(row.family || name || "Local Brain")),
    size: String(row.size || ""),
    role: "best",
    reason: String(row.reason || ""),
    supported,
    downloadRequired: Boolean(row.download_required),
    downloadSize: String(row.download_size || row.size || ""),
    storageLocation: String(row.storage_location || row.local_path || "~/.latticeai/models"),
    externalHost: externalHostLabel(row),
  };
}

export function fallbackModel(): RecommendedModel {
  return {
    id: "mlx-community/Qwen3-VL-8B-Instruct-4bit",
    loadId: "mlx-community/Qwen3-VL-8B-Instruct-4bit",
    engine: "local_mlx",
    name: "Qwen3-VL 8B",
    shortName: "Qwen 3",
    family: "Qwen 3",
    size: "",
    role: "best",
    reason: "Best Experience",
    supported: true,
    downloadRequired: false,
    downloadSize: "",
    storageLocation: "~/.latticeai/models",
    externalHost: "",
  };
}

function externalHostLabel(row: ApiData) {
  const raw = String(row.source_url || row.download_url || row.repository || row.provider || row.id || "");
  if (!raw) return "";
  if (/huggingface|hf\\.co|mlx-community/i.test(raw)) return "Hugging Face / model repository";
  if (/ollama/i.test(raw)) return "Ollama registry";
  return raw.replace(/^https?:\/\//, "").split("/")[0] || raw;
}

function rankLabel(role: RecommendedModel["role"], index: number, language: Language) {
  if (role === "best") return language === "ko" ? "추천" : "Best Experience";
  if (role === "faster") return language === "ko" ? "빠른 선택" : "Faster";
  if (role === "advanced") return language === "ko" ? "고급 선택" : "Advanced";
  return `Choice ${index + 1}`;
}

function recommendedSummary(analysis: FlowAnalysis, language: Language) {
  const recs = asRecord(analysis.recommendations?.recommendations);
  const topPick = asRecord(recs.top_pick);
  if (topPick.name || topPick.id) {
    const model = friendlyModelName(String(topPick.name || topPick.id));
    return language === "ko" ? `${model}이 이 컴퓨터에 가장 잘 맞습니다.` : `${model} looks like the best fit.`;
  }
  return language === "ko" ? "이 컴퓨터에는 개인 로컬 Brain을 추천합니다." : "A private local Brain is recommended for this computer.";
}

function friendlyModelName(value: string) {
  return String(value || "Recommended Brain")
    .replace(/^mlx-community\//i, "")
    .replace(/[-_]?Instruct/gi, "")
    .replace(/[-_]?4bit/gi, "")
    .replace(/Qwen3[-_ ]?VL/gi, "Qwen 3")
    .replace(/Qwen3/gi, "Qwen 3")
    .replace(/Gemma[-_ ]?4/gi, "Gemma 4")
    .replace(/A4B/gi, "")
    .replace(/[-_]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function friendlyOs(value: unknown, language: Language) {
  const text = String(value || "Computer");
  if (/darwin|mac/i.test(text)) return "Mac";
  if (/win/i.test(text)) return "Windows PC";
  if (/linux/i.test(text)) return "Linux computer";
  return t(language, "flow.analysis.fact.computer");
}

function friendlyInstallStage(stage: string): InstallStage {
  if (/download|pull|weights/i.test(stage)) return "download";
  if (/smoke|validate|verify|test/i.test(stage)) return "validate";
  if (/load|ready/i.test(stage)) return "load";
  if (/done|complete/i.test(stage)) return "done";
  return "install";
}

function percentForStage(stage: InstallStage) {
  if (stage === "install") return 20;
  if (stage === "download") return 55;
  if (stage === "validate") return 82;
  if (stage === "load") return 94;
  if (stage === "done") return 100;
  return 8;
}

function friendlyInstallMessage(event: ApiData, stage: InstallStage, language: Language) {
  const fallback = {
    install: t(language, "flow.install.prepare"),
    download: t(language, "flow.install.stage.download"),
    validate: t(language, "flow.install.stage.validate"),
    load: t(language, "flow.install.stage.load"),
    done: t(language, "flow.install.done"),
    idle: t(language, "flow.install.wait"),
    error: t(language, "flow.install.stage.error"),
  }[stage];
  return cleanConsumerText(String(event.user_message || event.message || fallback));
}

function installLabel(stage: "install" | "download" | "validate" | "load", language: Language) {
  return t(language, `flow.install.step.${stage}`);
}

function progressClass(percent: number) {
  const step = Math.max(0, Math.min(100, Math.round(percent / 10) * 10));
  return `progress-${step}`;
}

function installStepState(current: InstallStage, item: "install" | "download" | "validate" | "load") {
  const order: InstallStage[] = ["idle", "install", "download", "validate", "load", "done"];
  const currentIndex = order.indexOf(current);
  const itemIndex = order.indexOf(item);
  if (current === "error") return "is-error";
  if (current === "done" || currentIndex > itemIndex) return "is-done";
  if (current === item) return "is-active";
  return "";
}

function consumerError(data: ApiData | unknown) {
  const record = asRecord(data);
  const guidance = asArray<string>(record.recovery_guidance).map(cleanConsumerText).filter(Boolean);
  const message = cleanConsumerText(String(record.user_message || record.reason || record.error || "The selected model could not be loaded."));
  return [message, ...guidance.slice(0, 2)].join(" ");
}

function cleanConsumerText(value: string) {
  return String(value || "")
    .replace(/gemma4_unified/gi, "this model format")
    .replace(/mlx[-_ ]?vlm|mlx[-_ ]?lm|local_mlx|\bmlx\b|\bgguf\b|\bollama\b|huggingface|hugging face/gi, "local model support")
    .replace(/runtime/gi, "model support")
    .replace(/No module named ['\"][^'\"]+['\"]/gi, "A local support component is missing")
    .replace(/\s+/g, " ")
    .trim();
}

function asRecord(value: unknown): ApiData {
  return value && typeof value === "object" && !Array.isArray(value) ? value as ApiData : {};
}
