import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ChronicleSeriesPoint } from "@/api/client";
import { renderPage } from "@/test/renderPage";
import "@/i18n/chronicle";
import { buildTimeline } from "./chronicleModel";
import { GrowthScrubber, nextIndexFor } from "./GrowthScrubber";


function series(): ChronicleSeriesPoint[] {
  return [
    { date: "2026-06-01", sources: 2, entities: 1, connections: 0, conversations: 1 },
    { date: "2026-06-03", sources: 1, entities: 4, connections: 2, conversations: 0 },
    { date: "2026-06-05", sources: 0, entities: 2, connections: 3, conversations: 2 },
  ];
}

const TIMELINE = buildTimeline(series());

function render(index = TIMELINE.length - 1, timeline = TIMELINE) {
  const onIndexChange = vi.fn();
  const result = renderPage(
    <GrowthScrubber timeline={timeline} index={index} onIndexChange={onIndexChange} language="ko" />,
  );
  return { ...result, onIndexChange, slider: screen.getByTestId("chronicle-scrubber") };
}

function widen(element: HTMLElement, left = 0, width = 200) {
  vi.spyOn(element, "getBoundingClientRect").mockReturnValue({
    left, width, right: left + width, top: 0, bottom: 40, height: 40, x: left, y: 0,
    toJSON: () => ({}),
  } as DOMRect);
}

describe("GrowthScrubber", () => {
  it("names itself and reports the day it sits on", () => {
    const { slider } = render();
    expect(slider).toHaveAttribute("role", "slider");
    expect(slider).toHaveAttribute("aria-label", "시점 고르기");
    expect(slider).toHaveAttribute("aria-valuemin", "0");
    expect(slider).toHaveAttribute("aria-valuemax", "4");
    expect(slider).toHaveAttribute("aria-valuenow", "4");
    expect(slider.getAttribute("aria-valuetext")).toContain("2026-06-05");
    expect(slider.getAttribute("aria-valuetext")).toContain("자료 3");
    expect(screen.getByTestId("chronicle-scrubber-date")).toHaveTextContent("2026-06-05");
  });

  it("shows the cumulative lanes for the chosen day, not the whole history", () => {
    render(0);
    expect(screen.getByText("자료").nextElementSibling).toHaveTextContent("2");
    expect(screen.getByText("연결").nextElementSibling).toHaveTextContent("0");
  });

  it("moves a day per arrow key and jumps to the ends", () => {
    const { slider, onIndexChange } = render(2);

    fireEvent.keyDown(slider, { key: "ArrowLeft" });
    expect(onIndexChange).toHaveBeenLastCalledWith(1);
    fireEvent.keyDown(slider, { key: "ArrowRight" });
    expect(onIndexChange).toHaveBeenLastCalledWith(3);
    fireEvent.keyDown(slider, { key: "Home" });
    expect(onIndexChange).toHaveBeenLastCalledWith(0);
    fireEvent.keyDown(slider, { key: "End" });
    expect(onIndexChange).toHaveBeenLastCalledWith(4);
  });

  it("ignores keys that are not its own", () => {
    const { slider, onIndexChange } = render(2);
    fireEvent.keyDown(slider, { key: "a" });
    expect(onIndexChange).not.toHaveBeenCalled();
  });

  it("lands on the day under the pointer and keeps following a drag", () => {
    const { slider, onIndexChange } = render(4);
    widen(slider);

    fireEvent.pointerDown(slider, { clientX: 100 });
    expect(onIndexChange).toHaveBeenLastCalledWith(2);
    expect(slider.className).toContain("is-dragging");

    fireEvent.pointerMove(window, { clientX: 0 });
    expect(onIndexChange).toHaveBeenLastCalledWith(0);
    fireEvent.pointerMove(window, { clientX: 999 });
    expect(onIndexChange).toHaveBeenLastCalledWith(4);

    fireEvent.pointerUp(window);
    expect(slider.className).not.toContain("is-dragging");
    onIndexChange.mockClear();
    fireEvent.pointerMove(window, { clientX: 10 });
    expect(onIndexChange).not.toHaveBeenCalled();
  });

  it("gives up rather than moving to NaN when the chart has no width yet", () => {
    const { slider, onIndexChange } = render(4);
    fireEvent.pointerDown(slider, { clientX: 100 });
    expect(onIndexChange).not.toHaveBeenCalled();
  });

  it("survives a Brain with a single recorded day", () => {
    const single = buildTimeline([
      { date: "2026-06-01", sources: 1, entities: 0, connections: 0, conversations: 0 },
    ]);
    const { slider } = render(0, single);
    expect(slider).toHaveAttribute("aria-valuemax", "0");
    expect(screen.getByTestId("chronicle-growth").querySelector<HTMLElement>(".chronicle-growth-handle")?.style.left)
      .toBe("100%");
  });

  it("renders without a chosen point rather than throwing", () => {
    render(9);
    expect(screen.getByTestId("chronicle-scrubber-date")).toHaveTextContent("지금");
    expect(screen.getByTestId("chronicle-scrubber")).toHaveAttribute("aria-valuetext", "시점 고르기");
  });
});

describe("nextIndexFor", () => {
  it("maps every key it owns and refuses the rest", () => {
    expect(nextIndexFor("ArrowDown", 5, 10)).toBe(4);
    expect(nextIndexFor("ArrowUp", 5, 10)).toBe(6);
    expect(nextIndexFor("PageUp", 5, 10)).toBe(10);
    expect(nextIndexFor("PageDown", 5, 10)).toBe(0);
    expect(nextIndexFor("Enter", 5, 10)).toBeNull();
  });

  it("clamps at both ends instead of walking off the timeline", () => {
    expect(nextIndexFor("ArrowLeft", 0, 10)).toBe(0);
    expect(nextIndexFor("ArrowRight", 10, 10)).toBe(10);
  });
});
