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
    <div className="ritual-shell" aria-label="Awaken your Brain">
      <div className="ritual-container">
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
    <div>
      <div className="ritual-title">Welcome to your mind.</div>
      <div className="ritual-subtitle">This is private. Everything stays on your machine. Begin by opening a local profile for your Brain.</div>

      <form onSubmit={submit} className="ritual-card" style={{ maxWidth: 420, margin: "0 auto" }}>
        <div style={{ display: "grid", gap: "0.85rem" }}>
          <div>
            <div style={{ fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: "1px", color: "hsl(var(--fg-muted))", marginBottom: 4 }}>Your name</div>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="You" />
          </div>
          <div>
            <div style={{ fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: "1px", color: "hsl(var(--fg-muted))", marginBottom: 4 }}>Email (local only)</div>
            <Input value={email} onChange={(e) => setEmail(e.target.value)} type="email" placeholder="you@local" />
          </div>
          <div>
            <div style={{ fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: "1px", color: "hsl(var(--fg-muted))", marginBottom: 4 }}>Password</div>
            <Input value={password} onChange={(e) => setPassword(e.target.value)} type="password" placeholder="Create a strong local password" />
          </div>
        </div>

        {error && <div style={{ marginTop: "0.85rem", padding: "0.6rem 0.85rem", background: "hsl(var(--destructive)/0.12)", border: "1px solid hsl(var(--destructive)/0.4)", borderRadius: 10, fontSize: "0.9rem" }}>{error}</div>}

        <Button type="submit" disabled={busy || !email.trim()} style={{ width: "100%", marginTop: "1rem" }}>
          {busy ? "Opening the Brain..." : "Open my Brain"} 
        </Button>
        <div style={{ fontSize: "0.75rem", color: "hsl(var(--fg-muted))", marginTop: "0.6rem" }}>
          Your first conversation will feel like coming home.
        </div>
      </form>
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
  const detected = buildDetectedFacts(analysis);
  return (
    <div>
      <div className="ritual-title">Understanding your home.</div>
      <div className="ritual-subtitle">
        We are learning what kind of mind this computer can support. Your Brain will live here — quietly, privately, powerfully.
      </div>

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
            <div style={{ fontWeight: 620 }}>{analysis ? recommendedSummary(analysis) : "Preparing the best fit..."}</div>
            <div style={{ fontSize: "0.9rem", color: "hsl(var(--fg-muted))" }}>
              {analysis ? "A short, personal list of minds is ready for you to choose from." : "Reading your machine. This is gentle."}
            </div>
          </div>
        </div>
      </div>

      {error && <div className="ritual-card" style={{ borderColor: "hsl(var(--destructive)/0.4)", background: "hsl(var(--destructive)/0.06)" }}>{error}</div>}

      <div style={{ marginTop: "1.25rem" }}>
        <Button onClick={onContinue} disabled={!analysis && !error} style={{ minWidth: 260 }}>
          See how your Brain can think
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
  const items = recommendations.length ? recommendations : [fallbackModel()];
  return (
    <div>
      <div className="ritual-title">How shall your mind think today?</div>
      <div className="ritual-subtitle">
        A short, honest list chosen for the computer you are on right now. Pick the one that feels right.
      </div>

      <div style={{ maxWidth: 560, margin: "0 auto" }}>
        {items.slice(0, 3).map((model, index) => (
          <button
            key={`${model.role}-${model.id}`}
            className="ritual-model-card"
            onClick={() => model.supported && onSelect(model)}
            disabled={!model.supported}
            style={{ width: "100%" }}
          >
            <div className="role">{rankLabel(model.role, index)}</div>
            <div className="name">{model.shortName}</div>
            <div className="reason">{model.reason} · {model.size || "ready"}</div>
            {!model.supported && <div style={{ color: "hsl(var(--destructive))", marginTop: 6, fontSize: "0.85rem" }}>Needs attention on this machine</div>}
          </button>
        ))}
      </div>

      <div style={{ marginTop: "1.1rem", display: "flex", justifyContent: "center", gap: "1rem", alignItems: "center" }}>
        <Button variant="ghost" onClick={onBack}>Back</Button>
        <div style={{ fontSize: "0.82rem", color: "hsl(var(--fg-muted))" }}>Your choice becomes the current voice of your Brain.</div>
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
  const [busy, setBusy] = React.useState(false);
  const [stage, setStage] = React.useState<InstallStage>("idle");
  const [percent, setPercent] = React.useState(0);
  const [message, setMessage] = React.useState("Your Brain is waiting for this mind.");
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

  const brainStateForStage: any = 
    stage === "download" ? "thinking" :
    stage === "validate" ? "recalling" :
    stage === "load" ? "synthesizing" :
    stage === "done" ? "idle" : "listening";

  return (
    <div>
      <div className="ritual-title">Bring this mind home.</div>
      <div className="ritual-subtitle">
        <strong>{model.shortName}</strong> — {model.reason}.<br />
        We will download (if needed), validate, and load it. Nothing happens without your explicit consent.
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
              <span>{installLabel(item)}</span>
            </div>
          ))}
        </div>

        <div className="ritual-bar">
          <span style={{ width: `${Math.max(4, Math.min(100, percent))}%` }} />
        </div>
      </div>

      <div className="ritual-status">{message}</div>

      {error && (
        <div className="ritual-card" style={{ borderColor: "hsl(var(--destructive)/0.45)", background: "hsl(var(--destructive)/0.07)", marginBottom: "1rem" }}>
          {error}
          <div style={{ marginTop: "0.5rem", fontSize: "0.85rem" }}>You can go back and choose a different mind, or try again.</div>
        </div>
      )}

      <div style={{ display: "flex", gap: "0.75rem", justifyContent: "center", marginTop: "1rem" }}>
        <Button variant="ghost" onClick={onBack} disabled={busy}>Choose differently</Button>

        {stage !== "done" ? (
          <Button 
            onClick={start} 
            disabled={busy || !model.supported}
          >
            {busy ? "Waking the mind..." : "Yes — make this my Brain"}
          </Button>
        ) : (
          <Button onClick={onComplete}>Enter your Brain</Button>
        )}
      </div>

      <div style={{ fontSize: "0.72rem", color: "hsl(var(--fg-muted))", marginTop: "0.9rem" }}>
        Explicit consent only. All work happens locally on your machine.
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
