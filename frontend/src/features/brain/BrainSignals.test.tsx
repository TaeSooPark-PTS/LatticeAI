import type * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import {
  IngestionJobReportCard,
  IngestionJobsPanel,
  jobStatusKey,
  PendingApprovalsNotice,
  StaleEmbedderNotice,
  VectorFreshnessNotice,
  WatchHealthCard,
} from "./BrainSignals";
import type { IngestionJob, VectorFreshness } from "./types";

function renderWithQuery(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return Object.assign(
    render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>),
    { queryClient },
  );
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

  it("stays silent when the index is fully ready", async () => {
    vi.spyOn(latticeApi, "brainVectorFreshness").mockResolvedValue({
      ok: true, status: 200, source: "live",
      data: { status: "ready", pending_items: 0, total_items: 80, detail: "" },
    } as never);
    renderWithQuery(<VectorFreshnessNotice language="ko" />);
    await waitFor(() => expect(latticeApi.brainVectorFreshness).toHaveBeenCalled());
    expect(screen.queryByTestId("vector-freshness-chip")).toBeNull();
  });

  it("stays silent for a pending status that reports zero waiting items", async () => {
    vi.spyOn(latticeApi, "brainVectorFreshness").mockResolvedValue({
      ok: true, status: 200, source: "live",
      data: { status: "pending", pending_items: 0, total_items: 80, detail: "" },
    } as never);
    renderWithQuery(<VectorFreshnessNotice language="ko" />);
    await waitFor(() => expect(latticeApi.brainVectorFreshness).toHaveBeenCalled());
    expect(screen.queryByTestId("vector-freshness-chip")).toBeNull();
  });
});

