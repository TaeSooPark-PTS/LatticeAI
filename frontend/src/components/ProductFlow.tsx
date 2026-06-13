import * as React from "react";
import { CheckCircle2, ChevronRight, Cpu, Download, LockKeyhole, MonitorCog, Sparkles } from "lucide-react";
import { latticeApi } from "@/api/client";
import { LivingBrain } from "@/components/LivingBrain";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn, asArray } from "@/lib/utils";

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

export function ProductFlow({ onComplete }: { onComplete: () => void }) {
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
        setAnalysisError("Lattice could not finish reading this computer. You can still continue with a safe default.");
      }
    }
    void runAnalysis();
    return () => { cancelled = true; };
  }, [analysis, step]);

  return (
    <main className="product-flow-shell" aria-label="Lattice first run">
      <div className="product-flow-orbit" aria-hidden="true" />
      {step === "login" ? (
        <LoginScreen onSuccess={() => setStep("analysis")} />
      ) : null}
      {step === "analysis" ? (
        <AnalysisScreen
          analysis={analysis}
          error={analysisError}
          onContinue={() => setStep("recommend")}
        />
      ) : null}
      {step === "recommend" ? (
        <RecommendationScreen
          recommendations={recommendations}
          onBack={() => setStep("analysis")}
          onSelect={(model) => {
            setSelected(model);
            setStep("install");
          }}
        />
      ) : null}
      {step === "install" ? (
        <InstallScreen
          model={selected || recommendations[0] || fallbackModel()}
          onBack={() => setStep("recommend")}
          onComplete={() => {
            try { localStorage.setItem(FLOW_COMPLETE_KEY, "true"); } catch {}
            onComplete();
          }}
        />
      ) : null}
    </main>
  );
}

