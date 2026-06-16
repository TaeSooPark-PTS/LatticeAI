import * as React from "react";
import { CheckCircle2 } from "lucide-react";
import { latticeApi } from "@/api/client";
import { type BrainState, LivingBrain } from "@/components/LivingBrain";
import { Button } from "@/components/ui/button";
import { asArray } from "@/lib/utils";
import { t, type Language } from "@/i18n";
import { useAppStore } from "@/store/appStore";
import { DownloadConsentPanel } from "./DownloadConsentPanel";
import { asRecord, type ApiData, type RecommendedModel } from "./recommendationModel";

type InstallStage = "idle" | "install" | "download" | "validate" | "load" | "done" | "error";

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

  const brainStateForStage: BrainState =
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
