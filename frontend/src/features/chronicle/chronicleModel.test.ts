import { describe, expect, it } from "vitest";

import type { ChronicleSeriesPoint } from "@/api/client";
import {
  buildHeatmap,
  buildTimeline,
  dayToMs,
  endOfDay,
  growthPaths,
  heatLevel,
  msToDay,
  weekdayOf,
} from "./chronicleModel";

/**
 * The chronicle's arithmetic, tested where it is provable.
 *
 * Two of these properties would be invisible on screen until someone noticed
 * the wrong thing months later: a sparse series charted index-by-index (a
 * two-year gap drawn as one step), and calendar days derived from the local
 * clock (a grid that shifts by one column depending on the machine's timezone).
 */

function point(date: string, values: Partial<ChronicleSeriesPoint> = {}): ChronicleSeriesPoint {
  return { date, sources: 0, entities: 0, connections: 0, conversations: 0, ...values };
}

describe("dayToMs / msToDay", () => {
  it("round-trips a calendar day through UTC midnight", () => {
    expect(msToDay(dayToMs("2026-06-06") as number)).toBe("2026-06-06");
  });

  it("refuses anything that is not YYYY-MM-DD", () => {
    expect(dayToMs("")).toBeNull();
    expect(dayToMs("2026-6-6")).toBeNull();
    expect(dayToMs("yesterday")).toBeNull();
  });

  it("refuses a well-shaped date that does not exist", () => {
    // `Date.UTC(2026, 1, 31)` happily rolls over into March. Charting a day
    // that never happened is worse than dropping the row.
    expect(dayToMs("2026-02-31")).toBeNull();
  });
});

describe("weekdayOf", () => {
  it("reads the weekday in UTC, so the grid never depends on the machine", () => {
    expect(weekdayOf("2026-06-07")).toBe(0); // Sunday
    expect(weekdayOf("2026-06-06")).toBe(6); // Saturday
  });

  it("answers Sunday for an unreadable day rather than throwing", () => {
    expect(weekdayOf("not-a-day")).toBe(0);
  });
});

describe("buildTimeline", () => {
  it("has nothing to say about a Brain with no recorded day", () => {
    expect(buildTimeline([])).toEqual([]);
  });

  it("fills the calendar between the first and last recorded day", () => {
    // Sparse input: two days, four apart. The chart needs all five.
    const timeline = buildTimeline([
      point("2026-06-01", { sources: 2 }),
      point("2026-06-05", { entities: 3 }),
    ]);

    expect(timeline.map((entry) => entry.date)).toEqual([
      "2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05",
    ]);
    // Cumulative, so the quiet middle holds its level instead of dropping.
    expect(timeline.map((entry) => entry.total)).toEqual([2, 2, 2, 2, 5]);
    expect(timeline.map((entry) => entry.daily)).toEqual([2, 0, 0, 0, 3]);
  });

  it("accumulates each lane separately", () => {
    const timeline = buildTimeline([
      point("2026-06-01", { sources: 1, entities: 2, connections: 3, conversations: 4 }),
      point("2026-06-02", { sources: 1, entities: 1, connections: 1, conversations: 1 }),
    ]);
    expect(timeline[1]).toMatchObject({
      sources: 2, entities: 3, connections: 4, conversations: 5, total: 14, daily: 4,
    });
  });

  it("sorts the series itself rather than trusting the order it arrived in", () => {
    const timeline = buildTimeline([point("2026-06-03"), point("2026-06-01")]);
    expect(timeline[0].date).toBe("2026-06-01");
    expect(timeline).toHaveLength(3);
  });

  it("drops a row whose date is unreadable instead of letting it set the span", () => {
    const timeline = buildTimeline([
      point("nonsense"),
      point("2026-06-01", { sources: 5 }),
    ]);
    expect(timeline).toHaveLength(1);
    expect(timeline[0].total).toBe(5);
  });

  it("counts a missing or non-numeric lane as nothing, never NaN", () => {
    const timeline = buildTimeline([
      { date: "2026-06-01", sources: "3", entities: undefined, connections: Number.NaN, conversations: 2 } as unknown as ChronicleSeriesPoint,
    ]);
    expect(timeline[0].total).toBe(2);
    expect(timeline[0].daily).toBe(2);
  });
});

describe("heatLevel", () => {
  it("gives a quiet day no colour at all", () => {
    expect(heatLevel(0, 10)).toBe(0);
    expect(heatLevel(-1, 10)).toBe(0);
  });

  it("scales against the busiest day so any Brain gets a readable spread", () => {
    expect(heatLevel(10, 10)).toBe(4);
    expect(heatLevel(5, 10)).toBe(3);
    expect(heatLevel(2, 10)).toBe(2);
    expect(heatLevel(1, 10)).toBe(1);
  });

  it("still shows something when the peak is unknown", () => {
    expect(heatLevel(3, 0)).toBe(1);
  });
});

describe("buildHeatmap", () => {
  it("has no grid for an empty timeline", () => {
    expect(buildHeatmap([])).toEqual([]);
  });

  it("pads to whole weeks and leaves the padding without a date", () => {
    // 2026-06-03 is a Wednesday, so Sunday..Tuesday before it are padding.
    const weeks = buildHeatmap(buildTimeline([point("2026-06-03", { sources: 1 })]));
    expect(weeks).toHaveLength(1);
    expect(weeks[0].key).toBe("2026-05-31");
    expect(weeks[0].cells.slice(0, 3)).toEqual([null, null, null]);
    expect(weeks[0].cells[3]).toEqual({ date: "2026-06-03", daily: 1, level: 4 });
    expect(weeks[0].cells.slice(4)).toEqual([null, null, null]);
  });

  it("spans several weeks, one column each", () => {
    const weeks = buildHeatmap(buildTimeline([
      point("2026-06-01", { sources: 1 }),
      point("2026-06-20", { sources: 4 }),
    ]));
    // 2026-06-01 is a Monday and 2026-06-20 a Saturday, so the grid runs from
    // Sunday 05-31 through 06-20: three whole columns.
    expect(weeks.map((week) => week.key)).toEqual(["2026-05-31", "2026-06-07", "2026-06-14"]);
    for (const week of weeks) expect(week.cells).toHaveLength(7);
  });
});

describe("growthPaths", () => {
  it("draws nothing for an empty timeline", () => {
    expect(growthPaths([], 100, 50)).toEqual({ area: "", line: "" });
  });

  it("draws a step, not a slope, between two recorded days", () => {
    const { line, area } = growthPaths(
      buildTimeline([point("2026-06-01", { sources: 1 }), point("2026-06-02", { sources: 1 })]),
      100,
      50,
    );
    // Horizontal first, then vertical: the Brain did not learn half a document
    // overnight, and a diagonal would claim it did.
    expect(line).toBe("M 0 25 L 100 25 L 100 0");
    expect(area).toContain("L 100 50 L 0 50 Z");
  });

  it("keeps a flat line on the baseline when nothing was ever counted", () => {
    const { line } = growthPaths(buildTimeline([point("2026-06-01")]), 100, 50);
    expect(line).toBe("M 0 50");
  });
});

describe("endOfDay", () => {
  it("asks about the day's last second, with no offset the store never wrote", () => {
    expect(endOfDay("2026-06-06")).toBe("2026-06-06T23:59:59");
  });
});
