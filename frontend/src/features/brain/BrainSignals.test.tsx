import type * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { IngestionJobReportCard, IngestionJobsPanel, VectorFreshnessNotice } from "./BrainSignals";
import type { IngestionJob, VectorFreshness } from "./types";

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

describe("IngestionJobReportCard", () => {
  const completedJob: IngestionJob = {
    jobId: "job-done",
    status: "completed",
    total: 24,
    processed: 21,
    failed: 3,
    errors: [
      "a.pdf — parse failed",
      "b.docx — unsupported",
      "c.txt — empty",
      "d.txt — should be cut at three samples",
    ],
  };
  const freshness: VectorFreshness = {
    status: "pending",
    pendingItems: 10,
    totalItems: 100,
    detail: "",
  };

  it("formats documents, failures, vector freshness and up to 3 samples", () => {
    render(
      <IngestionJobReportCard language="ko" job={completedJob} freshness={freshness} />,
    );
    const card = screen.getByTestId("ingestion-job-report");
    expect(card.textContent).toContain("+21 문서");
    expect(card.textContent).toContain("실패 3건");
    expect(card.textContent).toContain("90%");
    const samples = card.querySelectorAll("li");
    expect(samples.length).toBe(3);
    expect(samples[0].textContent).toContain("a.pdf");
    expect(card.textContent).not.toContain("d.txt");
  });

  it("degrades gracefully when fields are missing (no NaN, hidden lines)", () => {
    const sparseJob: IngestionJob = {
      jobId: "job-sparse",
      status: "completed",
      total: 0,
      processed: 0,
      failed: 0,
      errors: [],
    };
    render(<IngestionJobReportCard language="ko" job={sparseJob} freshness={null} />);
    const card = screen.getByTestId("ingestion-job-report");
    expect(card.textContent).not.toContain("NaN");
    expect(card.textContent).not.toContain("%");
    expect(card.querySelectorAll(".brain-jobs-report-stat").length).toBe(0);
    expect(card.querySelectorAll("li").length).toBe(0);
  });

  it("hides the vector line when the index reports zero items", () => {
    render(
      <IngestionJobReportCard
        language="ko"
        job={completedJob}
        freshness={{ status: "unavailable", pendingItems: 0, totalItems: 0, detail: "" }}
      />,
    );
    expect(screen.getByTestId("ingestion-job-report").textContent).not.toContain("%");
  });
});
