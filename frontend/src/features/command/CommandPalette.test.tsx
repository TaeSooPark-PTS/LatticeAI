import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { CommandPalette } from "./CommandPalette";
import { DailyBriefingPanel, revealBriefingPanel } from "./DailyBriefingPanel";

function renderWithClient(node: React.ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>);
}

beforeEach(() => {
  window.location.hash = "";
});

afterEach(() => {
  // The briefing panel test installs one; jsdom ships no scrolling of its own.
  delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView;
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

  it("opens from the app-wide open-command event as well as the shortcut", async () => {
    mockBriefing({ sections: {}, quick_actions: [] }, false);
    renderWithClient(<CommandPalette language="ko" />);
    fireEvent(window, new Event("lattice:open-command"));
    expect(await screen.findByTestId("command-palette")).toBeTruthy();
  });

  it("still offers the proactive actions when the briefing carries no numbers", async () => {
    // An `ok` envelope with nothing in it — a brand new install. The section
    // appears, but with no counts invented under the labels.
    mockBriefing(null as never);
    renderWithClient(<CommandPalette language="ko" initialOpen />);

    await screen.findByText("지금 바로");
    expect(screen.getByText("오늘의 브리핑 열기")).toBeTruthy();
    expect(screen.getByText("검토 대기 항목 보기")).toBeTruthy();
    expect(screen.queryByText(/오늘의 제안/)).toBeNull();
    expect(screen.queryByText(/대기 중/)).toBeNull();
  });

  it("moves the selection with the arrow keys and opens it with Enter", async () => {
    mockBriefing({
      sections: { review: { pending: 3 }, suggestions: { count: 2 } },
      quick_actions: [],
    });
    const opened = vi.fn();
    window.addEventListener("lattice:open-briefing", opened);
    renderWithClient(<CommandPalette language="ko" initialOpen />);
    await screen.findByText("지금 바로");

    const input = screen.getByRole("textbox");
    expect(screen.getAllByRole("option")[0].getAttribute("aria-selected")).toBe("true");

    await userEvent.type(input, "{ArrowDown}");
    expect(screen.getAllByRole("option")[1].getAttribute("aria-selected")).toBe("true");
    await userEvent.type(input, "{ArrowUp}");
    expect(screen.getAllByRole("option")[0].getAttribute("aria-selected")).toBe("true");

    await userEvent.type(input, "{Enter}");
    // The briefing entry navigates *and* asks the panel to expand itself.
    expect(window.location.hash).toBe("#/brain");
    expect(opened).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("command-palette")).toBeNull();
    window.removeEventListener("lattice:open-briefing", opened);
  });

  it("says when nothing matched, and Enter on an empty list does nothing", async () => {
    mockBriefing({ sections: {}, quick_actions: [] }, false);
    vi.spyOn(latticeApi, "commandSearch").mockResolvedValue({
      ok: true, status: 200, source: "api", data: { query: "zzz", total: 0, groups: [] },
    } as never);

    renderWithClient(<CommandPalette language="ko" initialOpen />);
    const input = screen.getByRole("textbox");
    await userEvent.type(input, "zzzzz");
    await screen.findByText("일치하는 항목이 없습니다");

    await userEvent.type(input, "{Enter}");
    expect(window.location.hash).toBe("");
    expect(screen.getByTestId("command-palette")).toBeTruthy();
  });

  it("renders every hit kind it knows and silently skips the ones it does not", async () => {
    mockBriefing({ sections: {}, quick_actions: [] }, false);
    vi.spyOn(latticeApi, "commandSearch").mockResolvedValue({
      ok: true,
      status: 200,
      source: "api",
      data: {
        query: "회의",
        total: 6,
        groups: [
          // A knowledge hit with nothing but its own existence.
          { kind: "knowledge", items: [{}] },
          {
            kind: "conversation",
            items: [
              { conversation_id: "c1", snippet: "회의 요약해줘", timestamp: "2026-06-22T12:00:00Z" },
              {},
            ],
          },
          { kind: "automation", items: [{ id: "w1", name: "회의록 정리", enabled: true }, {}] },
          // A group kind this build does not render yet.
          { kind: "files", items: [{ id: "f1", name: "회의록.md" }] },
        ],
      },
    } as never);

    renderWithClient(<CommandPalette language="ko" initialOpen />);
    await userEvent.type(screen.getByRole("textbox"), "회의");

    await screen.findByText("회의 요약해줘");
    expect(screen.getByText("2026-06-22")).toBeTruthy(); // timestamp trimmed to a date
    expect(screen.getByText("지난 대화")).toBeTruthy();
    expect(screen.getByText("회의록 정리")).toBeTruthy();
    expect(screen.getByText("켜짐")).toBeTruthy();
    expect(screen.getByText("초안")).toBeTruthy(); // the automation with no `enabled`
    // The unknown kind contributes no option at all rather than an empty row.
    expect(screen.queryByText("회의록.md")).toBeNull();
  });

  it("navigates to a conversation hit", async () => {
    mockBriefing({ sections: {}, quick_actions: [] }, false);
    vi.spyOn(latticeApi, "commandSearch").mockResolvedValue({
      ok: true,
      status: 200,
      source: "api",
      data: {
        query: "회의",
        total: 1,
        groups: [{ kind: "conversation", items: [{ conversation_id: "c1", snippet: "회의 요약" }] }],
      },
    } as never);

    renderWithClient(<CommandPalette language="ko" initialOpen />);
    await userEvent.type(screen.getByRole("textbox"), "회의");
    await userEvent.click(await screen.findByText("회의 요약"));
    expect(window.location.hash).toBe("#/brain");
  });

  it("closes when the backdrop is clicked, but not when the dialog itself is", async () => {
    mockBriefing({ sections: {}, quick_actions: [] }, false);
    const { container } = renderWithClient(<CommandPalette language="ko" initialOpen />);

    await userEvent.click(screen.getByTestId("command-palette"));
    expect(screen.getByTestId("command-palette")).toBeTruthy();

    await userEvent.click(container.querySelector(".command-palette-backdrop") as HTMLElement);
    expect(screen.queryByTestId("command-palette")).toBeNull();
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

  it("shows zeros and a dash rather than blanks when the sections are bare", async () => {
    // One section present keeps this out of the "empty briefing" path, so the
    // stat grid renders with nothing behind it — the case that used to print
    // "undefined/undefined".
    mockBriefing({ sections: { knowledge: {} } });
    renderWithClient(<DailyBriefingPanel language="ko" variant="home" />);

    await screen.findByText("질문");
    expect(screen.getAllByText("0").length).toBeGreaterThan(0);
    expect(screen.getByText("0/0")).toBeTruthy();
    expect(screen.getByText("—")).toBeTruthy(); // no health grade to show
    expect(screen.queryByText("최근 담긴 지식")).toBeNull();
    expect(screen.queryByText(/자동화 제안/)).toBeNull();
  });

  it("names a recent note by its id when it has no title, and stays quiet when it has neither", async () => {
    mockBriefing({
      sections: { knowledge: { available: true, recent: [{ id: "note-7" }, {}] } },
      quick_actions: [],
    });
    const { container } = renderWithClient(<DailyBriefingPanel language="ko" variant="home" />);
    const entry = await screen.findByText("note-7");
    expect(entry.getAttribute("title")).toBe("");
    // The nameless one still occupies a row rather than printing "undefined".
    const rows = container.querySelectorAll(".daily-briefing-recent li");
    expect(rows).toHaveLength(2);
    expect(rows[1].textContent).toBe("");
  });

  it("drops a quick action whose kind this build has no words for", async () => {
    mockBriefing({
      sections: { review: { pending: 1 } },
      quick_actions: [
        { id: "teleport", kind: "teleport", count: 4, target: "/nowhere" },
        { id: "connect-knowledge", kind: "knowledge", target: "/brain/capture" },
      ],
    });
    renderWithClient(<DailyBriefingPanel language="ko" variant="home" />);

    // The known action renders even with no count of its own…
    await screen.findByText("지식 폴더 연결하기");
    // …and the unknown one contributes no button rather than a raw key.
    expect(screen.queryByText(/teleport/)).toBeNull();
  });

  it("expands, opens its drawer and scrolls into view when the palette asks", async () => {
    const scrollIntoView = vi.fn();
    // jsdom implements no scrolling at all; the component guards with `?.`.
    Element.prototype.scrollIntoView = scrollIntoView;
    mockBriefing({ sections: { conversations: { questions: 4 } }, quick_actions: [] });

    renderWithClient(
      <details>
        <summary>더 보기</summary>
        <DailyBriefingPanel language="ko" />
      </details>,
    );
    expect(screen.queryByText("질문")).toBeNull(); // starts collapsed

    fireEvent(window, new Event("lattice:open-briefing"));

    await screen.findByText("질문");
    expect(document.querySelector("details")?.getAttribute("open")).toBe("");
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalled());
  });

  it("collapses again when its own summary is clicked", async () => {
    mockBriefing({ sections: { conversations: { questions: 4 } }, quick_actions: [] });
    renderWithClient(<DailyBriefingPanel language="ko" variant="home" />);
    await screen.findByText("질문");
    await userEvent.click(screen.getByRole("button", { name: /오늘의 브리핑/ }));
    expect(screen.queryByText("질문")).toBeNull();
  });
});

describe("revealBriefingPanel", () => {
  it("does nothing when the panel is not on screen", () => {
    // The effect calls this with a ref that can legitimately be null.
    expect(() => revealBriefingPanel(null)).not.toThrow();
  });
});
