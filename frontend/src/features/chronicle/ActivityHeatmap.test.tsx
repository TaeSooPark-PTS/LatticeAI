import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ChronicleSeriesPoint } from "@/api/client";
import { renderPage } from "@/test/renderPage";
import "@/i18n/chronicle";
import { ActivityHeatmap } from "./ActivityHeatmap";
import { buildHeatmap, buildTimeline } from "./chronicleModel";

/**
 * The grid is the second way to choose a day, so its cells have to be reachable
 * without a mouse and have to say which date they are. A coloured `div` would
 * look identical in a screenshot and be invisible to everything else.
 */

const SERIES: ChronicleSeriesPoint[] = [
  { date: "2026-06-01", sources: 1, entities: 0, connections: 0, conversations: 0 },
  { date: "2026-06-03", sources: 4, entities: 4, connections: 2, conversations: 0 },
  { date: "2026-06-05", sources: 0, entities: 0, connections: 0, conversations: 0 },
];

const WEEKS = buildHeatmap(buildTimeline(SERIES));

function render(selectedDate = "2026-06-03") {
  const onSelect = vi.fn();
  const result = renderPage(
    <ActivityHeatmap weeks={WEEKS} selectedDate={selectedDate} onSelect={onSelect} language="ko" />,
  );
  return { ...result, onSelect };
}

describe("ActivityHeatmap", () => {
  it("renders one button per recorded day and nothing for the padding", () => {
    render();
    const cells = screen.getAllByTestId("chronicle-heatmap-cell");
    // Five calendar days between 06-01 and 06-05; the rest of the week is padding.
    expect(cells).toHaveLength(5);
    expect(screen.getByRole("group", { name: "날짜별 활동" })).toBeTruthy();
  });

  it("names each cell with its date and what landed on it", () => {
    render();
    expect(screen.getByRole("button", { name: "2026-06-03, 10가지" })).toBeTruthy();
    // A day inside the span with nothing on it says so rather than going unnamed.
    expect(screen.getByRole("button", { name: "2026-06-05, 쌓인 것 없음" })).toBeTruthy();
  });

  it("marks the chosen day as pressed, and only that one", () => {
    render("2026-06-01");
    expect(screen.getByRole("button", { name: /2026-06-01/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /2026-06-03/ })).toHaveAttribute("aria-pressed", "false");
  });

  it("hands the date back when a cell is chosen", async () => {
    const { onSelect } = render();
    await userEvent.click(screen.getByRole("button", { name: /2026-06-01/ }));
    expect(onSelect).toHaveBeenCalledWith("2026-06-01");
  });

  it("shades the busiest day darkest and a quiet one not at all", () => {
    render();
    expect(screen.getByRole("button", { name: /2026-06-03/ })).toHaveAttribute("data-level", "4");
    expect(screen.getByRole("button", { name: /2026-06-05/ })).toHaveAttribute("data-level", "0");
  });

  it("carries a legend so the shading is readable without a tooltip", () => {
    render();
    expect(screen.getByText("적음")).toBeTruthy();
    expect(screen.getByText("많음")).toBeTruthy();
  });

  it("shows every weekday label in the reader's language", () => {
    render();
    for (const label of ["일", "월", "화", "수", "목", "금", "토"]) {
      expect(screen.getByText(label)).toBeTruthy();
    }
  });
});
