import { Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { asArray } from "@/lib/utils";
import { t, type Language } from "@/i18n";
import { useAppStore } from "@/store/appStore";
import { asRecord, friendlyModelName, type FlowAnalysis } from "./recommendationModel";

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

function recommendedSummary(analysis: FlowAnalysis, language: Language) {
  const recs = asRecord(analysis.recommendations?.recommendations);
  const topPick = asRecord(recs.top_pick);
  if (topPick.name || topPick.id) {
    const model = friendlyModelName(String(topPick.name || topPick.id));
    return language === "ko" ? `${model}이 이 컴퓨터에 가장 잘 맞습니다.` : `${model} looks like the best fit.`;
  }
  return language === "ko" ? "이 컴퓨터에는 개인 로컬 Brain을 추천합니다." : "A private local Brain is recommended for this computer.";
}

function friendlyOs(value: unknown, language: Language) {
  const text = String(value || "Computer");
  if (/darwin|mac/i.test(text)) return "Mac";
  if (/win/i.test(text)) return "Windows PC";
  if (/linux/i.test(text)) return "Linux computer";
  return t(language, "flow.analysis.fact.computer");
}
