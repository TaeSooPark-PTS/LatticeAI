import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { fail, ok, renderPage, type RenderPageOptions } from "@/test/renderPage";
import { RunsListPanel } from "./RunsListPanel";

const runs = (count: number, status = "ok") =>
  Array.from({ length: count }, (_, index) => ({ run_id: `${status}-${index}`, status, name: `${status} run ${index}` }));

const render = (api: RenderPageOptions["api"]) =>
  renderPage(<RunsListPanel />, {
    language: "en",
    api: { agentRuntime: ok({ runs: [] }), workflowRuns: ok({ runs: [] }), ...api },
  });

describe("RunsListPanel history", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("filters by status, shows more, and resets the page on a new filter", async () => {
    render({ activityRuns: ok({ runs: [...runs(25), ...runs(2, "failed"), { run_id: "n-1", name: "no status" }] }) });
    expect(await screen.findByText("Showing 10 of 28")).toBeTruthy();
    expect(latticeApi.activityRuns).toHaveBeenCalledWith(100);
    await userEvent.click(screen.getByRole("button", { name: "Show more" }));
    expect(screen.getByText("Showing 20 of 28")).toBeTruthy();

    const select = screen.getByRole("combobox", { name: "Status" });
    await userEvent.selectOptions(select, "failed");
    expect(screen.getByText("Showing 2 of 2")).toBeTruthy();
    expect(screen.queryByText("ok run 0")).toBeNull();
    await userEvent.selectOptions(select, "approval");
    expect(screen.getByText("No runs with this status.")).toBeTruthy();
    await userEvent.selectOptions(select, "all");
    expect(screen.getByText("Showing 10 of 28")).toBeTruthy();
  });

  it("lists cancelled runs under their own status", async () => {
    render({ activityRuns: ok({ runs: [...runs(3), ...runs(2, "cancelled")] }) });
    expect(await screen.findByText("Showing 5 of 5")).toBeTruthy();

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Status" }), "Cancelled");
    expect(screen.getByText("Showing 2 of 2")).toBeTruthy();
    expect(screen.getByText("cancelled run 0")).toBeTruthy();
    expect(screen.queryByText("ok run 0")).toBeNull();
  });

  it("says when only the latest hundred runs were loaded", async () => {
    render({ activityRuns: ok({ runs: runs(100) }) });
    expect(await screen.findByText("Only the latest 100 runs are loaded.")).toBeTruthy();
  });

  it("keeps the activity failure visible above the substitute list and retries", async () => {
    render({
      activityRuns: fail("history database locked", { runs: [] }),
      agentRuntime: ok({ runs: [{ run_id: "a-1", status: "ok", goal: "substitute run" }] }),
    });
    expect(await screen.findByText("substitute run")).toBeTruthy();
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("history database locked");
    expect(alert.textContent).toContain("may be incomplete");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(latticeApi.activityRuns).toHaveBeenCalledTimes(2));
  });

  it("does not claim an empty history when every source failed", async () => {
    render({
      activityRuns: fail("", { runs: [] }),
      agentRuntime: fail("runtime down", {}),
    });
    await waitFor(() => expect(screen.getByText("Some runs could not be loaded: runtime down")).toBeTruthy());
    expect(screen.queryByRole("combobox", { name: "Status" })).toBeNull();
    expect(screen.getAllByRole("alert")[0]!.textContent).not.toContain("may be incomplete");
  });

  it("reports a failed substitute source even when the activity feed is merely empty", async () => {
    render({ activityRuns: ok({ runs: [] }), workflowRuns: fail("", {}) });
    expect(await screen.findByText(/Some runs could not be loaded/)).toBeTruthy();
  });

  it("shows loading until the history answers", () => {
    render({ activityRuns: () => new Promise(() => {}) });
    expect(screen.getAllByRole("status").length).toBeGreaterThan(0);
  });
});
