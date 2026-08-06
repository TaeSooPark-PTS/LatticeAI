import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { InstalledAutomations, shortWhen } from "./InstalledAutomations";

function renderWithClient(node: React.ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>);
}

function mockOverview(installed: Array<Record<string, unknown>>) {
  return vi.spyOn(latticeApi, "automationOverview").mockResolvedValue({
    ok: true,
    status: 200,
    source: "live",
    data: { suggestions: [], installed, questions_scanned: 0 },
  } as never);
}

describe("InstalledAutomations", () => {
  it("shows the last execution line for each installed automation", async () => {
    mockOverview([
      {
        id: "wf-1",
        name: "Daily Memory Digest",
        enabled: true,
        requires_user_enable: false,
        creates: [],
        last_execution: {
          mode: "live",
          status: "ok",
          summary: "ok — 3 step(s) recorded",
          run_id: "run-1",
          finished_at: "2026-07-21T10:00:00",
        },
      },
      {
        id: "wf-2",
        name: "Folder digest",
        enabled: false,
        requires_user_enable: true,
        creates: [],
        last_execution: null,
      },
    ]);

    renderWithClient(<InstalledAutomations language="ko" />);
    const cards = await screen.findAllByTestId("installed-automation-card");
    expect(cards).toHaveLength(2);
    const lines = screen.getAllByTestId("automation-last-execution");
    expect(lines[0].textContent).toContain("마지막 실행");
    expect(lines[0].textContent).toContain("ok — 3 step(s) recorded");
    expect(lines[1].textContent).toContain("아직 실행 기록이 없습니다");
  });

  it("dry-runs first and only then unlocks the real run", async () => {
    mockOverview([
      {
        id: "wf-1",
        name: "Daily Memory Digest",
        enabled: false,
        requires_user_enable: true,
        creates: [],
        last_execution: null,
      },
    ]);
    const runNow = vi.spyOn(latticeApi, "runAutomationNow").mockResolvedValue({
      ok: true,
      status: 200,
      source: "live",
      data: {
        workflow_id: "wf-1",
        dry_run: true,
        status: "ok",
        last_execution: { mode: "dry_run", status: "ok", summary: "2 step(s) would run", finished_at: "2026-07-21T10:00:00" },
      },
    } as never);

    renderWithClient(<InstalledAutomations language="ko" />);
    const runButton = await screen.findByRole("button", { name: /지금 한 번 실행/ });
    // Before any dry run the real-run button is not offered.
    expect(screen.queryByRole("button", { name: /실제로 한 번 실행/ })).toBeNull();

    await userEvent.click(runButton);
    expect(runNow).toHaveBeenCalledWith("wf-1", true);

    const liveButton = await screen.findByRole("button", { name: /실제로 한 번 실행/ });
    await userEvent.click(liveButton);
    expect(runNow).toHaveBeenLastCalledWith("wf-1", false);
  });

  it("shows an empty state without installed automations", async () => {
    mockOverview([]);
    renderWithClient(<InstalledAutomations language="en" />);
    await screen.findByText(/No automations installed yet/);
  });

  it("renders a sparse last execution without inventing a time or summary", async () => {
    mockOverview([
      {
        // No id at all: the card still renders rather than crashing on the key.
        name: "무명 자동화",
        enabled: false,
        requires_user_enable: true,
        creates: [],
        last_execution: { mode: "live" },
      },
    ]);
    renderWithClient(<InstalledAutomations language="ko" />);
    const line = await screen.findByTestId("automation-last-execution");
    expect(line.textContent).toContain("실제");
    // Absent status, finished_at and summary add nothing to the line.
    expect(line.textContent).not.toMatch(/undefined|—/);
    expect(screen.getByText("무명 자동화")).toBeTruthy();
  });

  it("names only the running card while a dry run and then a live run are in flight", async () => {
    mockOverview([
      { id: "wf-a", name: "A 자동화", enabled: true, requires_user_enable: false, creates: [], last_execution: null },
      { id: "wf-b", name: "B 자동화", enabled: true, requires_user_enable: false, creates: [], last_execution: null },
    ]);
    let resolve!: (value: unknown) => void;
    const runNow = vi.spyOn(latticeApi, "runAutomationNow").mockImplementation(
      () => new Promise((res) => { resolve = res; }) as never,
    );

    renderWithClient(<InstalledAutomations language="ko" />);
    const runButtons = await screen.findAllByRole("button", { name: /지금 한 번 실행/ });
    expect(runButtons).toHaveLength(2);
    await userEvent.click(runButtons[0]);

    // The clicked card reports the dry run; its sibling keeps its label but is locked.
    await screen.findByRole("button", { name: /모의 실행 중/ });
    const idle = screen.getByRole("button", { name: /지금 한 번 실행/ });
    expect((idle as HTMLButtonElement).disabled).toBe(true);
    resolve({
      ok: true,
      status: 200,
      source: "live",
      data: { workflow_id: "wf-a", dry_run: true, status: "ok", last_execution: { mode: "dry_run", status: "ok" } },
    });

    const liveButton = await screen.findByRole("button", { name: /실제로 한 번 실행/ });
    await userEvent.click(liveButton);
    await screen.findByRole("button", { name: /^실행 중/ });
    resolve({
      ok: true,
      status: 200,
      source: "live",
      data: { workflow_id: "wf-a", dry_run: false, status: "ok", last_execution: { mode: "live", status: "ok" } },
    });
    await waitFor(() => expect(screen.queryByRole("button", { name: /^실행 중/ })).toBeNull());
    expect(runNow).toHaveBeenLastCalledWith("wf-a", false);
  });

  it("a failed run does not unlock the live button", async () => {
    mockOverview([
      { id: "wf-1", name: "실패하는 자동화", enabled: true, requires_user_enable: false, creates: [], last_execution: null },
    ]);
    vi.spyOn(latticeApi, "runAutomationNow").mockResolvedValue({
      ok: false,
      status: 503,
      source: "unavailable",
      data: null,
      error: "server unavailable",
    } as never);

    renderWithClient(<InstalledAutomations language="ko" />);
    await userEvent.click(await screen.findByRole("button", { name: /지금 한 번 실행/ }));
    await waitFor(() => expect(latticeApi.runAutomationNow).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: /실제로 한 번 실행/ })).toBeNull();
  });

  it("points at the review inbox when a run failed into review", async () => {
    mockOverview([
      { id: "wf-1", name: "검토로 간 자동화", enabled: true, requires_user_enable: false, creates: [], last_execution: null },
    ]);
    vi.spyOn(latticeApi, "runAutomationNow").mockResolvedValue({
      ok: true,
      status: 200,
      source: "live",
      data: { workflow_id: "wf-1", dry_run: true, status: "failed", review_item_id: "rev-9" },
    } as never);

    renderWithClient(<InstalledAutomations language="ko" />);
    await userEvent.click(await screen.findByRole("button", { name: /지금 한 번 실행/ }));
    await screen.findByText("실행이 실패해 검토함에 추가했습니다.");
  });
});

describe("shortWhen", () => {
  it("shortens an ISO timestamp to date and minute", () => {
    expect(shortWhen("2026-07-21T10:00:00")).toBe("2026-07-21 10:00");
  });

  it("returns an empty string for a missing value", () => {
    // Every render site guards on truthiness first, so this contract is only
    // reachable directly — it keeps the helper safe for future callers.
    expect(shortWhen(undefined)).toBe("");
  });
});
