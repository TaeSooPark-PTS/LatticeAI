import type { ChronicleSeriesPoint } from "@/api/client";


const DAY_MS = 86_400_000;
const DAY_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/;

export type ChronicleTimelinePoint = {
  date: string;
  sources: number;
  entities: number;
  connections: number;
  conversations: number;
  total: number;
  daily: number;
};

export type HeatmapCell = { date: string; daily: number; level: number };
export type HeatmapWeek = { key: string; cells: Array<HeatmapCell | null> };

export function dayToMs(date: string): number | null {
  const match = DAY_PATTERN.exec(date);
  if (!match) return null;
  const ms = Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  return msToDay(ms) === date ? ms : null;
}

export function msToDay(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

export function weekdayOf(date: string): number {
  const ms = dayToMs(date);
  return ms === null ? 0 : new Date(ms).getUTCDay();
}

export function buildTimeline(series: ChronicleSeriesPoint[]): ChronicleTimelinePoint[] {
  const byDate = new Map<string, ChronicleSeriesPoint>();
  for (const point of series) {
    const ms = dayToMs(point.date);
    if (ms !== null) byDate.set(msToDay(ms), point);
  }
  const days = [...byDate.keys()].sort();
  if (!days.length) return [];

  const start = dayToMs(days[0]!) as number;
  const end = dayToMs(days[days.length - 1]!) as number;
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

function count(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

export function heatLevel(daily: number, busiest: number): number {
  if (daily <= 0) return 0;
  if (busiest <= 0) return 1;
  const share = daily / busiest;
  if (share > 0.66) return 4;
  if (share > 0.33) return 3;
  if (share > 0.12) return 2;
  return 1;
}

export function buildHeatmap(timeline: ChronicleTimelinePoint[]): HeatmapWeek[] {
  if (!timeline.length) return [];
  const busiest = timeline.reduce((peak, point) => Math.max(peak, point.daily), 0);
  const firstPoint = timeline[0]!;
  const lastPoint = timeline[timeline.length - 1]!;
  const first = dayToMs(firstPoint.date) as number;
  const last = dayToMs(lastPoint.date) as number;
  const gridStart = first - weekdayOf(firstPoint.date) * DAY_MS;
  const gridEnd = last + (6 - weekdayOf(lastPoint.date)) * DAY_MS;
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

export function growthPaths(
  timeline: ChronicleTimelinePoint[],
  width: number,
  height: number,
): { area: string; line: string } {
  if (!timeline.length) return { area: "", line: "" };
  const peak = timeline[timeline.length - 1]!.total;
  const span = Math.max(timeline.length - 1, 1);
  const points = timeline.map((point, index) => {
    const x = (index / span) * width;
    const y = height - (peak > 0 ? (point.total / peak) * height : 0);
    return { x, y };
  });

  const firstPoint = points[0]!;
  const lastPoint = points[points.length - 1]!;
  const steps: string[] = [`M ${round(firstPoint.x)} ${round(firstPoint.y)}`];
  for (let index = 1; index < points.length; index += 1) {
    const current = points[index]!;
    const previous = points[index - 1]!;
    steps.push(`L ${round(current.x)} ${round(previous.y)}`);
    steps.push(`L ${round(current.x)} ${round(current.y)}`);
  }
  const line = steps.join(" ");
  const closing = `L ${round(lastPoint.x)} ${height} L ${round(firstPoint.x)} ${height} Z`;
  return { area: `${line} ${closing}`, line };
}

function round(value: number): number {
  return Math.round(value * 100) / 100;
}

export function endOfDay(date: string): string {
  return `${date}T23:59:59`;
}