describe("StaleEmbedderNotice", () => {
  function mockFreshness(data: Record<string, unknown>, ok = true) {
    return vi.spyOn(latticeApi, "brainVectorFreshness").mockResolvedValue({
      ok, status: ok ? 200 : 503, source: ok ? "live" : "unavailable", data,
    } as never);
  }

  it("renders nothing when there is no freshness data at all", async () => {
    mockFreshness({}, false);
    renderWithQuery(<StaleEmbedderNotice language="ko" />);
    await waitFor(() => expect(latticeApi.brainVectorFreshness).toHaveBeenCalled());
    expect(screen.queryByTestId("stale-embedder-notice")).toBeNull();
  });

  it("renders nothing when the index is fresh, just not by the current embedder", async () => {
    mockFreshness({ status: "ready", pending_items: 0, total_items: 40, detail: "" });
    renderWithQuery(<StaleEmbedderNotice language="ko" />);
    await waitFor(() => expect(latticeApi.brainVectorFreshness).toHaveBeenCalled());
    expect(screen.queryByTestId("stale-embedder-notice")).toBeNull();
  });

  it("names the problem and offers to reindex when the embedder changed", async () => {
    mockFreshness({ status: "stale_embedder", pending_items: 5, total_items: 40, detail: "" });
    renderWithQuery(<StaleEmbedderNotice language="ko" />);
    const notice = await screen.findByTestId("stale-embedder-notice");
    expect(notice.textContent).toContain("찾는 방식이 바뀌었어요");
    expect(notice.getAttribute("role")).toBe("status");
    const button = screen.getByTestId("stale-embedder-reindex") as HTMLButtonElement;
    expect(button.disabled).toBe(false);
    expect(button.textContent).toBe("기억 다시 정리하기");
    expect(notice.querySelector(".is-error")).toBeNull();
  });

  it("reindexes on click, shows the running label while pending, and refreshes freshness on success", async () => {
    mockFreshness({ status: "stale_embedder", pending_items: 5, total_items: 40, detail: "" });
    let release: (value: unknown) => void = () => {};
    const rebuild = vi.spyOn(latticeApi, "memoryRebuild").mockReturnValue(
      new Promise((resolve) => { release = resolve; }) as never,
    );
    renderWithQuery(<StaleEmbedderNotice language="ko" />);
    const button = await screen.findByTestId("stale-embedder-reindex");
    const callsBeforeClick = (latticeApi.brainVectorFreshness as ReturnType<typeof vi.fn>).mock.calls.length;

    await userEvent.click(button);
    expect(rebuild).toHaveBeenCalledTimes(1);
    await screen.findByText("기억을 다시 정리하는 중…");
    expect((screen.getByTestId("stale-embedder-reindex") as HTMLButtonElement).disabled).toBe(true);

    release({ ok: true, status: 200, source: "live", data: {} });
    await waitFor(() => expect((screen.getByTestId("stale-embedder-reindex") as HTMLButtonElement).disabled).toBe(false));
    expect(screen.getByTestId("stale-embedder-reindex").textContent).toBe("기억 다시 정리하기");
    expect(document.querySelector(".is-error")).toBeNull();
    // A successful rebuild invalidates the freshness query, triggering a refetch.
    await waitFor(() =>
      expect((latticeApi.brainVectorFreshness as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(
        callsBeforeClick,
      ),
    );
  });

  it("shows a failure line when the reindex request itself fails", async () => {
    mockFreshness({ status: "stale_embedder", pending_items: 5, total_items: 40, detail: "" });
    vi.spyOn(latticeApi, "memoryRebuild").mockResolvedValue({
      ok: false, status: 500, source: "live", data: {}, error: "rebuild failed",
    } as never);
    renderWithQuery(<StaleEmbedderNotice language="en" />);
    const button = await screen.findByTestId("stale-embedder-reindex");
    await userEvent.click(button);
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
    expect(screen.getByText("Could not sort them again. Please try again shortly.")).toBeTruthy();
  });
});

describe("jobStatusKey", () => {
  it("maps every known status and defaults the unknown to queued", () => {
    expect(jobStatusKey("queued")).toBe("brain.jobs.status.queued");
    expect(jobStatusKey("running")).toBe("brain.jobs.status.running");
    expect(jobStatusKey("completed")).toBe("brain.jobs.status.completed");
    expect(jobStatusKey("failed")).toBe("brain.jobs.status.failed");
    expect(jobStatusKey("partial")).toBe("brain.jobs.status.partial");
    expect(jobStatusKey("cancelled")).toBe("brain.jobs.status.queued");
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

  it("marks only the resumed job busy while the request is in flight", async () => {
    vi.spyOn(latticeApi, "ingestionJobs").mockResolvedValue({
      ok: true, status: 200, source: "live",
      data: { jobs: [
        { job_id: "job-a", status: "failed", total: 10, processed: 4, failed: 6, errors: [] },
        { job_id: "job-b", status: "partial", total: 8, processed: 6, failed: 2, errors: [] },
      ] },
    } as never);
    let release: (value: unknown) => void = () => {};
    vi.spyOn(latticeApi, "resumeIngestionJob").mockReturnValue(
      new Promise((resolve) => { release = resolve; }) as never,
    );

    renderWithQuery(<IngestionJobsPanel language="ko" />);
    await screen.findByTestId("ingestion-jobs-panel");
    const [first, second] = screen.getAllByRole("button", { name: /이어서 처리/ });
    fireEvent.click(first);
    // The clicked job says "resuming", its sibling keeps the idle label but is
    // disabled with it while the request runs.
    await screen.findByText("다시 시작하는 중…");
    expect(second.textContent).toContain("이어서 처리");
    expect((first as HTMLButtonElement).disabled).toBe(true);
    expect((second as HTMLButtonElement).disabled).toBe(true);
    release({ ok: true, status: 200, source: "live", data: {} });
    await waitFor(() => expect((first as HTMLButtonElement).disabled).toBe(false));
  });

  it("turns a watched job into a dismissible completion report and forgets lost jobs", async () => {
    const jobsSpy = vi.spyOn(latticeApi, "ingestionJobs");
    const envelope = (jobs: Array<Record<string, unknown>>) =>
      ({ ok: true, status: 200, source: "live", data: { jobs } }) as never;
    const running = { job_id: "job-1", status: "running", total: 12, processed: 3, failed: 0, errors: [] };
    const completed = { job_id: "job-1", status: "completed", total: 12, processed: 12, failed: 0, errors: [] };
    jobsSpy.mockResolvedValueOnce(envelope([running]));
    vi.spyOn(latticeApi, "brainVectorFreshness").mockResolvedValue({
      ok: true, status: 200, source: "live",
      data: { status: "ready", pending_items: 0, total_items: 40, detail: "" },
    } as never);

    const { queryClient } = renderWithQuery(<IngestionJobsPanel language="ko" />);
    await screen.findByTestId("ingestion-jobs-panel");
    expect(screen.queryByTestId("ingestion-job-report")).toBeNull();

    // active → completed: the report card appears and the list disappears.
    jobsSpy.mockResolvedValueOnce(envelope([completed]));
    await act(async () => {
      await queryClient.refetchQueries({ queryKey: ["ingestionJobs"] });
    });
    await screen.findByTestId("ingestion-job-report");
    expect(screen.queryByRole("progressbar")).toBeNull();
    expect(screen.getByTestId("ingestion-job-report").textContent).toContain("+12 문서");

    // The backend restarts the same job; when it completes again the report
    // stays single (the id is deduplicated, not stacked).
    jobsSpy.mockResolvedValueOnce(envelope([running]));
    await act(async () => {
      await queryClient.refetchQueries({ queryKey: ["ingestionJobs"] });
    });
    jobsSpy.mockResolvedValueOnce(envelope([completed]));
    await act(async () => {
      await queryClient.refetchQueries({ queryKey: ["ingestionJobs"] });
    });
    await waitFor(() => expect(screen.queryByRole("progressbar")).toBeNull());
    expect(screen.getAllByTestId("ingestion-job-report").length).toBe(1);

    // Dismissing hides the report; with nothing else to show the panel leaves.
    fireEvent.click(screen.getByRole("button", { name: "수집 결과 닫기" }));
    await waitFor(() => expect(screen.queryByTestId("ingestion-jobs-panel")).toBeNull());
  });

  it("accumulates reports for two distinct jobs that finish separately, newest first", async () => {
    const jobsSpy = vi.spyOn(latticeApi, "ingestionJobs");
    const envelope = (jobs: Array<Record<string, unknown>>) =>
      ({ ok: true, status: 200, source: "live", data: { jobs } }) as never;
    const jobARunning = { job_id: "job-A", status: "running", total: 10, processed: 2, failed: 0, errors: [] };
    const jobACompleted = { job_id: "job-A", status: "completed", total: 10, processed: 10, failed: 0, errors: [] };
    const jobBRunning = { job_id: "job-B", status: "running", total: 6, processed: 1, failed: 0, errors: [] };
    const jobBCompleted = { job_id: "job-B", status: "completed", total: 6, processed: 6, failed: 0, errors: [] };

    jobsSpy.mockResolvedValueOnce(envelope([jobARunning]));
    const { queryClient } = renderWithQuery(<IngestionJobsPanel language="ko" />);
    await screen.findByTestId("ingestion-jobs-panel");

    // job-A finishes first: the previous (empty) report list is filtered,
    // which is a no-op filter over zero items.
    jobsSpy.mockResolvedValueOnce(envelope([jobACompleted]));
    await act(async () => {
      await queryClient.refetchQueries({ queryKey: ["ingestionJobs"] });
    });
    await screen.findByTestId("ingestion-job-report");
    expect(screen.getAllByTestId("ingestion-job-report").length).toBe(1);

    // job-B starts and finishes while job-A's report is still on screen: the
    // filter now runs over a non-empty previous list, keeping job-A's report
    // (it is not in the newly-finished set) and placing job-B's first. The
    // intermediate "job-B running" state must actually commit (not just
    // resolve in the query cache) before job-B can be observed transitioning
    // out of it, so wait for its progress bar before moving on.
    jobsSpy.mockResolvedValueOnce(envelope([jobACompleted, jobBRunning]));
    await act(async () => {
      await queryClient.refetchQueries({ queryKey: ["ingestionJobs"] });
    });
    await waitFor(() => expect(screen.getByRole("progressbar")).toBeTruthy());

    jobsSpy.mockResolvedValueOnce(envelope([jobACompleted, jobBCompleted]));
    await act(async () => {
      await queryClient.refetchQueries({ queryKey: ["ingestionJobs"] });
    });
    await waitFor(() => expect(screen.getAllByTestId("ingestion-job-report").length).toBe(2));
    const reports = screen.getAllByTestId("ingestion-job-report");
    expect(reports[0].textContent).toContain("+6 문서");
    expect(reports[1].textContent).toContain("+10 문서");
  });

  it("drops a report whose job vanished from the backend list", async () => {
    const jobsSpy = vi.spyOn(latticeApi, "ingestionJobs");
    const envelope = (jobs: Array<Record<string, unknown>>) =>
      ({ ok: true, status: 200, source: "live", data: { jobs } }) as never;
    jobsSpy.mockResolvedValueOnce(envelope([
      { job_id: "job-2", status: "running", total: 5, processed: 1, failed: 0, errors: [] },
    ]));

    const { queryClient } = renderWithQuery(<IngestionJobsPanel language="ko" />);
    await screen.findByTestId("ingestion-jobs-panel");

    jobsSpy.mockResolvedValueOnce(envelope([
      { job_id: "job-2", status: "partial", total: 5, processed: 4, failed: 1, errors: ["a.pdf"] },
    ]));
    await act(async () => {
      await queryClient.refetchQueries({ queryKey: ["ingestionJobs"] });
    });
    // Partial: report card and the resumable list entry coexist.
    await screen.findByTestId("ingestion-job-report");
    expect(screen.getByRole("progressbar")).toBeTruthy();

    jobsSpy.mockResolvedValueOnce(envelope([]));
    await act(async () => {
      await queryClient.refetchQueries({ queryKey: ["ingestionJobs"] });
    });
    await waitFor(() => expect(screen.queryByTestId("ingestion-jobs-panel")).toBeNull());
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

  it("counts a fully-ready index as 100% and titles a partial run honestly", () => {
    const onDismiss = vi.fn();
    render(
      <IngestionJobReportCard
        language="ko"
        job={{ ...completedJob, status: "partial" }}
        freshness={{ status: "ready", pendingItems: 0, totalItems: 50, detail: "" }}
        onDismiss={onDismiss}
      />,
    );
    const card = screen.getByTestId("ingestion-job-report");
    expect(card.textContent).toContain("폴더 수집을 일부 마쳤어요");
    expect(card.textContent).toContain("100%");
    fireEvent.click(screen.getByRole("button", { name: "수집 결과 닫기" }));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });
});

describe("WatchHealthCard", () => {
  function mockWatchStatus(data: Record<string, unknown>, ok = true) {
    return vi.spyOn(latticeApi, "ingestionWatchStatus").mockResolvedValue({
      ok, status: ok ? 200 : 503, source: ok ? "live" : "unavailable", data,
    } as never);
  }

  it("shows watch basenames, scan results, errors, and the not-live note", async () => {
    mockWatchStatus({
      enabled_count: 1,
      polling: false,
      interval_seconds: 60,
      watches: [{
        id: "watch_1",
        path: "/Users/me/Documents/프로젝트노트",
        enabled: true,
        last_scan_at: new Date(Date.now() - 5 * 60_000).toISOString(),
        last_result: { status: "ok", new: 3, ingested: 3, failed: 2 },
        tracked_files: 41,
        last_errors: [
          { path: "/Users/me/Documents/프로젝트노트/broken.pdf", detail: "parse failed" },
          { path: "/Users/me/Documents/프로젝트노트/locked.docx", detail: "permission denied" },
        ],
      }],
    });

    renderWithQuery(<WatchHealthCard language="ko" />);
    const card = await screen.findByTestId("watch-health-card");
    expect(card.textContent).toContain("프로젝트노트");
    expect(card.textContent).toContain("5분 전 스캔");
    expect(card.textContent).toContain("+3 반영");
    expect(card.textContent).toContain("실패 2건");
    expect(card.textContent).toContain("broken.pdf — parse failed");
    expect(screen.getByTestId("watch-polling-note")).toBeTruthy();
  });

  it("renders nothing when no watch is enabled", async () => {
    mockWatchStatus({ enabled_count: 0, polling: false, interval_seconds: 60, watches: [] });
    renderWithQuery(<WatchHealthCard language="ko" />);
    await Promise.resolve();
    expect(screen.queryByTestId("watch-health-card")).toBeNull();
  });

  it("stays silent when the endpoint is unavailable", async () => {
    mockWatchStatus({}, false);
    renderWithQuery(<WatchHealthCard language="ko" />);
    await Promise.resolve();
    expect(screen.queryByTestId("watch-health-card")).toBeNull();
  });

  it("hides the periodic-scan note while the poller is live", async () => {
    mockWatchStatus({
      enabled_count: 1,
      polling: true,
      interval_seconds: 60,
      watches: [{ id: "w", path: "/tmp/docs", enabled: true, tracked_files: 3 }],
    });
    renderWithQuery(<WatchHealthCard language="ko" />);
    await screen.findByTestId("watch-health-card");
    expect(screen.queryByTestId("watch-polling-note")).toBeNull();
  });

  it("renders nothing when the enabled count lies about disabled watches", async () => {
    mockWatchStatus({
      enabled_count: 2,
      polling: true,
      interval_seconds: 60,
      watches: [{ id: "w1", path: "/tmp/a", enabled: false, tracked_files: 3 }],
    });
    const { queryClient } = renderWithQuery(<WatchHealthCard language="ko" />);
    // Wait for the query to actually settle (not just for the call to have
    // started) so this exercises the post-filter empty-watches guard rather
    // than the trivial "still loading" null render.
    await waitFor(() => expect(queryClient.getQueryData(["ingestionWatch"])).toBeTruthy());
    expect(screen.queryByTestId("watch-health-card")).toBeNull();
  });

  it("labels scan ages from just-now to days and caps the list at four watches", async () => {
    const now = Date.now();
    mockWatchStatus({
      enabled_count: 5,
      polling: true,
      interval_seconds: 60,
      watches: [
        { id: "w1", path: "/", enabled: true, last_scan_at: new Date(now - 10_000).toISOString(), tracked_files: 0 },
        { id: "w2", path: "/tmp/시간", enabled: true, last_scan_at: new Date(now - 3 * 3_600_000).toISOString(), tracked_files: 0 },
        { id: "w3", path: "/tmp/일", enabled: true, last_scan_at: new Date(now - 2 * 86_400_000).toISOString(), tracked_files: 0 },
        { id: "w4", path: "/tmp/고장", enabled: true, last_scan_at: "definitely-not-a-date", tracked_files: 0 },
        { id: "w5", path: "/tmp/잘림", enabled: true, tracked_files: 9 },
      ],
    });
    renderWithQuery(<WatchHealthCard language="ko" />);
    const card = await screen.findByTestId("watch-health-card");
    expect(card.querySelectorAll(".brain-watch-item").length).toBe(4);
    expect(card.textContent).toContain("방금 스캔");
    expect(card.textContent).toContain("3시간 전 스캔");
    expect(card.textContent).toContain("2일 전 스캔");
    expect(card.textContent).toContain("아직 스캔 전");
    expect(card.textContent).not.toContain("잘림");
    // A root path has no basename to shorten to.
    expect(card.querySelector(".brain-watch-path")!.textContent).toBe("/");
  });

  it("hides zero stats and joins error lines from whatever fields exist", async () => {
    mockWatchStatus({
      enabled_count: 1,
      polling: true,
      interval_seconds: 60,
      watches: [{
        id: "w1",
        path: "/tmp/docs",
        enabled: true,
        last_result: { status: "ok", ingested: 0, failed: 3 },
        tracked_files: 0,
        last_errors: [
          { path: "", detail: "watcher crashed" },
          { path: "/tmp/docs/딱경로만.pdf", detail: "" },
        ],
      }],
    });
    renderWithQuery(<WatchHealthCard language="ko" />);
    const card = await screen.findByTestId("watch-health-card");
    expect(card.textContent).not.toContain("반영");
    expect(card.textContent).toContain("실패 3건");
    expect(card.textContent).not.toContain("추적");
    const errors = card.querySelectorAll(".brain-watch-errors li");
    expect(errors[0].textContent).toBe("watcher crashed");
    expect(errors[1].textContent).toBe("딱경로만.pdf");
  });
});

describe("PendingApprovalsNotice", () => {
  function mockApprovals(pending: Array<Record<string, unknown>>) {
    return vi.spyOn(latticeApi, "agentApprovals").mockResolvedValue({
      ok: true, status: 200, source: "live", data: { pending },
    } as never);
  }

  it("surfaces a paused run that no visible message represents", async () => {
    mockApprovals([
      { run_id: "run-lost", goal: "주간 보고서 정리", expires_at: "2026-07-26T09:30:00+09:00" },
      { run_id: "run-broken", goal: "깨진 시간", expires_at: "not-a-timestamp" },
    ]);
    renderWithQuery(<PendingApprovalsNotice language="ko" knownRunIds={[]} />);
    const notice = await screen.findByTestId("pending-approvals-notice");
    expect(notice.textContent).toContain("주간 보고서 정리");
    // An unparseable expiry falls back to the "decide on the original card" hint.
    expect(notice.textContent).toContain("깨진 시간");
    expect(notice.getAttribute("role")).toBe("note");
  });

  it("stays silent when the paused run already has an inline card", async () => {
    mockApprovals([
      { run_id: "run-visible", goal: "이미 보이는 실행", expires_at: "2026-07-26T09:30:00+09:00" },
    ]);
    renderWithQuery(<PendingApprovalsNotice language="ko" knownRunIds={["run-visible"]} />);
    await Promise.resolve();
    expect(screen.queryByTestId("pending-approvals-notice")).toBeNull();
  });

  it("stays silent when nothing is pending", async () => {
    mockApprovals([]);
    renderWithQuery(<PendingApprovalsNotice language="ko" knownRunIds={[]} />);
    await Promise.resolve();
    expect(screen.queryByTestId("pending-approvals-notice")).toBeNull();
  });

  it("stays silent when the approvals endpoint fails", async () => {
    vi.spyOn(latticeApi, "agentApprovals").mockResolvedValue({
      ok: false, status: 503, source: "unavailable", data: { pending: [] },
    } as never);
    renderWithQuery(<PendingApprovalsNotice language="ko" knownRunIds={[]} />);
    await waitFor(() => expect(latticeApi.agentApprovals).toHaveBeenCalled());
    expect(screen.queryByTestId("pending-approvals-notice")).toBeNull();
  });

  it("falls back to generic copy and shows at most two orphaned runs", async () => {
    mockApprovals([
      { run_id: "run-1", goal: "", expires_at: "" },
      { run_id: "run-2", goal: "두번째 목표", expires_at: "2026-07-26T09:30:00+09:00" },
      { run_id: "run-3", goal: "세번째 목표", expires_at: "2026-07-26T09:30:00+09:00" },
    ]);
    renderWithQuery(<PendingApprovalsNotice language="en" knownRunIds={[]} />);
    const notice = await screen.findByTestId("pending-approvals-notice");
    expect(notice.querySelectorAll("p").length).toBe(2);
    // No goal and no expiry → generic sentence plus the hint line.
    expect(notice.textContent).toContain("두번째 목표");
    expect(notice.textContent).not.toContain("세번째 목표");
  });
});
