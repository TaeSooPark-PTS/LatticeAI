import * as React from "react";
import "@/i18n/chronicle";
import { useQuery } from "@tanstack/react-query";
import { History } from "lucide-react";

import { latticeApi, type ChronicleAsOf, type ChronicleDay, type ChronicleOverview } from "@/api/client";
import { EmptyState, LoadingPanel, OperationResult } from "@/components/primitives";
import { ActivityHeatmap } from "@/features/chronicle/ActivityHeatmap";
import { buildHeatmap, buildTimeline, endOfDay } from "@/features/chronicle/chronicleModel";
import { DayStory } from "@/features/chronicle/DayStory";
import { GrowthScrubber } from "@/features/chronicle/GrowthScrubber";
import { RewindPanel } from "@/features/chronicle/RewindPanel";
import { t } from "@/i18n";
import { useAppStore } from "@/store/appStore";

export function ChroniclePage() {
  const language = useAppStore((state) => state.language);
  const overview = useQuery({ queryKey: ["chronicleOverview"], queryFn: latticeApi.chronicleOverview });
  const overviewResult = overview.data;
  const data = overviewResult?.data as ChronicleOverview | undefined;

  const timeline = React.useMemo(() => buildTimeline(data?.series ?? []), [data]);
  const heatmap = React.useMemo(() => buildHeatmap(timeline), [timeline]);

  const [pinnedDate, setPinnedDate] = React.useState<string | null>(null);
  const maxIndex = Math.max(timeline.length - 1, 0);
  const pinnedIndex = pinnedDate === null ? -1 : timeline.findIndex((point) => point.date === pinnedDate);
  const index = pinnedIndex >= 0 ? pinnedIndex : maxIndex;
  const selected = timeline[index];
  const selectedDate = selected ? selected.date : "";
  const isPast = Boolean(selected) && index < maxIndex;

  const dayQuery = useQuery({
    queryKey: ["chronicleDay", selectedDate],
    queryFn: () => latticeApi.chronicleDay(selectedDate),
    enabled: selectedDate !== "",
  });
  const asOfQuery = useQuery({
    queryKey: ["chronicleAsOf", selectedDate],
    queryFn: () => latticeApi.chronicleAsOf(endOfDay(selectedDate)),
    enabled: isPast,
  });

  return (
    <div className="product-page chronicle-page" data-testid="page-chronicle">
      <header className="page-hero">
        <div className="page-kicker"><History className="h-4 w-4" /> {t(language, "chronicle.kicker")}</div>
        <h1 className="page-title">{t(language, "chronicle.title")}</h1>
        <p className="page-copy">{t(language, "chronicle.copy")}</p>
      </header>

      {overview.isLoading ? (
        <LoadingPanel title={t(language, "chronicle.loading")} />
      ) : overviewResult && !overviewResult.ok ? (
        <OperationResult result={overviewResult} />
      ) : timeline.length === 0 ? (
        <EmptyState
          title={t(language, "chronicle.empty.title")}
          detail={t(language, "chronicle.empty.detail")}
        />
      ) : (
        <div className="chronicle-stack">
          <GrowthScrubber
            timeline={timeline}
            index={index}
            onIndexChange={(next) => setPinnedDate(timeline[next]!.date)}
            language={language}
          />
          <ActivityHeatmap
            weeks={heatmap}
            selectedDate={selectedDate}
            onSelect={setPinnedDate}
            language={language}
          />
          {isPast ? (
            <RewindPanel
              asOf={(asOfQuery.data?.data as ChronicleAsOf | undefined) ?? null}
              date={selectedDate}
              loading={asOfQuery.isPending}
              onReset={() => setPinnedDate(null)}
              language={language}
            />
          ) : null}
          <DayStory
            day={(dayQuery.data?.data as ChronicleDay | undefined) ?? null}
            loading={dayQuery.isPending}
            language={language}
          />
        </div>
      )}
    </div>
  );
}
