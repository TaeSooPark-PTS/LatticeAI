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

function mockBriefing(data: Record<string, unknown>, ok = true) {
  return vi.spyOn(latticeApi, "commandBriefing").mockResolvedValue({
    ok,
    status: ok ? 200 : 503,
    source: ok ? "api" : "unavailable",
    data,
  } as never);
}

describe("CommandPalette", () => {
  it("opens with Cmd+K, searches all surfaces, and navigates on selection", async () => {
    mockBriefing({ sections: {}, quick_actions: [] }, false);
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
    mockBriefing({ sections: {}, quick_actions: [] }, false);
    const spy = vi.spyOn(latticeApi, "commandSearch");
    renderWithClient(<CommandPalette language="ko" />);
    await userEvent.keyboard("{Meta>}k{/Meta}");
    // all shell pages listed by default
    expect(screen.getAllByRole("option").length).toBeGreaterThanOrEqual(6);
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByTestId("command-palette")).toBeNull();
    expect(spy).not.toHaveBeenCalled();
  });

  it("shows proactive quick actions on cold open and hides them once the user types", async () => {
    mockBriefing({
      sections: {
        review: { available: true, pending: 3 },
        suggestions: { available: true, count: 2, top: [] },
      },
      quick_actions: [],
    });

    renderWithClient(<CommandPalette language="ko" />);
    await userEvent.keyboard("{Meta>}k{/Meta}");

    await screen.findByText("지금 바로");
    expect(screen.getByText("오늘의 브리핑 열기")).toBeTruthy();
    expect(screen.getByText("오늘의 제안 2개")).toBeTruthy();
    expect(screen.getByText("3건 대기 중")).toBeTruthy();

    await userEvent.type(screen.getByRole("textbox"), "brain");
    expect(screen.queryByText("지금 바로")).toBeNull();

    await userEvent.clear(screen.getByRole("textbox"));
    await userEvent.click(await screen.findByText("검토 대기 항목 보기"));
    expect(window.location.hash).toBe("#/act/review");
  });

  it("hides the proactive section entirely when the briefing fetch fails", async () => {
    mockBriefing({ sections: {}, quick_actions: [] }, false);
    renderWithClient(<CommandPalette language="ko" />);
    await userEvent.keyboard("{Meta>}k{/Meta}");
    // pages still listed, but no proactive group appears
    expect(screen.getAllByRole("option").length).toBeGreaterThanOrEqual(6);
    expect(screen.queryByText("지금 바로")).toBeNull();
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

  it("fetches immediately in the home variant without any click", async () => {
    const spy = mockBriefing({
      sections: {
        knowledge: { available: true, recent: [{ id: "n1", title: "회의록" }] },
        conversations: { available: true, questions: 4 },
        automations: { available: true, total: 3, enabled: 1, drafts: 2 },
        review: { available: true, pending: 5 },
        health: { available: true, grade: "B", score: 82 },
        suggestions: { available: true, count: 0, top: [] },
      },
      quick_actions: [],
    });

    renderWithClient(<DailyBriefingPanel language="ko" variant="home" />);
    await screen.findByText("회의록");
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("degrades to a friendly one-liner when the briefing is unavailable", async () => {
    mockBriefing({ sections: {}, quick_actions: [] }, false);
    renderWithClient(<DailyBriefingPanel language="ko" variant="home" />);
    await screen.findByTestId("daily-briefing-empty");
    expect(screen.queryByText("질문")).toBeNull();
  });
});
