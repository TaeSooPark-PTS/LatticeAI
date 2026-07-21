import { type ChatAgentPayload, latticeApi } from "@/api/client";
import type { MessageApproval } from "./types";

// Interactive plan approval (backlog #2): pure helpers between the
// awaiting_approval agent payload, the /agent/resume call, and the chat
// message state. Kept UI-free so the token/HTTP contract is unit-testable.

// Builds the message-level approval record from an awaiting_approval payload.
// Returns null when the payload is malformed (missing run_id/token) so the
// chat never renders an approval card it cannot resume.
export function parseApprovalPayload(agent: ChatAgentPayload): MessageApproval | null {
  if (agent.status !== "awaiting_approval") return null;
  const runId = typeof agent.run_id === "string" ? agent.run_id : "";
  const token = typeof agent.approval?.token === "string" ? agent.approval.token : "";
  if (!runId || !token) return null;
  return {
    runId,
    token,
    expiresAt: typeof agent.approval?.expires_at === "string" ? agent.approval.expires_at : "",
    planSummary: typeof agent.approval?.plan_summary === "string" ? agent.approval.plan_summary : "",
    plan: agent.plan && typeof agent.plan === "object" ? agent.plan : null,
    status: "pending",
  };
}

export type ApprovalDecision = {
  approve: boolean;
  editedPlan?: Record<string, unknown>;
};

// What the chat message should record after a resume attempt. `finished`
// carries the standard agent finish payload (response/created_files/
// artifacts/final_state) to merge like a normal completion.
export type ApprovalResolution =
  | { kind: "finished"; payload: ChatAgentPayload }
  | { kind: "cancelled" }
  | { kind: "expired" }
  | { kind: "error"; reason: string };

// Presents the single-use token to /agent/resume and classifies the outcome.
// 410 (token TTL elapsed) and 404 (run lost, e.g. server restart) both mean
// "start over" — surfaced as `expired` so the UI shows one honest message.
export async function resolveApprovalRequest(
  approval: Pick<MessageApproval, "runId" | "token">,
  decision: ApprovalDecision,
): Promise<ApprovalResolution> {
  const result = await latticeApi.resumeAgentApproval({
    run_id: approval.runId,
    approval_token: approval.token,
    approve: decision.approve,
    ...(decision.editedPlan ? { edited_plan: decision.editedPlan } : {}),
  });
  if (!result.ok) {
    if (result.status === 410 || result.status === 404) return { kind: "expired" };
    return { kind: "error", reason: result.error || String(result.status || "") };
  }
  const payload = result.data as ChatAgentPayload;
  if (payload.status === "cancelled") return { kind: "cancelled" };
  return { kind: "finished", payload };
}
