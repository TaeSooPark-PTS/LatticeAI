import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { renderPage } from "@/test/renderPage";
import { BrainMemoryLayer } from "./BrainMemoryLayer";
import type { MemoryFragment } from "./types";

const fragment = (id: string, overrides: Partial<MemoryFragment> = {}): MemoryFragment => ({
  id,
  title: `기억 ${id}`,
  kind: "Note",
  tags: [],
  agentGenerated: false,
  ...overrides,
});

const many = (count: number) => Array.from({ length: count }, (_, index) => fragment(`m${index + 1}`));

describe("BrainMemoryLayer", () => {
  it("shows the quiet empty state when there are no memories", () => {
    const { container } = renderPage(
      <BrainMemoryLayer memories={[]} depth={2} onRecallMemory={() => {}} />,
    );
    const empty = container.querySelector(".memory-fragment.is-empty");
    expect(empty).toBeTruthy();
    expect(empty?.textContent).toContain("첫 기억");
    expect(empty?.textContent).toContain("기억이 조용합니다.");
  });

  it("caps shallow depth at 6 fragments on the tighter radius", () => {
    const { container } = renderPage(
      <BrainMemoryLayer memories={many(9)} depth={2} onRecallMemory={() => {}} />,
    );
    const buttons = container.querySelectorAll("button.memory-fragment");
    expect(buttons).toHaveLength(6);
    // Index 0 at the shallow radius: x = 50 + cos(-112°) * 31.
    const first = buttons[0] as HTMLElement;
    expect(first.style.getPropertyValue("--delay")).toBe("0ms");
    expect(first.style.getPropertyValue("--x")).toContain("%");
  });

  it("opens up to 8 fragments at depth 3 and recalls the clicked memory", async () => {
    const onRecall = vi.fn();
    const memories = many(9);
    const { container } = renderPage(
      <BrainMemoryLayer memories={memories} depth={3} onRecallMemory={onRecall} />,
    );
    expect(container.querySelectorAll("button.memory-fragment")).toHaveLength(8);
    expect(screen.getByText("기억 m3")).toBeTruthy();

    await userEvent.click(screen.getByText("기억 m1").closest("button") as HTMLButtonElement);
    expect(onRecall).toHaveBeenCalledWith(memories[0]);
  });
});
