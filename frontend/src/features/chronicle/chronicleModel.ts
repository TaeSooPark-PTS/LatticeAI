import type { ChronicleSeriesPoint } from "@/api/client";

/**
 * The arithmetic behind the 연대기 screen, kept out of the components.
 *
 * Two properties are load-bearing and neither is visible in a screenshot:
 *
 *   1. **The server's series is sparse.** Only days that carried something are
 *      in it. Plotted index-by-index, a two-year gap would look like one step,
 *      so every function here works on the *calendar* between the first and the
 *      last recorded day. A quiet Tuesday is a real point with a zero, which is
 *      what makes "arrow key moves one day" true and the heat-map honest.
 *   2. **Dates are calendar days, not instants.** All arithmetic goes through
 *      `Date.UTC`, so a machine in Seoul, in São Paulo, or on the wrong side of
 *      a DST switch produces the same grid. The strings never leave `YYYY-MM-DD`.
 */

const DAY_MS = 86_400_000;
const DAY_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/;

export type ChronicleTimelinePoint = {
  date: string;
  /** Cumulative totals up to and including this day. */
  sources: number;
  entities: number;
  connections: number;
  conversations: number;
  /** Everything remembered up to this day, across the four lanes. */
  total: number;
  /** What arrived on this day alone — 0 on a quiet day. */
  daily: number;
};

export type HeatmapCell = { date: string; daily: number; level: number };
export type HeatmapWeek = { key: string; cells: Array<HeatmapCell | null> };

/** Midnight UTC for a `YYYY-MM-DD` string, or `null` when it is not one. */
export function dayToMs(date: string): number | null {
  const match = DAY_PATTERN.exec(date);
  if (!match) return null;
  const ms = Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  // `2026-02-31` matches the shape and rolls over to March; refuse it rather
  // than silently charting a day that does not exist.
  return msToDay(ms) === date ? ms : null;
}

export function msToDay(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

/** Weekday index, 0 = Sunday, for a calendar day. */
export function weekdayOf(date: string): number {
  const ms = dayToMs(date);
  return ms === null ? 0 : new Date(ms).getUTCDay();
}

/**
 * One point per calendar day between the first and the last recorded day.
 *
 * Rows whose `date` the server never promises to be well-formed are dropped
 * rather than trusted — a single unparsable string would otherwise decide the
 * span of the whole chart.
 */
export function buildTimeline(series: ChronicleSeriesPoint[]): ChronicleTimelinePoint[] {
  const byDate = new Map<string, ChronicleSeriesPoint>();
  for (const point of series) {
    // `dayToMs` is the only validation: a row whose date is missing, mis-shaped
    // or impossible produces `null` there, so there is no second guard here
    // that no payload could ever reach.
    const ms = dayToMs(point.date);
    if (ms !== null) byDate.set(msToDay(ms), point);
  }
  const days = [...byDate.keys()].sort();
  if (!days.length) return [];

  const start = dayToMs(days[0]) as number;
  const end = dayToMs(days[days.length - 1]) as number;
  const timeline: ChronicleTimelinePoint[] = [];
  let sources = 0;
  let entities = 0;
  let connections = 0;
  let conversations = 0;
  for (let ms = start; ms <= end; ms += DAY_MS) {
    const date = msToDay(ms);
    const point = byDate.get(date);
    const daily = point
      ? count(point.sources) + count(point.entities) + count(point.connections) + count(point.conversations)
      : 0;
    if (point) {
      sources += count(point.sources);
      entities += count(point.entities);
      connections += count(point.connections);
      conversations += count(point.conversations);
    }
    timeline.push({
      date,
      sources,
      entities,
      connections,
      conversations,
      total: sources + entities + connections + conversations,
      daily,
    });
  }
  return timeline;
}

/** A lane value that is missing or not a number counts as nothing, never NaN. */
function count(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

/**
 * Five levels, so a heavy day and a quiet one are distinguishable at a glance.
 *
 * Thresholds are fractions of the busiest day rather than fixed counts: a Brain
 * that takes in three things a week and one that takes in three hundred both
 * get a readable spread.
 */
export function heatLevel(daily: number, busiest: number): number {
  if (daily <= 0) return 0;
  if (busiest <= 0) return 1;
  const share = daily / busiest;
  if (share > 0.66) return 4;
  if (share > 0.33) return 3;
  if (share > 0.12) return 2;
  return 1;
}

/**
 * A GitHub-style week × weekday grid covering the timeline.
 *
 * The grid is padded out to whole weeks — Sunday before the first day, Saturday
 * after the last — and the padding cells are `null` so the caller renders a
 * blank rather than a button for a date that has no meaning.
 */
export function buildHeatmap(timeline: ChronicleTimelinePoint[]): HeatmapWeek[] {
  if (!timeline.length) return [];
  const busiest = timeline.reduce((peak, point) => Math.max(peak, point.daily), 0);
  const first = dayToMs(timeline[0].date) as number;
  const last = dayToMs(timeline[timeline.length - 1].date) as number;
  const gridStart = first - weekdayOf(timeline[0].date) * DAY_MS;
  const gridEnd = last + (6 - weekdayOf(timeline[timeline.length - 1].date)) * DAY_MS;
  const daily = new Map(timeline.map((point) => [point.date, point.daily]));

  const weeks: HeatmapWeek[] = [];
  for (let weekStart = gridStart; weekStart <= gridEnd; weekStart += 7 * DAY_MS) {
    const cells: Array<HeatmapCell | null> = [];
    for (let offset = 0; offset < 7; offset += 1) {
      const ms = weekStart + offset * DAY_MS;
      const date = msToDay(ms);
      const value = daily.get(date);
      cells.push(value === undefined ? null : { date, daily: value, level: heatLevel(value, busiest) });
    }
    weeks.push({ key: msToDay(weekStart), cells });
  }
  return weeks;
}

/**
 * The cumulative area and its outline, as SVG path data.
 *
 * Drawn as a step function, not a smooth line: the Brain did not learn a third
 * of a document overnight, and interpolating between days would draw growth on
 * days when nothing happened.
 */
export function growthPaths(
  timeline: ChronicleTimelinePoint[],
  width: number,
  height: number,
): { area: string; line: string } {
  if (!timeline.length) return { area: "", line: "" };
  const peak = timeline[timeline.length - 1].total;
  const span = Math.max(timeline.length - 1, 1);
  const points = timeline.map((point, index) => {
    const x = (index / span) * width;
    const y = height - (peak > 0 ? (point.total / peak) * height : 0);
    return { x, y };
  });

  const steps: string[] = [`M ${round(points[0].x)} ${round(points[0].y)}`];
  for (let index = 1; index < points.length; index += 1) {
    steps.push(`L ${round(points[index].x)} ${round(points[index - 1].y)}`);
    steps.push(`L ${round(points[index].x)} ${round(points[index].y)}`);
  }
  const line = steps.join(" ");
  const closing = `L ${round(points[points.length - 1].x)} ${height} L ${round(points[0].x)} ${height} Z`;
  return { area: `${line} ${closing}`, line };
}

function round(value: number): number {
  return Math.round(value * 100) / 100;
}

/**
 * The instant to ask `as-of` about for a chosen day: its last second, local.
 *
 * The store writes naive local stamps, so this string carries no offset — one
 * with a `Z` would be shifted again server-side and answer for a different day.
 */
export function endOfDay(date: string): string {
  return `${date}T23:59:59`;
}
