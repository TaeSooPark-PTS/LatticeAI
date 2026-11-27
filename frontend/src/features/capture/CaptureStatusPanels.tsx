import { HardDrive, ScanLine, Share2, Sparkles } from "lucide-react";
import { latticeApi } from "@/api/client";
import { ActionButton, DataPanel, EntityList, StructuredView } from "@/components/primitives";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { DocumentLibrary } from "@/features/capture/DocumentLibrary";
import { FolderMemoryHealthCard } from "@/features/capture/FolderMemoryHealth";
import { t } from "@/i18n";
import { asArray, fmtNumber } from "@/lib/utils";
import { useAppStore } from "@/store/appStore";
import { useCapturePipeline, useRecentCaptures } from "./hooks/useCaptureIntake";

const stepIcon = { read: ScanLine, understand: Sparkles, connect: Share2 } as const;

export function PipelinePanel() {
  const language = useAppStore((state) => state.language);
  const mode = useAppStore((state) => state.mode);
  const { index, stats, journey, hasPipelineData } = useCapturePipeline();

  return (
    <div className="capture-secondary-column space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><ScanLine className="h-4 w-4" /> {t(language, "capture.pipeline.journey.title")}</CardTitle>
          <CardDescription>{t(language, "capture.pipeline.journey.detail")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <ol className="capture-journey flex flex-col md:flex-row gap-3 items-stretch" aria-label={t(language, "capture.pipeline.journey.aria")}>
            {journey.steps.map(({ key, state, count }) => {
              const Icon = stepIcon[key as keyof typeof stepIcon];
              return (
                <li key={key} className={`capture-journey-step is-${state} flex-1 p-3 rounded-lg border border-border/60 bg-muted/20 flex flex-col justify-between`}>
                  <div className="capture-journey-step-header flex items-center gap-2">
                    <span className="capture-journey-mark" aria-hidden="true"><Icon className="h-4 w-4" /></span>
                    <div className="capture-journey-copy min-w-0 flex-1">
                      <strong className="block text-sm">{t(language, `capture.pipeline.step.${key}`)}</strong>
                      <small className="block text-xs text-muted-foreground">{t(language, `capture.pipeline.step.${key}.detail`)}</small>
                    </div>
                  </div>
                  <div className="capture-journey-step-meta flex items-center justify-between gap-2 mt-3 pt-2 border-t border-border/40 text-xs">
                    <Badge variant="muted" className="capture-journey-count">
                      {t(language, "ui.value.records", { count })}
                    </Badge>
                    <span className="capture-journey-state">{t(language, `capture.pipeline.step.${state}`)}</span>
                  </div>
                </li>
              );
            })}
          </ol>
          {hasPipelineData ? (
            <div className="flex flex-wrap gap-2 text-sm pt-2">
              <Badge variant="success">{t(language, "capture.pipeline.count.remembered", { count: fmtNumber(journey.extracted) })}</Badge>
              <Badge variant="muted">{t(language, "capture.pipeline.count.connections", { count: fmtNumber(journey.connected) })}</Badge>
              <Badge variant={journey.waiting ? "warning" : "muted"}>
                {journey.waiting
                  ? t(language, "capture.pipeline.count.waiting", { count: fmtNumber(journey.waiting) })
                  : t(language, "capture.pipeline.count.none")}
              </Badge>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">{t(language, "capture.pipeline.empty")}</p>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><HardDrive className="h-4 w-4" /> {t(language, "capture.pipeline.refresh")}</CardTitle>
          <CardDescription>{t(language, "capture.pipeline.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <ActionButton label={t(language, "capture.pipeline.rebuild")} action={() => latticeApi.rebuildIndex()} invalidate={["index"]} />
        </CardContent>
      </Card>
      {mode === "basic" ? null : (
        <>
          <DataPanel title={t(language, "capture.pipeline.status")} result={index.data}>
            {(data) => <StructuredView value={data} />}
          </DataPanel>
          <DataPanel title={t(language, "capture.pipeline.growth")} result={stats.data}>
            {(data) => <StructuredView value={data} />}
          </DataPanel>
        </>
      )}
    </div>
  );
}

export function RecentCapturePanel() {
  const language = useAppStore((state) => state.language);
  const mode = useAppStore((state) => state.mode);
  const { docs, local, agent } = useRecentCaptures();

  return (
    <div className="capture-secondary-column space-y-4">
      <DocumentLibrary result={docs.data} onRetry={() => void docs.refetch()} />
      <FolderMemoryHealthCard language={language} />
      <DataPanel title={t(language, "capture.local.sources")} result={local.data}>
        {(data) => (
          <div className="space-y-3">
            <EntityList items={(data as Record<string, unknown>).sources} titleKey="path" metaKey="status" />
            {asArray<Record<string, unknown>>((data as Record<string, unknown>).sources).map((source) => (
              <ActionButton
                key={String(source.id || source.source_id || source.path)}
                label={t(language, "capture.local.stop", { source: String(source.path || source.id || t(language, "capture.local.source")) })}
                action={() => latticeApi.localWatchStop(String(source.id || source.source_id))}
                invalidate={["localSources"]}
              />
            ))}
          </div>
        )}
      </DataPanel>
      {mode === "basic" ? null : (
        <DataPanel title={t(language, "capture.local.access")} result={agent.data}>
          {(data) => <StructuredView value={data} />}
        </DataPanel>
      )}
    </div>
  );
}
