/**
 * Agent approval flow (SURFACE_PARITY, v9.9.5).
 *
 * Tokens are not re-issued by `GET /agent/approvals`, so resuming a paused run
 * depends on whichever token this session cached — or on the user pasting one.
 * That constraint is the reason these four functions belong together: every one
 * of them has to answer "do we have a token for this run?" the same way.
 */
import * as vscode from "vscode";
import { LatticeAIClient } from "./client";

export interface PendingApprovalItem extends vscode.QuickPickItem {
  runId: string;
  token: string;
  planSummary: string;
}

export async function fetchPendingApprovals(c: LatticeAIClient): Promise<PendingApprovalItem[]> {
  const payload = await c.listApprovals();
  const items = Array.isArray(payload?.pending)
    ? payload.pending
    : Array.isArray(payload)
      ? payload
      : [];
  return items
    .map((entry: any): PendingApprovalItem | null => {
      const runId = String(entry.run_id || entry.id || "");
      if (!runId) return null;
      // Tokens are not re-issued by GET /agent/approvals (security). Resume
      // only works when this extension cached the token from the pause
      // response, or the user pastes one.
      const token = String(c.takeCachedApprovalToken(runId) || "");
      const planSummary = String(
        entry.plan_summary || entry.summary || entry.goal || entry.approval?.plan_summary || "(plan)"
      );
      const expires = entry.expires_at || entry.approval?.expires_at || "";
      return {
        label: planSummary.slice(0, 80) || runId,
        description: token
          ? (expires ? `expires ${expires}` : "token ready")
          : "token not in this session — paste required",
        detail: runId,
        runId,
        token,
        planSummary,
      };
    })
    .filter((x: PendingApprovalItem | null): x is PendingApprovalItem => x !== null);
}

export async function resolveToken(c: LatticeAIClient, item: PendingApprovalItem): Promise<string | undefined> {
  if (item.token) return item.token;
  const pasted = await vscode.window.showInputBox({
    prompt: `Paste approval token for run ${item.runId.slice(0, 12)}…`,
    placeHolder: "token from the pause response or web UI",
    ignoreFocusOut: true,
    password: true,
  });
  if (pasted) c.cacheApprovalToken(item.runId, pasted);
  return pasted || undefined;
}

export async function showPendingApprovals(c: LatticeAIClient, approveSelected: boolean) {
  try {
    const pending = await fetchPendingApprovals(c);
    if (!pending.length) {
      vscode.window.showInformationMessage("Lattice AI: No pending agent approvals.");
      return;
    }
    if (!approveSelected) {
      const lines = pending.map((p) => `• ${p.planSummary} (${p.runId.slice(0, 8)}…)`).join("\n");
      const openWeb = "Open Web UI";
      const pick = await vscode.window.showInformationMessage(
        `Lattice AI: ${pending.length} pending approval(s).\n${lines}`,
        openWeb
      );
      if (pick === openWeb) {
        const base = vscode.workspace.getConfiguration("ltcai").get<string>("serverUrl") || "http://localhost:4825";
        await vscode.env.openExternal(vscode.Uri.parse(`${base.replace(/\/$/, "")}/app`));
      }
      return;
    }
    const chosen = await vscode.window.showQuickPick(pending, {
      placeHolder: "Select a paused agent run to approve",
      matchOnDescription: true,
      matchOnDetail: true,
    });
    if (!chosen) return;
    const token = await resolveToken(c, chosen);
    if (!token) {
      vscode.window.showWarningMessage("Lattice AI: Approval token required to resume.");
      return;
    }
    const result = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: "Lattice AI: Resuming approved run..." },
      async () => c.resumeAgent({
        run_id: chosen.runId,
        approval_token: token,
        approved: true,
      })
    );
    const status = String(result?.status || "ok");
    const response = String(result?.response || result?.final_message || status);
    vscode.window.showInformationMessage(`Lattice AI: ${status} — ${response.slice(0, 200)}`);
  } catch (err: any) {
    vscode.window.showErrorMessage(`Lattice AI approval failed: ${err?.message || err}`);
  }
}

export async function rejectPendingApproval(c: LatticeAIClient) {
  try {
    const pending = await fetchPendingApprovals(c);
    if (!pending.length) {
      vscode.window.showInformationMessage("Lattice AI: No pending agent approvals.");
      return;
    }
    const chosen = await vscode.window.showQuickPick(pending, {
      placeHolder: "Select a paused agent run to reject",
    });
    if (!chosen) return;
    const token = await resolveToken(c, chosen);
    if (!token) {
      vscode.window.showWarningMessage("Lattice AI: Approval token required to reject.");
      return;
    }
    const result = await c.resumeAgent({
      run_id: chosen.runId,
      approval_token: token,
      approved: false,
    });
    vscode.window.showInformationMessage(
      `Lattice AI: ${result?.status || "cancelled"} — run rejected.`
    );
  } catch (err: any) {
    vscode.window.showErrorMessage(`Lattice AI reject failed: ${err?.message || err}`);
  }
}
