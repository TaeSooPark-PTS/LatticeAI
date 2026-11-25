import { type ChatAgentPayload, latticeApi } from "@/api/client";
import type { MessageApproval } from "./types";


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

export type ApprovalResolution =
  | { kind: "finished"; payload: ChatAgentPayload }
  | { kind: "cancelled" }
  | { kind: "expired"; replanMessage?: string }
  | { kind: "error"; reason: string };

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
