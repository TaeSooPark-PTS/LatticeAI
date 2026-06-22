import * as React from "react";
import { latticeApi } from "@/api/client";
import { type BrainState, LivingBrain } from "@/components/LivingBrain";
import { t } from "@/i18n";
import { useAppStore } from "@/store/appStore";
import {
  AnalysisScreen,
  InstallScreen,
  LanguageChooser,
  LoginScreen,
  RecommendationScreen,
  buildRecommendations,
  fallbackModel,
  type FlowAnalysis,
  type FlowStep,
  type RecommendedModel,
} from "@/components/onboarding/ProductFlowScreens";

const FLOW_COMPLETE_KEY = "lattice.productFlow.complete";

export function readProductFlowComplete() {
  try {
    return localStorage.getItem(FLOW_COMPLETE_KEY) === "true";
  } catch {}
  return false;
}

export function ProductFlow({ onComplete }: { onComplete: () => void }) {
  const language = useAppStore((state) => state.language);
  const [step, setStep] = React.useState<FlowStep>("wake");
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
        setup: setup.ok ? setup.data as Record<string, unknown> : null,
        recommendations: recommendationsResult.ok ? recommendationsResult.data as Record<string, unknown> : null,
        models: models.ok ? models.data as Record<string, unknown> : null,
        sysinfo: sysinfo.ok ? sysinfo.data as Record<string, unknown> : null,
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
        <div className="ritual-brain">
          <LivingBrain state={brainStateForStep(step)} intensity={step === "install" ? 0.92 : 0.7} size="large" showLabel={false} />
        </div>

        {step === "wake" && <WakeBrainScreen onWake={() => setStep("login")} onUseExisting={() => completeFlow(onComplete)} />}

        {step === "login" && <LoginScreen onSuccess={() => setStep("analysis")} />}

        {step === "analysis" && (
          <AnalysisScreen analysis={analysis} error={analysisError} onContinue={() => setStep("recommend")} />
        )}

        {step === "recommend" && (
          <RecommendationScreen
            recommendations={recommendations}
            onBack={() => setStep("analysis")}
            onSkipModel={() => completeFlow(onComplete)}
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
            onComplete={() => completeFlow(onComplete)}
            onLater={() => completeFlow(onComplete)}
          />
        )}
      </div>
    </div>
  );
}

function WakeBrainScreen({ onWake, onUseExisting }: { onWake: () => void; onUseExisting: () => void }) {
  const language = useAppStore((state) => state.language);
  return (
    <section className="ritual-wake" aria-label={t(language, "flow.wake.aria")}>
      <div className="ritual-title">{t(language, "flow.wake.title")}</div>
      <div className="ritual-subtitle">{t(language, "flow.wake.body")}</div>
      <div className="ritual-wake-plan" aria-label={t(language, "flow.wake.plan.aria")}>
        <div>
          <span>1</span>
          <strong>{t(language, "flow.wake.step.identity")}</strong>
        </div>
        <div>
          <span>2</span>
          <strong>{t(language, "flow.wake.step.check")}</strong>
        </div>
        <div>
          <span>3</span>
          <strong>{t(language, "flow.wake.step.voice")}</strong>
        </div>
      </div>
      <div className="ritual-button-row">
        <button type="button" className="ritual-full-button" onClick={onWake}>
          {t(language, "flow.wake.primary")}
        </button>
        <button type="button" className="ritual-secondary-button" onClick={onUseExisting}>
          {t(language, "flow.wake.existing")}
        </button>
      </div>
    </section>
  );
}

function completeFlow(onComplete: () => void) {
  try { localStorage.setItem(FLOW_COMPLETE_KEY, "true"); } catch {}
  onComplete();
}

function brainStateForStep(step: FlowStep): BrainState {
  if (step === "wake") return "idle";
  if (step === "analysis") return "listening";
  if (step === "recommend") return "recalling";
  if (step === "install") return "thinking";
  return "idle";
}
