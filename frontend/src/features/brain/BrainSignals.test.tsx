import type * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { IngestionJobsPanel, VectorFreshnessNotice } from "./BrainSignals";

function renderWithQuery(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

describe("VectorFreshnessNotice", () => {
  it("shows a soft pending chip with the waiting count", async () => {
    vi.spyOn(latticeApi, "brainVectorFreshness").mockResolvedValue({
      ok: true, status: 200, source: "live",
      data: { status: "pending", pending_items: 12, total_items: 80, detail: "reindex scheduled" },
    } as never);

    renderWithQuery(<VectorFreshnessNotice language="ko" />);
    const chip = await screen.findByTestId("vector-freshness-chip");
    expect(chip.textContent).toContain("12");
    expect(chip.getAttribute("role")).toBe("status");
  });

  it("stays silent when the endpoint is unavailable", async () => {
    vi.spyOn(latticeApi, "brainVectorFreshness").mockResolvedValue({
      ok: false, status: 404, source: "unavailable",
      data: { status: "unavailable", pending_items: 0, total_items: 0, detail: "" },
    } as never);

    renderWithQuery(<VectorFreshnessNotice language="ko" />);
    await Promise.resolve();
    expect(screen.queryByTestId("vector-freshness-chip")).toBeNull();
  });
});

describe("IngestionJobsPanel", () => {
  it("shows progress for running jobs and resumes failed ones", async () => {
    vi.spyOn(latticeApi, "ingestionJobs").mockResolvedValue({
      ok: true, status: 200, source: "live",
      data: { jobs: [
        { job_id: "job-run", status: "running", total: 40, processed: 10, failed: 0, errors: [] },
        { job_id: "job-part", status: "partial", total: 20, processed: 15, failed: 5, errors: ["a.pdf"] },
      ] },
    } as never);
    const resumeSpy = vi.spyOn(latticeApi, "resumeIngestionJob").mockResolvedValue({
      ok: true, status: 200, source: "live", data: {},
    } as never);

    renderWithQuery(<IngestionJobsPanel language="ko" />);
    await screen.findByTestId("ingestion-jobs-panel");
    expect(screen.getByText("10/40 처리")).toBeTruthy();
    expect(screen.getByText("실패 5건")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: /이어서 처리/ }));
    expect(resumeSpy).toHaveBeenCalledWith("job-part");
  });

  it("renders nothing when the jobs endpoint is missing", async () => {
    vi.spyOn(latticeApi, "ingestionJobs").mockResolvedValue({
      ok: false, status: 404, source: "unavailable", data: { jobs: [] },
    } as never);

    renderWithQuery(<IngestionJobsPanel language="ko" />);
    await Promise.resolve();
    expect(screen.queryByTestId("ingestion-jobs-panel")).toBeNull();
  });
});
