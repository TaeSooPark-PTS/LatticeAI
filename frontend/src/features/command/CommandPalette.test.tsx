import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { CommandPalette } from "./CommandPalette";
import { DailyBriefingPanel } from "./DailyBriefingPanel";

function renderWithClient(node: React.ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>);
}

beforeEach(() => {
  window.location.hash = "";
});

describe("CommandPalette", () => {
  it("opens with Cmd+K, searches all surfaces, and navigates on selection", async () => {
    vi.spyOn(latticeApi, "commandSearch").mockResolvedValue({
      ok: true,
      status: 200,
      source: "api",
      data: {
        query: "계약서",
        total: 2,
        groups: [
          { kind: "knowledge", items: [{ id: "n1", title: "계약서 초안", summary: "요약", type: "document" }] },
          { kind: "automation", items: [{ id: "w1", name: "계약서 digest", enabled: true }] },
        ],
      },
    } as never);

    renderWithClient(<CommandPalette language="ko" />);
    expect(screen.queryByTestId("command-palette")).toBeNull();

    await userEvent.keyboard("{Meta>}k{/Meta}");
    expect(screen.getByTestId("command-palette")).toBeTruthy();

    const input = screen.getByRole("textbox");
    await userEvent.type(input, "계약서");
    await screen.findByText("계약서 초안");
    await screen.findByText("계약서 digest");

    await userEvent.click(screen.getByText("계약서 초안"));
    expect(window.location.hash).toBe("#/brain/graph");
    expect(screen.queryByTestId("command-palette")).toBeNull();
  });

  it("filters navigation pages without a backend call and closes on Escape", async () => {
    const spy = vi.spyOn(latticeApi, "commandSearch");
    renderWithClient(<CommandPalette language="ko" />);
    await userEvent.keyboard("{Meta>}k{/Meta}");
    // all shell pages listed by default
    expect(screen.getAllByRole("option").length).toBeGreaterThanOrEqual(6);
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByTestId("command-palette")).toBeNull();
    expect(spy).not.toHaveBeenCalled();
  });
});

describe("DailyBriefingPanel", () => {
  it("shows stats and quick actions after expanding", async () => {
    vi.spyOn(latticeApi, "commandBriefing").mockResolvedValue({
      ok: true,
      status: 200,
      source: "api",
      data: {
        sections: {
          knowledge: { available: true, recent: [{ id: "n1", title: "회의록" }] },
          conversations: { available: true, questions: 4 },
          automations: { available: true, total: 3, enabled: 1, drafts: 2 },
          review: { available: true, pending: 5 },
          health: { available: true, grade: "B", score: 82 },
          suggestions: { available: true, count: 2, top: [] },
        },
        quick_actions: [
          { id: "review-pending", kind: "review", count: 5, target: "/act/review" },
        ],
      },
    } as never);

    renderWithClient(<DailyBriefingPanel language="ko" />);
    await userEvent.click(screen.getByRole("button", { name: /오늘의 브리핑/ }));

    await screen.findByText("회의록");
    expect(screen.getByText("5")).toBeTruthy();
    expect(screen.getByText("1/3")).toBeTruthy();

    await userEvent.click(screen.getByText(/검토 대기 5건/));
    expect(window.location.hash).toBe("#/act/review");
  });
});
