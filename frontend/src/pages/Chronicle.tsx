import * as React from "react";
// Route-scoped copy: importing the namespace registers it into the shared
// table and keeps it inside this lazy chunk instead of the entry bundle.
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

/**
 * 연대기 — the Brain's own history, and the first surface built on the
 * bitemporal columns v11.1.0 started writing.
 *
 * Nothing on this screen is generated. Every number, title and preview is a row
 * the Brain already stored, rearranged by day; the only computation is addition
 * and a calendar. That is what lets the page make its claim honestly, and it is
 * why there is no "요약" anywhere in the copy.
 *
 * The scrubber is the single source of "when". The heat-map writes to it, the
 * day story and the rewind panel read from it, and `null` means "follow the
 * latest day" — so a Brain that ingests something while the page is open moves
 * the handle forward instead of pinning it to a day that is no longer the end.
 */
export function ChroniclePage() {
  const language = useAppStore((state) => state.language);
  const overview = useQuery({ queryKey: ["chronicleOverview"], queryFn: latticeApi.chronicleOverview });
  const overviewResult = overview.data;
  const data = overviewResult?.data as ChronicleOverview | undefined;

  const timeline = React.useMemo(() => buildTimeline(data?.series ?? []), [data]);
  const heatmap = React.useMemo(() => buildHeatmap(timeline), [timeline]);

  // State is the chosen *day*, not its position. A position would need clamping
  // against a timeline that can grow under it, and every clamp would be a branch
  // no interaction can reach; a day that is no longer in the record simply falls
  // back to the latest one, which is also what "follow the newest day" means.
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
            onIndexChange={(next) => setPinnedDate(timeline[next].date)}
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
