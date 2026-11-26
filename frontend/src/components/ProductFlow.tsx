import * as React from "react";
import "@/i18n/brain";
import "@/i18n/onboarding";
import { latticeApi } from "@/api/client";
import { type BrainState, LivingBrain } from "@/components/LivingBrain";
import { t } from "@/i18n";
import { useAppStore } from "@/store/appStore";
import { ArrowRight, Cpu, Shield, Zap } from "lucide-react";
import {
  InstallScreen,
  LanguageChooser,
  LoginScreen,
  RecommendationScreen,
  evaluateAnalysis,
  fallbackModel,
  type AnalysisEndpointStatus,
  type FlowAnalysis,
  type FlowStep,
  type RecommendedModel,
} from "@/components/onboarding/ProductFlowScreens";
import { recommendedEmbeddingModel } from "@/components/onboarding/recommendationModel";

import { markProductFlowComplete } from "@/components/productFlowState";

export { readProductFlowComplete } from "@/components/productFlowState";

export function ProductFlow({ onComplete }: { onComplete: () => void }) {
  const language = useAppStore((state) => state.language);
  const [step, setStep] = React.useState<FlowStep>("wake");
  const [analysis, setAnalysis] = React.useState<FlowAnalysis | null>(null);
  const [endpoints, setEndpoints] = React.useState<AnalysisEndpointStatus | null>(null);
  const [reloadToken, setReloadToken] = React.useState(0);
  const [selected, setSelected] = React.useState<RecommendedModel | null>(null);
  const [embeddingReadiness, setEmbeddingReadiness] = React.useState<{
    state: "semantic" | "hash";
    modelId: string;
  }>({ state: "hash", modelId: "lattice-local-hash-v1" });

  const outcome = React.useMemo(() => evaluateAnalysis({ analysis, endpoints }), [analysis, endpoints]);
  const embeddingModel = React.useMemo(() => recommendedEmbeddingModel(analysis), [analysis]);

  React.useEffect(() => {
    let cancelled = false;
    async function runAnalysis() {
      const [setup, recommendationsResult, models, sysinfo, embStatus] = await Promise.all([
        latticeApi.setupScan(),
        latticeApi.modelRecommendations("local_mlx"),
        latticeApi.models(),
        latticeApi.sysinfo(),
        latticeApi.embeddingsStatus(),
      ]);
      if (cancelled) return;
      setAnalysis({
        setup: setup.ok ? setup.data as Record<string, unknown> : null,
        recommendations: recommendationsResult.ok ? recommendationsResult.data as Record<string, unknown> : null,
        models: models.ok ? models.data as Record<string, unknown> : null,
        sysinfo: sysinfo.ok ? sysinfo.data as Record<string, unknown> : null,
      });
      setEndpoints({
        setup: setup.ok,
        recommendations: recommendationsResult.ok,
        models: models.ok,
        sysinfo: sysinfo.ok,
      });
      const raw =
        embStatus.ok && embStatus.data && typeof embStatus.data === "object"
          ? (embStatus.data as Record<string, unknown>)
          : {};
      const provider = String(raw.provider || "").toLowerCase();
      const modelId = String(raw.model_id || "lattice-local-hash-v1");
      const isSemantic =
        !modelId.startsWith("lattice-local-hash") && provider !== "hash";
      setEmbeddingReadiness({
        state: isSemantic ? "semantic" : "hash",
        modelId,
      });
    }
    void runAnalysis();
    return () => { cancelled = true; };
  }, [reloadToken]);

  const retryAnalysis = React.useCallback(() => {
    setAnalysis(null);
    setEndpoints(null);
    setReloadToken((token) => token + 1);
  }, []);

  return (
    <div className="ritual-shell">
      <main className="ritual-container" aria-label={t(language, "flow.shell")}>
        <LanguageChooser />
        <div className="ritual-brain" data-scale={step === "wake" ? "hero" : "mark"}>
          <LivingBrain
            state={brainStateForStep(step)}
            intensity={step === "install" ? 0.92 : 0.7}
            size="large"
            showLabel={false}
          />
          <div className="ritual-edition" title={t(language, "brain.edition.tip")}>
            {t(language, "brain.edition")}
          </div>
        </div>

        {step === "wake" && <WakeBrainScreen onWake={() => setStep("login")} onUseExisting={() => completeFlow(onComplete)} />}

        {step === "login" && <LoginScreen onSuccess={() => setStep("recommend")} />}

        {step === "recommend" && (
          <>
            <RecommendationScreen
              status={outcome.status}
              reason={outcome.reason}
              recommendations={outcome.recommendations}
              analysis={analysis}
              onBack={() => setStep("login")}
              onRetry={retryAnalysis}
              onSkipModel={() => completeFlow(onComplete)}
              onSelect={(model) => {
                setSelected(model);
                setStep("install");
              }}
            />
            {outcome.status === "ready" && (
              <section
                className="ritual-embedding-recommendation mt-4 rounded-xl border border-border bg-background/60 p-4 text-left"
                data-testid="flow-embedding-recommendation"
                aria-label={t(language, "flow.recommend.embedding.aria")}
              >
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-xs font-medium text-primary">
                        {t(language, "flow.recommend.embedding.tag")}
                      </span>
                      <span
                        className="rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground"
                        data-testid="flow-embedding-readiness"
                        data-state={embeddingReadiness.state}
                      >
                        {embeddingReadiness.state === "semantic"
                          ? t(language, "flow.recommend.embedding.statusSemantic", {
                              model: embeddingReadiness.modelId,
                            })
                          : t(language, "flow.recommend.embedding.statusHash", {
                              model: embeddingReadiness.modelId,
                            })}
                      </span>
                    </div>
                    <div className="mt-0.5 text-base font-semibold">
                      {embeddingModel.name} <span className="text-xs font-normal text-muted-foreground">({embeddingModel.id})</span>
                    </div>
                    <div className="mt-1 text-xs text-muted-foreground">
                      {embeddingModel.reason}
                    </div>
                    <div className="mt-1 text-xs text-muted-foreground" data-testid="flow-embedding-mirror-notice">
                      {t(language, "flow.recommend.embedding.mirrorNotice", {
                        repo: String(embeddingModel.fallbackRepo),
                      })}
                    </div>
                  </div>
                  <button
                    type="button"
                    className="ritual-secondary-button"
                    data-testid="flow-select-embedding-model"
                    onClick={() => {
                      setSelected(embeddingModel);
                      setStep("install");
                    }}
                  >
                    {t(language, "flow.recommend.embedding.action")}
                  </button>
                </div>
              </section>
            )}
          </>
        )}

        {step === "install" && (
          <InstallScreen
            model={pickInstallModel(selected, outcome.recommendations)}
            onBack={() => setStep("recommend")}
            onComplete={() => completeFlow(onComplete)}
            onLater={() => completeFlow(onComplete)}
          />
        )}
      </main>
    </div>
  );
}

