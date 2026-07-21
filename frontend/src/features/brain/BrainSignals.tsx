import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Clock3, RotateCcw, X } from "lucide-react";

import { latticeApi } from "@/api/client";
import { t, type Language } from "@/i18n";
import { parseIngestionJobs, parseVectorFreshness } from "./brainData";
import type { IngestionJob, VectorFreshness } from "./types";

// One fetch per view entry, gentle refresh afterwards. When the endpoint is
// missing or reports "unavailable" the chip stays silent by design.
export function useVectorFreshness() {
  const query = useQuery({
    queryKey: ["vectorFreshness"],
    queryFn: latticeApi.brainVectorFreshness,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
    retry: false,
  });
  return React.useMemo(
    () => (query.data?.ok ? parseVectorFreshness(query.data.data) : null),
    [query.data],
  );
}

// Soft chip shown while some knowledge still waits for vector indexing.
export function VectorFreshnessNotice({ language }: { language: Language }) {
  const freshness = useVectorFreshness();
  if (!freshness || freshness.status !== "pending" || freshness.pendingItems < 1) return null;
  return (
    <p className="brain-freshness-chip" role="status" data-testid="vector-freshness-chip">
      <Clock3 className="h-3.5 w-3.5" aria-hidden="true" />
      <span>{t(language, "brain.freshness.pending", { count: freshness.pendingItems })}</span>
    </p>
  );
}

const ACTIVE_JOB_STATUSES = new Set(["queued", "running"]);
const RESUMABLE_JOB_STATUSES = new Set(["failed", "partial"]);

function isActiveJob(job: IngestionJob) {
  return ACTIVE_JOB_STATUSES.has(job.status);
}

function jobStatusKey(status: IngestionJob["status"]) {
  if (status === "queued" || status === "running" || status === "completed" || status === "failed" || status === "partial") {
    return `brain.jobs.status.${status}`;
  }
  return "brain.jobs.status.queued";
}

const REPORTABLE_JOB_STATUSES = new Set(["completed", "partial"]);
const REPORT_ERROR_SAMPLES = 3;

