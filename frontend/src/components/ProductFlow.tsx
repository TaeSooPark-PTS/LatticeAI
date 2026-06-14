import * as React from "react";
import { CheckCircle2, ChevronRight, Cpu, Download, LockKeyhole, MonitorCog, Sparkles } from "lucide-react";
import { latticeApi } from "@/api/client";
import { LivingBrain } from "@/components/LivingBrain";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn, asArray } from "@/lib/utils";
import { LANGUAGE_LABELS, t, type Language } from "@/i18n";
import { useAppStore } from "@/store/appStore";

const FLOW_COMPLETE_KEY = "lattice.productFlow.complete";
const FLOW_USER_KEY = "lattice.productFlow.user";

type FlowStep = "login" | "analysis" | "recommend" | "install";
type ApiData = Record<string, unknown>;

type FlowAnalysis = {
  setup?: ApiData | null;
  models?: ApiData | null;
  recommendations?: ApiData | null;
  sysinfo?: ApiData | null;
};

type RecommendedModel = {
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
};

type InstallStage = "idle" | "install" | "download" | "validate" | "load" | "done" | "error";

export function readProductFlowComplete() {
  try {
    return localStorage.getItem(FLOW_COMPLETE_KEY) === "true";
  } catch {}
  return false;
}

function readSavedFlowUser(): { email?: string; name?: string } | null {
  try {
    const raw = localStorage.getItem(FLOW_USER_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {}
  return null;
}

export function ProductFlow({ onComplete }: { onComplete: () => void }) {
  const language = useAppStore((state) => state.language);
  const [step, setStep] = React.useState<FlowStep>("login");
  const [analysis, setAnalysis] = React.useState<FlowAnalysis | null>(null);
  const [analysisError, setAnalysisError] = React.useState<string | null>(null);
  const [selected, setSelected] = React.useState<RecommendedModel | null>(null);

  const recommendations = React.useMemo(() => buildRecommendations(analysis), [analysis]);

  React.useEffect(() => {
    if (step !== "analysis" || analysis) return;
    let cancelled = false;
    async function runAnalysis() {
      setAnalysisError(null);
      const [setup, recommendationsResult, models, sysinfo] = await Promise.all([
        latticeApi.setupScan(),
        latticeApi.modelRecommendations("local_mlx"),
        latticeApi.models(),
        latticeApi.sysinfo(),
      ]);
      if (cancelled) return;
      setAnalysis({
        setup: setup.ok ? setup.data as ApiData : null,
        recommendations: recommendationsResult.ok ? recommendationsResult.data as ApiData : null,
        models: models.ok ? models.data as ApiData : null,
        sysinfo: sysinfo.ok ? sysinfo.data as ApiData : null,
      });
      if (!setup.ok && !recommendationsResult.ok && !models.ok) {
        setAnalysisError(t(language, "flow.analysis.error"));
      }
    }
    void runAnalysis();
    return () => { cancelled = true; };
  }, [analysis, language, step]);

  return (
    <div className="ritual-shell" aria-label={t(language, "flow.shell")}>
      <div className="ritual-container">
        <LanguageChooser />
        {/* The living presence participates in the ritual at every step */}
        <div className="ritual-brain">
          <LivingBrain
            state={
              step === "login" ? "idle" :
              step === "analysis" ? "listening" :
              step === "recommend" ? "recalling" :
              "thinking"
            }
            intensity={step === "install" ? 0.92 : 0.7}
            size="large"
            showLabel={false}
          />
        </div>

        {step === "login" && (
          <LoginScreen onSuccess={() => setStep("analysis")} />
        )}

        {step === "analysis" && (
          <AnalysisScreen
            analysis={analysis}
            error={analysisError}
            onContinue={() => setStep("recommend")}
          />
        )}

        {step === "recommend" && (
          <RecommendationScreen
            recommendations={recommendations}
            onBack={() => setStep("analysis")}
            onSelect={(model) => {
              setSelected(model);
              setStep("install");
            }}
          />
        )}

        {step === "install" && (
          <InstallScreen
            model={selected || recommendations[0] || fallbackModel()}
            onBack={() => setStep("recommend")}
            onComplete={() => {
              try { localStorage.setItem(FLOW_COMPLETE_KEY, "true"); } catch {}
              onComplete();
            }}
          />
        )}
      </div>
    </div>
  );
}

function LanguageChooser() {
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

function LoginScreen({ onSuccess }: { onSuccess: () => void }) {
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

      <form onSubmit={submit} className="ritual-card" style={{ maxWidth: 420, margin: "0 auto" }}>
        <div style={{ display: "grid", gap: "0.85rem" }}>
          <div>
            <div style={{ fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: "1px", color: "hsl(var(--fg-muted))", marginBottom: 4 }}>{t(language, "flow.name")}</div>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="You" />
          </div>
          <div>
            <div style={{ fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: "1px", color: "hsl(var(--fg-muted))", marginBottom: 4 }}>{t(language, "flow.email")}</div>
            <Input value={email} onChange={(e) => setEmail(e.target.value)} type="email" placeholder="you@local" />
          </div>
          <div>
            <div style={{ fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: "1px", color: "hsl(var(--fg-muted))", marginBottom: 4 }}>{t(language, "flow.password")}</div>
            <Input value={password} onChange={(e) => setPassword(e.target.value)} type="password" placeholder={t(language, "flow.password.placeholder")} />
          </div>
        </div>

        {error && <div style={{ marginTop: "0.85rem", padding: "0.6rem 0.85rem", background: "hsl(var(--destructive)/0.12)", border: "1px solid hsl(var(--destructive)/0.4)", borderRadius: 10, fontSize: "0.9rem" }}>{error}</div>}

        <Button type="submit" disabled={busy || !email.trim() || !password.trim()} style={{ width: "100%", marginTop: "1rem" }}>
          {busy ? t(language, "flow.login.busy") : t(language, "flow.login.submit")}
        </Button>
        <div style={{ fontSize: "0.75rem", color: "hsl(var(--fg-muted))", marginTop: "0.6rem" }}>
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

function AnalysisScreen({
  analysis,
  error,
  onContinue,
}: {
  analysis: FlowAnalysis | null;
  error: string | null;
  onContinue: () => void;
}) {
  const language = useAppStore((state) => state.language);
  const detected = buildDetectedFacts(analysis);
  return (
    <div>
      <div className="ritual-title">{t(language, "flow.analysis.title")}</div>
      <div className="ritual-subtitle">{t(language, "flow.analysis.body")}</div>

      <div className="ritual-fact-grid">
        {detected.map((item, idx) => (
          <div key={idx} className="ritual-fact">
            <div className="ritual-fact-label">{item.label}</div>
            <div className="ritual-fact-value">{item.value}</div>
            <div style={{ fontSize: "0.8rem", color: "hsl(var(--fg-muted))", marginTop: 3 }}>{item.detail}</div>
          </div>
        ))}
      </div>

      <div className="ritual-card" style={{ marginTop: "1rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.6rem" }}>
          <Sparkles style={{ color: "hsl(var(--brain-core))" }} />
          <div>
            <div style={{ fontWeight: 620 }}>{analysis ? recommendedSummary(analysis, language) : t(language, "flow.analysis.finding")}</div>
            <div style={{ fontSize: "0.9rem", color: "hsl(var(--fg-muted))" }}>
              {analysis ? t(language, "flow.analysis.ready") : t(language, "flow.analysis.wait")}
            </div>
          </div>
        </div>
      </div>

      {error && <div className="ritual-card" style={{ borderColor: "hsl(var(--destructive)/0.4)", background: "hsl(var(--destructive)/0.06)" }}>{error}</div>}

      <div style={{ marginTop: "1.25rem" }}>
        <Button onClick={onContinue} disabled={!analysis && !error} style={{ minWidth: 260 }}>
          {t(language, "flow.analysis.continue")}
        </Button>
      </div>
    </div>
  );
}

function RecommendationScreen({
  recommendations,
  onBack,
  onSelect,
}: {
  recommendations: RecommendedModel[];
  onBack: () => void;
  onSelect: (model: RecommendedModel) => void;
}) {
  const language = useAppStore((state) => state.language);
  const items = recommendations.length ? recommendations : [fallbackModel()];
  return (
    <div>
      <div className="ritual-title">{t(language, "flow.recommend.title")}</div>
      <div className="ritual-subtitle">{t(language, "flow.recommend.body")}</div>

      <div style={{ maxWidth: 560, margin: "0 auto" }}>
        {items[0]?.supported ? (
          <Button onClick={() => onSelect(items[0])} style={{ width: "100%", marginBottom: "0.85rem" }}>
            {t(language, "flow.recommend.primary")}
          </Button>
        ) : null}
        {items.slice(0, 3).map((model, index) => (
          <button
            key={`${model.role}-${model.id}`}
            className="ritual-model-card"
            onClick={() => model.supported && onSelect(model)}
            disabled={!model.supported}
            style={{ width: "100%" }}
          >
            <div className="role">{rankLabel(model.role, index, language)}</div>
            <div className="name">{model.shortName}</div>
            <div className="reason">{model.reason} · {model.size || "ready"}</div>
            {!model.supported && <div style={{ color: "hsl(var(--destructive))", marginTop: 6, fontSize: "0.85rem" }}>{t(language, "flow.recommend.unsupported")}</div>}
          </button>
        ))}
      </div>

      <div style={{ marginTop: "1.1rem", display: "flex", justifyContent: "center", gap: "1rem", alignItems: "center" }}>
        <Button variant="ghost" onClick={onBack}>{t(language, "flow.recommend.back")}</Button>
        <div style={{ fontSize: "0.82rem", color: "hsl(var(--fg-muted))" }}>{t(language, "flow.recommend.hint")}</div>
      </div>
    </div>
  );
}

function InstallScreen({
  model,
  onBack,
  onComplete,
}: {
  model: RecommendedModel;
  onBack: () => void;
  onComplete: () => void;
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
      <div style={{ margin: "0.6rem auto 1rem" }}>
        <LivingBrain 
          state={brainStateForStage} 
          intensity={stage === "download" || stage === "load" ? 0.96 : 0.82} 
          size="normal" 
        />
      </div>

      <div className="ritual-progress">
        <div className="ritual-stage-list">
          {(["install", "download", "validate", "load"] as const).map((item) => (
            <div key={item} className={`ritual-stage ${installStepState(stage, item)}`}>
              <CheckCircle2 style={{ width: 15, height: 15 }} />
              <span>{installLabel(item, language)}</span>
            </div>
          ))}
        </div>

        <div className="ritual-bar">
          <span style={{ width: `${Math.max(4, Math.min(100, percent))}%` }} />
        </div>
      </div>

      <div className="ritual-status">{message}</div>
      <div className="ritual-card" style={{ margin: "0.8rem auto 0", maxWidth: 540, fontSize: "0.86rem", color: "hsl(var(--fg-muted))" }}>
        {t(language, "flow.install.note")}
      </div>

      {error && (
        <div className="ritual-card" style={{ borderColor: "hsl(var(--destructive)/0.45)", background: "hsl(var(--destructive)/0.07)", marginBottom: "1rem" }}>
          {error}
          <div style={{ marginTop: "0.5rem", fontSize: "0.85rem" }}>{t(language, "flow.install.retry")}</div>
        </div>
      )}

      <div style={{ display: "flex", gap: "0.75rem", justifyContent: "center", marginTop: "1rem" }}>
        <Button variant="ghost" onClick={onBack} disabled={busy}>{t(language, "flow.install.back")}</Button>

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

      <div style={{ fontSize: "0.72rem", color: "hsl(var(--fg-muted))", marginTop: "0.9rem" }}>
        {t(language, "flow.install.local")}
      </div>
    </div>
  );
}

function buildDetectedFacts(analysis: FlowAnalysis | null) {
  if (!analysis) {
    return [
      { label: "Computer", value: "Checking", detail: "Operating system and chip" },
      { label: "Memory", value: "Checking", detail: "Available room for local thinking" },
      { label: "Graphics", value: "Checking", detail: "Local acceleration support" },
      { label: "Local Support", value: "Checking", detail: "Installed model helpers" },
      { label: "Models", value: "Checking", detail: "Installed local Brains" },
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
      label: "Computer",
      value: appleSilicon ? "Apple Silicon Mac" : friendlyOs(profile.os),
      detail: "Ready for local Digital Brain use",
    },
    {
      label: "Memory",
      value: ramGb ? `${Math.round(ramGb)} GB` : "Detected",
      detail: "Enough context for the recommended model",
    },
    {
      label: "Graphics",
      value: gpu.vendor || sysinfo.gpu_mem_gb ? "Local acceleration ready" : "Standard local mode",
      detail: "Lattice will choose the best available path",
    },
    {
      label: "Local Support",
      value: installedRuntimes.length ? "Ready" : "Will be prepared",
      detail: installedRuntimes.length ? "Installed model helpers detected" : "Lattice will add what is needed",
    },
    {
      label: "Models",
      value: loadedModels.length ? `${loadedModels.length} already installed` : "None installed yet",
      detail: loadedModels.length ? "One can be loaded immediately" : "Lattice will guide the first install",
    },
  ];
}

function buildRecommendations(analysis: FlowAnalysis | null): RecommendedModel[] {
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
    best ? { ...best, role: "best" as const, reason: "Best Experience" } : null,
    faster ? { ...faster, role: "faster" as const, reason: "Faster" } : null,
    advanced ? { ...advanced, role: "advanced" as const, reason: "Advanced" } : null,
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
    reason: String(row.reason || "Recommended"),
    supported,
    downloadRequired: Boolean(row.download_required),
  };
}

function fallbackModel(): RecommendedModel {
  return {
    id: "mlx-community/Qwen3-VL-8B-Instruct-4bit",
    loadId: "mlx-community/Qwen3-VL-8B-Instruct-4bit",
    engine: "local_mlx",
    name: "Qwen3-VL 8B",
    shortName: "Qwen 3",
    family: "Qwen 3",
    size: "ready",
    role: "best",
    reason: "Best Experience",
    supported: true,
    downloadRequired: false,
  };
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

function friendlyOs(value: unknown) {
  const text = String(value || "Computer");
  if (/darwin|mac/i.test(text)) return "Mac";
  if (/win/i.test(text)) return "Windows PC";
  if (/linux/i.test(text)) return "Linux computer";
  return "Computer";
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
    download: language === "ko" ? "모델 파일을 받는 중입니다." : "Getting the model files.",
    validate: language === "ko" ? "Brain이 응답할 수 있는지 확인 중입니다." : "Checking that the Brain can answer.",
    load: language === "ko" ? "Brain을 불러오는 중입니다." : "Loading the Brain.",
    done: t(language, "flow.install.done"),
    idle: t(language, "flow.install.wait"),
    error: language === "ko" ? "확인이 필요한 일이 있습니다." : "Something needs attention.",
  }[stage];
  return cleanConsumerText(String(event.user_message || event.message || fallback));
}

function installLabel(stage: "install" | "download" | "validate" | "load", language: Language) {
  if (language === "ko") {
    if (stage === "install") return "준비";
    if (stage === "download") return "다운로드";
    if (stage === "validate") return "확인";
    return "로드";
  }
  if (stage === "install") return "Install";
  if (stage === "download") return "Download";
  if (stage === "validate") return "Validate";
  return "Load";
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
