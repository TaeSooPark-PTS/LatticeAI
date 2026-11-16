import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { latticeApi } from "@/api/client";
import { formatStreamMetrics } from "@/components/onboarding/recommendationModel";
import { t, type Language } from "@/i18n";
import { deriveActiveModelView, deriveModelsPanelView } from "../libraryDomain";

export function useActiveModelSwitcher(language: Language) {
  const qc = useQueryClient();
  const models = useQuery({ queryKey: ["models"], queryFn: latticeApi.models });
  const [switching, setSwitching] = React.useState<string | null>(null);
  const [switchError, setSwitchError] = React.useState<string | null>(null);

  const view = deriveActiveModelView(models.data?.data);
  const unavailable = Boolean(models.data && !models.data.ok);

  async function switchTo(model: Record<string, unknown>) {
    const id = view.modelId(model);
    setSwitching(id);
    setSwitchError(null);
    const engine = String(model.recommended_engine || model.engine || "") || undefined;
    const result = await latticeApi.loadModel(String(model.recommended_load_id || id), engine, false);
    setSwitching(null);
    if (!result.ok) setSwitchError(result.error || t(language, "library.active.switchFailed"));
    await qc.invalidateQueries({ queryKey: ["models"] });
  }

  return {
    ...view,
    unavailable,
    switching,
    switchError,
    switchTo,
  };
}

export function useLibraryModels(language: Language) {
  const qc = useQueryClient();
  const models = useQuery({ queryKey: ["models"], queryFn: latticeApi.models });
  const recs = useQuery({
    queryKey: ["modelRecommendations", "local_mlx"],
    queryFn: () => latticeApi.modelRecommendations("local_mlx"),
  });
  const emb = useQuery({ queryKey: ["embeddings"], queryFn: latticeApi.embeddingsStatus });
  const [consent, setConsent] = React.useState(false);
  const [activeModel, setActiveModel] = React.useState<string | null>(null);
  const [progress, setProgress] = React.useState<Record<string, unknown>[]>([]);
  const [lastResult, setLastResult] = React.useState<Record<string, unknown> | null>(null);
  const [lastError, setLastError] = React.useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = React.useState(false);
  const streamReportedError = React.useRef(false);

  const derived = deriveModelsPanelView(models.data?.data, recs.data?.data, language);
  const latestProgress = progress[progress.length - 1] || null;
  const latestMetrics = formatStreamMetrics(latestProgress);

  async function prepareModel(loadId: string, engine: string, allowDownload: boolean) {
    setBusy(true);
    setActiveModel(loadId);
    setProgress([]);
    setLastResult(null);
    setLastError(null);
    streamReportedError.current = false;
    const result = await latticeApi.streamModelPrepare(
      { model: loadId, engine: engine || "local_mlx", allow_download: allowDownload },
      {
        onProgress: (event) => setProgress((items) => [...items.slice(-8), event]),
        onDone: (event) => setLastResult(event),
        onError: (event) => {
          streamReportedError.current = true;
          setLastError(event);
        },
      },
    );
    setBusy(false);
    await qc.invalidateQueries({ queryKey: ["models"] });
    await qc.invalidateQueries({ queryKey: ["modelRecommendations", "local_mlx"] });
    if (!result.ok && !streamReportedError.current) setLastError(result.data as Record<string, unknown>);
  }

  function unloadCurrentModel() {
    if (derived.currentId) {
      void latticeApi.unloadModel(derived.currentId).then(() => qc.invalidateQueries({ queryKey: ["models"] }));
    }
  }

  return {
    models,
    recs,
    emb,
    consent,
    setConsent,
    activeModel,
    latestProgress,
    latestMetrics,
    lastResult,
    lastError,
    busy,
    prepareModel,
    unloadCurrentModel,
    ...derived,
  };
}