// Completion report for a finished folder/background ingest: what actually
// landed ("+N documents"), how fresh the vector index is, and up to three
// skipped/failed samples with their reasons. Every line degrades by hiding
// itself when the API omits the underlying field — never a NaN.
export function IngestionJobReportCard({
  language,
  job,
  freshness,
  onDismiss,
}: {
  language: Language;
  job: IngestionJob;
  freshness: VectorFreshness | null;
  onDismiss?: () => void;
}) {
  const vectorPercent =
    freshness && freshness.totalItems > 0 && (freshness.status === "ready" || freshness.status === "pending")
      ? Math.max(0, Math.min(100, Math.round(((freshness.totalItems - freshness.pendingItems) / freshness.totalItems) * 100)))
      : null;
  const samples = job.errors.slice(0, REPORT_ERROR_SAMPLES);
  return (
    <div
      className={`brain-jobs-report lattice-success-pulse is-${job.status}`}
      role="status"
      data-testid="ingestion-job-report"
    >
      <div className="brain-jobs-report-head">
        <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
        <strong>
          {t(language, job.status === "partial" ? "brain.jobs.report.titlePartial" : "brain.jobs.report.title")}
        </strong>
        {onDismiss ? (
          <button
            type="button"
            className="brain-jobs-report-dismiss"
            aria-label={t(language, "brain.jobs.report.dismiss")}
            onClick={onDismiss}
          >
            <X className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        ) : null}
      </div>
      <div className="brain-jobs-report-stats">
        {job.processed > 0 ? (
          <span className="brain-jobs-report-stat">
            {t(language, "brain.jobs.report.documents", { count: job.processed })}
          </span>
        ) : null}
        {job.failed > 0 ? (
          <span className="brain-jobs-report-stat is-failed">
            {t(language, "brain.jobs.failedCount", { count: job.failed })}
          </span>
        ) : null}
        {vectorPercent !== null ? (
          <span className="brain-jobs-report-stat">
            {t(language, "brain.jobs.report.vector", { percent: vectorPercent })}
          </span>
        ) : null}
      </div>
      {samples.length ? (
        <div className="brain-jobs-report-samples">
          <small>{t(language, "brain.jobs.report.skipped")}</small>
          <ul>
            {samples.map((sample) => (
              <li key={sample}>{sample}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

// Background ingestion jobs (folder scans etc.): progress, failures, resume.
// Polls only while a job is actually queued or running. When a watched job
// finishes it stays on screen once as a completion report card.
export function IngestionJobsPanel({ language }: { language: Language }) {
  const qc = useQueryClient();
  const freshness = useVectorFreshness();
  const jobsQ = useQuery({
    queryKey: ["ingestionJobs"],
    queryFn: latticeApi.ingestionJobs,
    refetchOnWindowFocus: false,
    retry: false,
    refetchInterval: (query) => {
      const result = query.state.data;
      if (!result?.ok) return false;
      return parseIngestionJobs(result.data).some(isActiveJob) ? 4000 : false;
    },
  });
  const resume = useMutation({
    mutationFn: (jobId: string) => latticeApi.resumeIngestionJob(jobId),
    onSettled: () => void qc.invalidateQueries({ queryKey: ["ingestionJobs"] }),
  });

  const jobs = React.useMemo(
    () => (jobsQ.data?.ok ? parseIngestionJobs(jobsQ.data.data) : []),
    [jobsQ.data],
  );

  // Report cards appear when a job we saw active transitions to a terminal
  // "completed"/"partial" state while this panel is mounted.
  const previousStatusesRef = React.useRef<Map<string, string>>(new Map());
  const [reportJobIds, setReportJobIds] = React.useState<string[]>([]);
  React.useEffect(() => {
    const previous = previousStatusesRef.current;
    const finished: string[] = [];
    for (const job of jobs) {
      const before = previous.get(job.jobId);
      if (before && ACTIVE_JOB_STATUSES.has(before) && REPORTABLE_JOB_STATUSES.has(job.status)) {
        finished.push(job.jobId);
      }
      previous.set(job.jobId, job.status);
    }
    if (finished.length) {
      setReportJobIds((ids) => [...finished, ...ids.filter((id) => !finished.includes(id))].slice(0, 2));
    }
  }, [jobs]);
  const reportJobs = reportJobIds
    .map((id) => jobs.find((job) => job.jobId === id))
    .filter((job): job is IngestionJob => Boolean(job));

  const visibleJobs = jobs
    .filter((job) => isActiveJob(job) || RESUMABLE_JOB_STATUSES.has(job.status))
    .slice(0, 4);
  if (!visibleJobs.length && !reportJobs.length) return null;

  return (
    <section className="brain-jobs-panel" aria-label={t(language, "brain.jobs.aria")} data-testid="ingestion-jobs-panel">
      {reportJobs.map((job) => (
        <IngestionJobReportCard
          key={job.jobId}
          language={language}
          job={job}
          freshness={freshness}
          onDismiss={() => setReportJobIds((ids) => ids.filter((id) => id !== job.jobId))}
        />
      ))}
      {visibleJobs.length ? (
        <>
      <header className="brain-jobs-head">
        <strong>{t(language, "brain.jobs.title")}</strong>
      </header>
      <ul className="brain-jobs-list">
        {visibleJobs.map((job) => {
          const total = Math.max(job.total, job.processed, 1);
          const percent = Math.min(100, Math.round((job.processed / total) * 100));
          const resumable = RESUMABLE_JOB_STATUSES.has(job.status);
          const busy = resume.isPending && resume.variables === job.jobId;
          return (
            <li key={job.jobId} className={`brain-jobs-item is-${job.status}`}>
              <div className="brain-jobs-item-row">
                <span className="brain-jobs-status">{t(language, jobStatusKey(job.status))}</span>
                <span className="brain-jobs-counts">
                  {t(language, "brain.jobs.progress", { processed: job.processed, total: job.total })}
                </span>
                {job.failed > 0 ? (
                  <span className="brain-jobs-failed">
                    <AlertTriangle className="h-3 w-3" aria-hidden="true" />
                    {t(language, "brain.jobs.failedCount", { count: job.failed })}
                  </span>
                ) : null}
              </div>
              <div
                className="brain-jobs-bar"
                role="progressbar"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={percent}
                aria-label={t(language, "brain.jobs.progressAria", { processed: job.processed, total: job.total })}
              >
                <span className="brain-jobs-bar-fill" style={{ width: `${percent}%` }} />
              </div>
              {resumable ? (
                <button
                  type="button"
                  className="brain-jobs-resume"
                  disabled={resume.isPending}
                  onClick={() => resume.mutate(job.jobId)}
                >
                  <RotateCcw className="h-3 w-3" aria-hidden="true" />
                  {busy ? t(language, "brain.jobs.resuming") : t(language, "brain.jobs.resume")}
                </button>
              ) : null}
            </li>
          );
        })}
      </ul>
        </>
      ) : null}
    </section>
  );
}
