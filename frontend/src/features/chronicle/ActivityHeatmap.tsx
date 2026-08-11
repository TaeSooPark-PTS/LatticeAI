import * as React from "react";

import { t, type Language } from "@/i18n";
import { fmtNumber } from "@/lib/utils";
import type { HeatmapWeek } from "./chronicleModel";

const WEEKDAY_KEYS = [
  "chronicle.weekday.sun",
  "chronicle.weekday.mon",
  "chronicle.weekday.tue",
  "chronicle.weekday.wed",
  "chronicle.weekday.thu",
  "chronicle.weekday.fri",
  "chronicle.weekday.sat",
];

/**
 * Week × weekday density grid, the shape everyone already reads without a legend.
 *
 * Cells are buttons, not coloured divs: picking a day is the screen's second
 * navigation control, and a div would leave it unreachable by keyboard and
 * unnamed to a screen reader. Padding cells — the days before the first Sunday
 * and after the last Saturday — render as inert spans rather than disabled
 * buttons, so tabbing never stops on a date the Brain has no opinion about.
 */
export function ActivityHeatmap({
  weeks,
  selectedDate,
  onSelect,
  language,
}: {
  weeks: HeatmapWeek[];
  selectedDate: string;
  onSelect: (date: string) => void;
  language: Language;
}) {
  return (
    <section className="chronicle-panel chronicle-heatmap" data-testid="chronicle-heatmap">
      <header className="chronicle-panel-head">
        <h2 className="chronicle-panel-title">{t(language, "chronicle.heatmap.title")}</h2>
        <p className="chronicle-panel-hint">{t(language, "chronicle.heatmap.hint")}</p>
      </header>

      <div className="chronicle-heatmap-scroll">
        <div className="chronicle-heatmap-weekdays" aria-hidden="true">
          {WEEKDAY_KEYS.map((key) => (
            <span key={key}>{t(language, key)}</span>
          ))}
        </div>
        <div className="chronicle-heatmap-grid" role="group" aria-label={t(language, "chronicle.heatmap.aria")}>
          {weeks.map((week) => (
            <div className="chronicle-heatmap-week" key={week.key}>
              {week.cells.map((cell, offset) => (cell ? (
                <button
                  key={cell.date}
                  type="button"
                  className="chronicle-heatmap-cell"
                  data-level={cell.level}
                  data-testid="chronicle-heatmap-cell"
                  aria-pressed={cell.date === selectedDate}
                  aria-label={t(
                    language,
                    cell.daily > 0 ? "chronicle.heatmap.cell" : "chronicle.heatmap.cellEmpty",
                    { date: cell.date, count: fmtNumber(cell.daily) },
                  )}
                  onClick={() => onSelect(cell.date)}
                />
              ) : (
                <span key={`${week.key}-${offset}`} className="chronicle-heatmap-cell is-blank" aria-hidden="true" />
              )))}
            </div>
          ))}
        </div>
      </div>

      <p className="chronicle-heatmap-legend">
        <span>{t(language, "chronicle.heatmap.less")}</span>
        <span className="chronicle-heatmap-swatches" aria-hidden="true">
          {[0, 1, 2, 3, 4].map((level) => (
            <span key={level} className="chronicle-heatmap-cell" data-level={level} />
          ))}
        </span>
        <span>{t(language, "chronicle.heatmap.more")}</span>
      </p>
    </section>
  );
}
