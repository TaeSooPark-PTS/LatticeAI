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

function completeFlow(onComplete: () => void) {
  try { localStorage.setItem(FLOW_COMPLETE_KEY, "true"); } catch {}
  onComplete();
}

function brainStateForStep(step: FlowStep): BrainState {
  if (step === "analysis") return "listening";
  if (step === "recommend") return "recalling";
  if (step === "install") return "thinking";
  return "idle";
}
