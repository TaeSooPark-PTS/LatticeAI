import { get, post } from "./base";

export type WorkflowRunRecord = {
  id?: string;
  workflow_id?: string;
  name?: string;
  status?: string;
  created_at?: string;
  completed_at?: string;
};

export type WorkflowReplayFrame = {
  index: number;
  event: string;
  actor: string;
  when?: string | null;
  why?: string;
  output?: unknown;
  decision?: unknown;
  raw?: Record<string, unknown>;
};

export type WorkflowReplay = {
  run_id?: string;
  status?: string;
  frames?: WorkflowReplayFrame[];
};

export type BulkReviewAction = "approve" | "dismiss";

export type BulkReviewResult = {
  requested: number;
  succeeded: number;
  failed: number;
  results: Array<{ id: string; status: "ok" | "not_found" | "conflict" | "failed"; detail?: string | null }>;
};

export const BULK_REVIEW_CAP = 200;

export const actReviewApi = {
  workflowDefinitionRuns: (workflowId: string, limit = 20) =>
    get<{ runs: WorkflowRunRecord[] }>(
      `/workflows/api/definitions/${encodeURIComponent(workflowId)}/runs`,
      { runs: [] },
      { limit },
    ),
  workflowRunReplay: (runId: string) =>
    get<{ replay: WorkflowReplay }>(`/workflows/api/runs/${encodeURIComponent(runId)}/replay`, { replay: {} }),
  bulkReview: (action: BulkReviewAction, ids: string[]) =>
    post<BulkReviewResult>(`/automation/reviews/bulk/${action}`, { ids }, {
      requested: 0, succeeded: 0, failed: 0, results: [],
    }),
  rejectCitation: (nodeId: string, query: string) =>
    post<{ correction_id?: string }>("/api/brain/corrections", {
      target_kind: "citation",
      target_id: nodeId,
      status: "confirmed",
      reason: "not_a_good_source",
      before_snapshot: {},
      after_snapshot: { feedback: "wrong_source", query },
    }, {}),
  revertCorrection: (correctionId: string) =>
    post<{ correction_id?: string }>(`/api/brain/corrections/${encodeURIComponent(correctionId)}/revert`, {}, {}),
};
