import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { ChronicleAsOf, ChronicleDay, ChronicleOverview } from "@/api/client";
import { fail, ok, renderPage } from "@/test/renderPage";
import { ChroniclePage } from "./Chronicle";

/**
 * 연대기 end to end, at the level a screenshot cannot reach: the empty Brain,
 * the failed read, and — the one that matters — what happens when the handle
 * moves. Every panel below the scrubber reads from a single index, so a
 * regression there does not throw; it just quietly shows today's story under a
 * past date.
 */

const OVERVIEW: ChronicleOverview = {
  first_activity_at: "2026-06-01T09:12:04",
  last_activity_at: "2026-06-05T18:40:11",
  totals: { sources: 3, entities: 7, connections: 5, conversations: 3 },
  series: [
    { date: "2026-06-01", sources: 2, entities: 1, connections: 0, conversations: 1 },
    { date: "2026-06-03", sources: 1, entities: 4, connections: 2, conversations: 0 },
    { date: "2026-06-05", sources: 0, entities: 2, connections: 3, conversations: 2 },
  ],
};

const DAY: ChronicleDay = {
  date: "2026-06-05",
  counts: { sources: 1, entities: 0, conversations: 0, changes: 0 },
  groups: {
    sources: [
      { id: "prov-1", title: "retrieval-design.pdf", source_type: "upload", captured_at: "2026-06-05T09:20:00", node_id: "file:retrieval" },
    ],
    entities: [],
    conversations: [],
    changes: [],
  },
};

const AS_OF: ChronicleAsOf = {
  ts: "2026-06-01T23:59:59",
  stats: { entities: 42, connections: 18 },
  top_entities: [{ id: "entity:memory", label: "개인 기억", type: "Concept", importance_score: 11 }],
};

function render(overrides = {}) {
  return renderPage(<ChroniclePage />, {
    mode: "basic",
    api: {
      chronicleOverview: ok(OVERVIEW),
      chronicleDay: ok(DAY),
      chronicleAsOf: ok(AS_OF),
      ...overrides,
    },
  });
}

describe("ChroniclePage", () => {
  it("renders the four stacked panels once there is a history", async () => {
    render();
    expect(await screen.findByTestId("chronicle-growth")).toBeTruthy();
    expect(screen.getByTestId("chronicle-heatmap")).toBeTruthy();
    await screen.findByTestId("chronicle-day");
    // The rewind panel belongs to the past only; the handle starts at the end.
    expect(screen.queryByTestId("chronicle-rewind")).toBeNull();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("두뇌가 자라온 시간");
  });

  it("opens on the most recent day", async () => {
    render();
    expect(await screen.findByTestId("chronicle-scrubber-date")).toHaveTextContent("2026-06-05");
  });

  it("moves every panel when the handle moves, and rewinds only in the past", async () => {
    render();
    const slider = await screen.findByTestId("chronicle-scrubber");

    await userEvent.type(slider, "{Home}");

    await waitFor(() => expect(screen.getByTestId("chronicle-scrubber-date")).toHaveTextContent("2026-06-01"));
    // Past ⇒ the as-of read runs and the rewind panel appears.
    expect(await screen.findByTestId("chronicle-rewind")).toBeTruthy();
    expect(await screen.findByTestId("chronicle-rewind-entities")).toHaveTextContent("42");

    await userEvent.click(screen.getByRole("button", { name: "지금으로 돌아오기" }));
    await waitFor(() => expect(screen.queryByTestId("chronicle-rewind")).toBeNull());
    expect(screen.getByTestId("chronicle-scrubber-date")).toHaveTextContent("2026-06-05");
  });

  it("lets a heat-map cell choose the day", async () => {
    render();
    await screen.findByTestId("chronicle-heatmap");
    await userEvent.click(screen.getByRole("button", { name: /2026-06-03/ }));
    await waitFor(() => expect(screen.getByTestId("chronicle-scrubber-date")).toHaveTextContent("2026-06-03"));
  });

  it("says the record starts today for a Brain that has seen nothing", async () => {
    render({
      chronicleOverview: ok({
        first_activity_at: null,
        last_activity_at: null,
        totals: { sources: 0, entities: 0, connections: 0, conversations: 0 },
        series: [],
      }),
    });
    expect(await screen.findByText("오늘부터 기록이 쌓입니다")).toBeTruthy();
    expect(screen.queryByTestId("chronicle-growth")).toBeNull();
  });

  it("says the read failed rather than showing an empty history", async () => {
    render({
      chronicleOverview: fail("연대기를 읽지 못했습니다.", {
        first_activity_at: null,
        last_activity_at: null,
        totals: { sources: 0, entities: 0, connections: 0, conversations: 0 },
        series: [],
      }),
    });
    // The distinction matters: "nothing recorded yet" and "we could not read
    // your history" are different facts and only one of them is reassuring.
    expect(await screen.findByText("연대기를 읽지 못했습니다.")).toBeTruthy();
    expect(screen.queryByText("오늘부터 기록이 쌓입니다")).toBeNull();
  });

  it("shows a loading panel while the history is still on its way", () => {
    render({ chronicleOverview: () => new Promise(() => {}) });
    expect(screen.getByText("연대기를 불러오는 중입니다.")).toBeTruthy();
  });
});
