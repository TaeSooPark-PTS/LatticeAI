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
// artifacts/final_state) to merge like a normal completion. `expired` may
// carry the original user request from the 410 detail's replan hint so the
// UI can offer a one-click "다시 계획" as a fresh chat send.
export type ApprovalResolution =
  | { kind: "finished"; payload: ChatAgentPayload }
  | { kind: "cancelled" }
  | { kind: "expired"; replanMessage?: string }
  | { kind: "error"; reason: string };

// Extracts detail.replan.message (or replan.message) from an expiry response
// body. Defensive: absent/malformed → empty string (no replan offer).
export function parseReplanMessage(body: unknown): string {
  if (!body || typeof body !== "object") return "";
  const root = body as Record<string, unknown>;
  const detail = root.detail && typeof root.detail === "object" && !Array.isArray(root.detail)
    ? root.detail as Record<string, unknown>
    : root;
  const replan = detail.replan;
  if (!replan || typeof replan !== "object" || Array.isArray(replan)) return "";
  const message = (replan as Record<string, unknown>).message;
  return typeof message === "string" ? message.trim() : "";
}

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
    if (result.status === 410 || result.status === 404) {
      const replanMessage = parseReplanMessage(result.data);
      return { kind: "expired", ...(replanMessage ? { replanMessage } : {}) };
    }
    return { kind: "error", reason: result.error || String(result.status || "") };
  }
  const payload = result.data as ChatAgentPayload;
  if (payload.status === "cancelled") return { kind: "cancelled" };
  return { kind: "finished", payload };
}
