import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ChronicleAsOf } from "@/api/client";
import { renderPage } from "@/test/renderPage";
import "@/i18n/chronicle";
import { RewindPanel } from "./RewindPanel";

/**
 * The rewind panel prints a number that looks like it contradicts the curve
 * above it — `stats.entities` counts every node in the slice, documents
 * included, while the growth curve's "개념" lane counts concepts only. The note
 * saying so is the whole reason a reader can trust both, so it is asserted
 * here rather than left to a screenshot.
 */

const AS_OF: ChronicleAsOf = {
  ts: "2026-06-03T23:59:59",
  stats: { entities: 148, connections: 96 },
  top_entities: [
    { id: "entity:lattice", label: "Lattice Workspace", type: "Topic", importance_score: 14 },
    { id: "entity:memory", label: "개인 기억", type: "Concept", importance_score: 11 },
  ],
};

function render(asOf: ChronicleAsOf | null = AS_OF, loading = false) {
  const onReset = vi.fn();
  const result = renderPage(
    <RewindPanel asOf={asOf} date="2026-06-03" loading={loading} onReset={onReset} language="ko" />,
  );
  return { ...result, onReset };
}

describe("RewindPanel", () => {
  it("says which moment it is describing", () => {
    render();
    expect(screen.getByText("2026-06-03에 두뇌가 알고 있던 것")).toBeTruthy();
    expect(screen.getByTestId("chronicle-rewind-entities")).toHaveTextContent("148");
  });

  it("explains that its count is a different measure from the curve's", () => {
    render();
    expect(screen.getByText(/세는 방식이 달라요/)).toBeTruthy();
  });

  it("lists what mattered then, translated out of its schema word", () => {
    render();
    expect(screen.getByRole("button", { name: /Lattice Workspace/ })).toBeTruthy();
    expect(screen.getByText("주제")).toBeTruthy();
  });

  it("opens the map when one of those ideas is chosen", async () => {
    render();
    window.location.hash = "";
    await userEvent.click(screen.getByRole("button", { name: /개인 기억/ }));
    expect(window.location.hash).toBe("#/knowledge-graph");
  });

  it("offers a way back to now, and calls it that", async () => {
    const { onReset } = render();
    await userEvent.click(screen.getByRole("button", { name: "지금으로 돌아오기" }));
    expect(onReset).toHaveBeenCalled();
  });

  it("says the Brain held nothing notable rather than showing an empty list", () => {
    render({ ...AS_OF, top_entities: [] });
    expect(screen.getByText("그때는 아직 눈에 띄는 개념이 없었어요.")).toBeTruthy();
  });

  it("says it is loading rather than reporting zero of everything", () => {
    render(AS_OF, true);
    expect(screen.getByRole("status")).toHaveTextContent("그때 모습을 불러오는 중입니다.");
    expect(screen.queryByTestId("chronicle-rewind-entities")).toBeNull();
    render(null);
    expect(screen.getAllByRole("status")[0]).toBeTruthy();
  });
});
