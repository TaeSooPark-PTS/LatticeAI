import * as React from "react";

import { t, type Language } from "@/i18n";
import { fmtNumber } from "@/lib/utils";
import { growthPaths, type ChronicleTimelinePoint } from "./chronicleModel";

const VIEW_WIDTH = 1000;
const VIEW_HEIGHT = 220;
const WEEK = 7;

export function GrowthScrubber({
  timeline,
  index,
  onIndexChange,
  language,
}: {
  timeline: ChronicleTimelinePoint[];
  index: number;
  onIndexChange: (next: number) => void;
  language: Language;
}) {
  const [dragTrack, setDragTrack] = React.useState<HTMLElement | null>(null);
  const maxIndex = Math.max(timeline.length - 1, 0);
  const point = timeline[index];
  const paths = React.useMemo(() => growthPaths(timeline, VIEW_WIDTH, VIEW_HEIGHT), [timeline]);
  const position = maxIndex > 0 ? (index / maxIndex) * 100 : 100;

  const selectAt = React.useCallback((clientX: number, track: HTMLElement) => {
    const rect = track.getBoundingClientRect();
    if (rect.width <= 0) return;
    const ratio = (clientX - rect.left) / rect.width;
    const clamped = Math.min(Math.max(ratio, 0), 1);
    onIndexChange(Math.round(clamped * maxIndex));
  }, [maxIndex, onIndexChange]);

  React.useEffect(() => {
    if (!dragTrack) return;
    const move = (event: PointerEvent) => selectAt(event.clientX, dragTrack);
    const stop = () => setDragTrack(null);
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
    window.addEventListener("pointercancel", stop);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
    };
  }, [dragTrack, selectAt]);

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const next = nextIndexFor(event.key, index, maxIndex);
    if (next === null) return;
    event.preventDefault();
    onIndexChange(next);
  };

  const valueText = point
    ? t(language, "chronicle.growth.valueText", {
      date: point.date,
      sources: fmtNumber(point.sources),
      entities: fmtNumber(point.entities),
      connections: fmtNumber(point.connections),
      conversations: fmtNumber(point.conversations),
    })
    : t(language, "chronicle.growth.aria");

  return (
    <section className="chronicle-panel chronicle-growth" data-testid="chronicle-growth">
      <header className="chronicle-panel-head">
        <h2 className="chronicle-panel-title">{t(language, "chronicle.growth.title")}</h2>
        <p className="chronicle-panel-hint">{t(language, "chronicle.growth.hint")}</p>
      </header>

      <div className="chronicle-growth-plot">
        <svg
          className="chronicle-growth-chart"
          viewBox={`0 0 ${VIEW_WIDTH} ${VIEW_HEIGHT}`}
          preserveAspectRatio="none"
          aria-hidden="true"
        >
          <path className="chronicle-growth-area" d={paths.area} />
          <path className="chronicle-growth-line" d={paths.line} vectorEffect="non-scaling-stroke" />
        </svg>
        <div
          className={`chronicle-growth-track${dragTrack ? " is-dragging" : ""}`}
          data-testid="chronicle-scrubber"
          role="slider"
          tabIndex={0}
          aria-label={t(language, "chronicle.growth.aria")}
          aria-valuemin={0}
          aria-valuemax={maxIndex}
          aria-valuenow={index}
          aria-valuetext={valueText}
          onKeyDown={onKeyDown}
          onPointerDown={(event) => {
            setDragTrack(event.currentTarget);
            selectAt(event.clientX, event.currentTarget);
          }}
        >
          <span className="chronicle-growth-marker" style={{ left: `${position}%` }} aria-hidden="true" />
          <span className="chronicle-growth-handle" style={{ left: `${position}%` }} aria-hidden="true" />
        </div>
      </div>

      <div className="chronicle-growth-axis">
        <span>{t(language, "chronicle.growth.first")}</span>
        <strong data-testid="chronicle-scrubber-date">
          {point ? point.date : t(language, "chronicle.growth.latest")}
        </strong>
        <span>{t(language, "chronicle.growth.latest")}</span>
      </div>

      <dl className="chronicle-lane-row">
        <Lane labelKey="chronicle.lane.sources" value={point?.sources} language={language} />
        <Lane labelKey="chronicle.lane.entities" value={point?.entities} language={language} />
        <Lane labelKey="chronicle.lane.connections" value={point?.connections} language={language} />
        <Lane labelKey="chronicle.lane.conversations" value={point?.conversations} language={language} />
      </dl>
    </section>
  );
}

function Lane({ labelKey, value, language }: { labelKey: string; value?: number; language: Language }) {
  return (
    <div className="chronicle-lane">
      <dt>{t(language, labelKey)}</dt>
      <dd>{fmtNumber(value)}</dd>
    </div>
  );
}

export function nextIndexFor(key: string, index: number, maxIndex: number): number | null {
  const moves: Record<string, number> = {
    ArrowLeft: -1,
    ArrowDown: -1,
    ArrowRight: 1,
    ArrowUp: 1,
    PageDown: -WEEK,
    PageUp: WEEK,
  };
  if (key === "Home") return 0;
  if (key === "End") return maxIndex;
  const step = moves[key];
  if (step === undefined) return null;
  return Math.min(Math.max(index + step, 0), maxIndex);
}