function LoginScreen({ onSuccess }: { onSuccess: () => void }) {
  const [email, setEmail] = React.useState(() => {
    try {
      const saved = localStorage.getItem(FLOW_USER_KEY);
      return saved ? JSON.parse(saved).email || "you@local" : "you@local";
    } catch {
      return "you@local";
    }
  });
  const [password, setPassword] = React.useState("");
  const [name, setName] = React.useState("You");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const safePassword = password || "Lattice123";
    let result = await latticeApi.login(email, safePassword);
    if (!result.ok) {
      const registered = await latticeApi.register({
        email,
        password: safePassword,
        name: name || email.split("@")[0] || "You",
        nickname: name || "You",
      });
      if (registered.ok) result = await latticeApi.login(email, safePassword);
    }
    if (!result.ok) {
      const profile = await latticeApi.profile();
      if (!profile.ok) {
        setBusy(false);
        setError("We could not open your local profile. Check your password or create the first local account.");
        return;
      }
    }
    try { localStorage.setItem(FLOW_USER_KEY, JSON.stringify({ email, name })); } catch {}
    setBusy(false);
    onSuccess();
  }

  return (
    <section className="login-screen" aria-label="Login">
      <div className="login-mark" aria-hidden="true"><LockKeyhole className="h-5 w-5" /></div>
      <div className="login-card">
        <div>
          <div className="login-kicker">Lattice AI</div>
          <h1>Enter your Brain.</h1>
          <p>Your private workspace starts with a local profile.</p>
        </div>
        <form className="login-form" onSubmit={submit}>
          <label>
            <span>Name</span>
            <Input value={name} onChange={(event) => setName(event.target.value)} autoComplete="name" />
          </label>
          <label>
            <span>Email</span>
            <Input value={email} onChange={(event) => setEmail(event.target.value)} type="email" autoComplete="email" />
          </label>
          <label>
            <span>Password</span>
            <Input value={password} onChange={(event) => setPassword(event.target.value)} type="password" autoComplete="current-password" placeholder="Use your local password" />
          </label>
          {error ? <div className="flow-error">{error}</div> : null}
          <Button className="login-submit" type="submit" disabled={busy || !email.trim()}>
            {busy ? "Opening" : "Continue"} <ChevronRight className="h-4 w-4" />
          </Button>
        </form>
      </div>
    </section>
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
  const detected = buildDetectedFacts(analysis);
  return (
    <section className="flow-panel analysis-screen" aria-label="Environment Analysis">
      <div className="flow-panel-head">
        <div>
          <div className="flow-kicker"><MonitorCog className="h-4 w-4" /> Environment Analysis</div>
          <h1>Learning what your computer can do.</h1>
          <p>Lattice checks the essentials, then recommends the best local Brain for this machine.</p>
        </div>
        <Badge variant={analysis ? "success" : "muted"}>{analysis ? "complete" : "analyzing"}</Badge>
      </div>
      <div className="analysis-grid">
        {detected.map((item) => (
          <div key={item.label} className="analysis-fact">
            <span>{item.label}</span>
            <strong>{item.value}</strong>
            <small>{item.detail}</small>
          </div>
        ))}
      </div>
      <div className="recommendation-callout">
        <Sparkles className="h-5 w-5" />
        <div>
          <strong>{analysis ? recommendedSummary(analysis) : "Recommendation is being prepared."}</strong>
          <span>{analysis ? "You will choose from a short, ranked list next." : "This usually takes a moment."}</span>
        </div>
      </div>
      {error ? <div className="flow-error">{error}</div> : null}
      <div className="flow-actions">
        <Button onClick={onContinue} disabled={!analysis && !error}>See recommended models</Button>
      </div>
    </section>
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
  const items = recommendations.length ? recommendations : [fallbackModel()];
  return (
    <section className="flow-panel recommendation-screen" aria-label="Recommended Models">
      <div className="flow-panel-head">
        <div>
          <div className="flow-kicker"><Cpu className="h-4 w-4" /> Recommended Models</div>
          <h1>Recommended for your computer.</h1>
          <p>A short list, ranked for this Mac. No catalog digging required.</p>
        </div>
      </div>
      <div className="model-recommendation-list">
        {items.slice(0, 3).map((model, index) => (
          <button
            key={`${model.role}-${model.id}`}
            className={cn("model-recommendation-card", model.role)}
            onClick={() => onSelect(model)}
            disabled={!model.supported}
          >
            <span className="model-rank">{rankLabel(model.role, index)}</span>
            <span>
              <strong>{model.shortName}</strong>
              <small>{model.reason}</small>
            </span>
            <Badge variant={model.supported ? "success" : "warning"}>{model.supported ? model.size || "ready" : "needs update"}</Badge>
          </button>
        ))}
      </div>
      <div className="flow-actions split">
        <Button variant="ghost" onClick={onBack}>Back</Button>
        <span>Choose one recommendation to continue.</span>
      </div>
    </section>
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
  const [busy, setBusy] = React.useState(false);
  const [stage, setStage] = React.useState<InstallStage>("idle");
  const [percent, setPercent] = React.useState(0);
  const [message, setMessage] = React.useState("Ready when you are.");
  const [error, setError] = React.useState<string | null>(null);

  async function start() {
    setBusy(true);
    setError(null);
    setStage("install");
    setPercent(8);
    setMessage("Preparing the Brain.");
    const result = await latticeApi.streamModelPrepare(
      { model: model.loadId, engine: model.engine || "local_mlx", allow_download: true },
      {
        onProgress: (event) => {
          const nextStage = friendlyInstallStage(String(event.stage || ""));
          setStage(nextStage);
          setPercent(Number(event.percent || percentForStage(nextStage)));
          setMessage(friendlyInstallMessage(event, nextStage));
        },
        onDone: () => {
          setStage("done");
          setPercent(100);
          setMessage("Your Brain is ready.");
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
      setMessage("Your Brain is ready.");
      window.setTimeout(onComplete, 700);
    } else {
      setStage("error");
      setError(consumerError(result.data as ApiData));
    }
  }

  return (
    <section className="flow-panel install-screen" aria-label="Install and Load">
      <div className="install-hero">
        <LivingBrain activity={busy ? "thinking" : stage === "done" ? "listening" : "idle"} compact />
        <div>
          <div className="flow-kicker"><Download className="h-4 w-4" /> Install & Load</div>
          <h1>{model.shortName}</h1>
          <p>Lattice will install, download, validate, and load the selected Brain.</p>
        </div>
      </div>
      <div className="install-steps">
        {(["install", "download", "validate", "load"] as const).map((item) => (
          <div key={item} className={cn("install-step", installStepState(stage, item))}>
            <CheckCircle2 className="h-4 w-4" />
            <span>{installLabel(item)}</span>
          </div>
        ))}
      </div>
      <div className="install-progress">
        <div>
          <strong>{message}</strong>
          <span>{stage === "error" ? "We will explain what to try next." : `${Math.round(percent)}%`}</span>
        </div>
        <div className="install-bar"><span style={{ width: `${Math.max(0, Math.min(100, percent))}%` }} /></div>
      </div>
      {error ? <div className="flow-error">{error}</div> : null}
      <div className="flow-actions split">
        <Button variant="ghost" onClick={onBack} disabled={busy}>Back</Button>
        <Button onClick={stage === "done" ? onComplete : start} disabled={busy || !model.supported}>
          {stage === "done" ? "Enter Brain" : busy ? "Loading" : "Install & Load"}
        </Button>
      </div>
    </section>
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
      detail: "Ready for local AI workspace use",
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

function rankLabel(role: RecommendedModel["role"], index: number) {
  if (role === "best") return "Best Experience";
  if (role === "faster") return "Faster";
  if (role === "advanced") return "Advanced";
  return `Choice ${index + 1}`;
}

function recommendedSummary(analysis: FlowAnalysis) {
  const recs = asRecord(analysis.recommendations?.recommendations);
  const topPick = asRecord(recs.top_pick);
  if (topPick.name || topPick.id) return `${friendlyModelName(String(topPick.name || topPick.id))} looks like the best fit.`;
  return "A private local Brain is recommended for this computer.";
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

function friendlyInstallMessage(event: ApiData, stage: InstallStage) {
  const fallback = {
    install: "Preparing the Brain.",
    download: "Getting the model files.",
    validate: "Checking that the Brain can answer.",
    load: "Loading the Brain.",
    done: "Your Brain is ready.",
    idle: "Ready when you are.",
    error: "Something needs attention.",
  }[stage];
  return cleanConsumerText(String(event.user_message || event.message || fallback));
}

function installLabel(stage: "install" | "download" | "validate" | "load") {
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