function WakeBrainScreen({ onWake, onUseExisting }: { onWake: () => void; onUseExisting: () => void }) {
  const language = useAppStore((state) => state.language);
  const valueCards = [
    { icon: Shield, label: t(language, "flow.wake.value.local.k"), body: t(language, "flow.wake.value.local.v") },
    { icon: Zap, label: t(language, "flow.wake.value.instant.k"), body: t(language, "flow.wake.value.instant.v") },
    { icon: Cpu, label: t(language, "flow.wake.value.brain.k"), body: t(language, "flow.wake.value.brain.v") },
  ];
  return (
    <section className="ritual-wake" aria-label={t(language, "flow.wake.aria")}>
      <div className="ritual-title">{t(language, "flow.wake.title")}</div>
      <div className="ritual-subtitle">{t(language, "flow.wake.body")}</div>

      <div className="ritual-value-grid" aria-label={t(language, "flow.wake.value.aria")}>
        {valueCards.map((card) => {
          const Icon = card.icon;
          return (
            <div className="ritual-value-card" key={card.label}>
              <div className="ritual-value-label"><Icon size={14} /> {card.label}</div>
              <div className="ritual-value-body">{card.body}</div>
            </div>
          );
        })}
      </div>

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

      <div className="ritual-button-row ritual-button-row-primary">
        <button type="button" className="ritual-full-button ritual-full-button-primary" onClick={onWake}>
          {t(language, "flow.wake.primary")} <ArrowRight size={18} />
        </button>
        <button type="button" className="ritual-secondary-button" onClick={onUseExisting}>
          {t(language, "flow.wake.existing")}
        </button>
      </div>
      <div className="ritual-start-note">{t(language, "flow.wake.startNote")}</div>
    </section>
  );
}

function completeFlow(onComplete: () => void) {
  markProductFlowComplete();
  onComplete();
}

export function pickInstallModel(
  selected: RecommendedModel | null,
  recommendations: RecommendedModel[],
): RecommendedModel {
  return selected || recommendations[0] || fallbackModel();
}

export function brainStateForStep(step: FlowStep): BrainState {
  if (step === "wake") return "idle";
  if (step === "analysis") return "listening";
  if (step === "recommend") return "recalling";
  if (step === "install") return "thinking";
  return "idle";
}
