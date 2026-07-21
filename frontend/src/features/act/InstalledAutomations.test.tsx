import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { InstalledAutomations } from "./InstalledAutomations";

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
});
