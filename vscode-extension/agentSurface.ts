/**
 * The surfaces the web app has and the editor must match (SURFACE_PARITY).
 *
 * Grounding badge on a recall, evidence → action, Review Center, agent runs
 * (batch and live), folder capture, artifact cards. Each one renders the same
 * server payload the web card renders — the difference is a QuickPick instead
 * of a panel, never a different contract.
 */
import * as vscode from "vscode";
import { ChatPanel } from "./ChatPanel";
import { LatticeAIClient } from "./client";
import {
  client,
  lastArtifacts,
  lastRecall,
  outputChannel,
  setLastArtifacts,
  setLastRecall,
} from "./extensionState";
import { showPendingApprovals } from "./approvals";
import {
  artifactReport,
  citedSourceIds,
  groundingBadge,
  groundingLine,
  parseArtifacts,
  parseEvidenceActions,
  parseProposals,
  runReport,
  stepLine,
  type ArtifactCard,
  type EvidenceAction,
  type ProposalSummary,
} from "./surface";

/** Ask /chat for the grounding verdict on a recall and report it honestly. */
export async function showRecallGrounding(question: string, message: string): Promise<void> {
  try {
    const payload = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Window, title: "Lattice AI: checking evidence..." },
      async () => client.chat(message),
    );
    const line = groundingLine(payload);
    // Remember what this answer actually cited so "이 근거로 만들기" has real
    // evidence to work from (SURFACE_PARITY v9.9.7).
    const sourceIds = citedSourceIds(payload);
    setLastRecall({ question, sourceIds });
    const buildFrom = "Build from this evidence";
    const openWeb = "Open Web UI";
    const actions = groundingBadge(payload).status === "supported" && sourceIds.length
      ? [buildFrom, openWeb]
      : [openWeb];
    const pick = await vscode.window.showInformationMessage(
      `Lattice AI — ${question.slice(0, 60)}\n${line}`,
      ...actions,
    );
    if (pick === buildFrom) await showEvidenceActions(client);
    else if (pick === openWeb) await openAppSurface("/app");
  } catch (err: any) {
    // No verdict is not a passing verdict — say so rather than badge nothing.
    vscode.window.showWarningMessage(
      `Lattice AI: could not confirm evidence for this answer (${err?.message || err}).`,
    );
  }
}

interface EvidenceActionItem extends vscode.QuickPickItem {
  action: EvidenceAction;
}

/**
 * Evidence → action parity (v9.9.7). The sources the last recall actually
 * cited become one-click follow-ups, composed server-side by the same
 * `/api/evidence/actions` surface the web card uses. File-producing actions
 * run through the agent so the artifact lands in the workspace; chat actions
 * open in the panel.
 */
export async function showEvidenceActions(
  c: LatticeAIClient,
  context?: vscode.ExtensionContext,
): Promise<void> {
  if (!lastRecall || !lastRecall.sourceIds.length) {
    vscode.window.showInformationMessage(
      "Lattice AI: ask your Brain something first — evidence actions need a grounded answer.",
    );
    return;
  }
  let actions: EvidenceAction[];
  try {
    const payload = await c.evidenceActions(lastRecall.question, lastRecall.sourceIds);
    actions = parseEvidenceActions(payload);
  } catch (err: any) {
    vscode.window.showErrorMessage(`Lattice AI: evidence actions unavailable (${err?.message || err}).`);
    return;
  }
  if (!actions.length) {
    vscode.window.showInformationMessage(
      "Lattice AI: nothing can be built directly from this answer's sources.",
    );
    return;
  }
  const items: EvidenceActionItem[] = actions.map((action) => ({
    label: action.label,
    description: action.suggestedPath || undefined,
    action,
  }));
  const chosen = await vscode.window.showQuickPick(items, {
    placeHolder: `Build from ${lastRecall.sourceIds.length} cited source(s)`,
  });
  if (!chosen) return;
  if (chosen.action.kind === "file") {
    // A file action must actually produce a file — that is the agent's job.
    await runAgentLive(c, chosen.action.prompt);
    return;
  }
  if (context) ChatPanel.createOrShow(context.extensionUri, c);
  ChatPanel.sendMessage(chosen.action.prompt);
  vscode.window.showInformationMessage("Lattice AI: sent to chat with the cited evidence.");
}

/**
 * Live step timeline parity (v9.9.7): run the agent with `stream:true` and
 * write every named `agent_step` frame to the output channel as it happens,
 * instead of only reporting after the run ends.
 */
export async function runAgentLive(c: LatticeAIClient, presetGoal?: string): Promise<void> {
  const goal = presetGoal ?? await vscode.window.showInputBox({
    prompt: "What should the Lattice agent do? (live step timeline)",
    placeHolder: "e.g. write a README for this project",
  });
  if (!goal) return;
  const output = outputChannel();
  output.appendLine(`--- ${goal} ---`);
  output.show(true);
  try {
    const payload = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: "Lattice AI: agent running (live)..." },
      async () => c.runAgentLive(goal, (step) => output.appendLine(`  ${stepLine(step)}`)),
    );
    const status = String(payload?.status || "");
    if (status === "awaiting_approval" || status === "waiting_approval") {
      const approve = "Review approvals";
      const pick = await vscode.window.showInformationMessage(
        `Lattice AI: the plan needs approval.\n${String(payload?.approval?.plan_summary || "").slice(0, 300)}`,
        approve,
      );
      if (pick === approve) await showPendingApprovals(c, true);
      return;
    }
    output.appendLine(runReport(payload));
  } catch (err: any) {
    vscode.window.showErrorMessage(`Lattice AI: live agent run failed (${err?.message || err}).`);
  }
}

export async function openAppSurface(path: string): Promise<void> {
  const base = vscode.workspace.getConfiguration("ltcai").get<string>("serverUrl") || "http://localhost:4825";
  await vscode.env.openExternal(vscode.Uri.parse(`${base.replace(/\/$/, "")}${path}`));
}

interface ProposalPickItem extends vscode.QuickPickItem {
  proposal: ProposalSummary;
}

/**
 * Review Center parity: list staged change proposals and approve/reject them
 * without leaving the editor. Approval applies the staged content exactly as
 * reviewed; a 409 means the file drifted since staging, which is reported as
 * a conflict rather than retried behind the user's back.
 */
export async function showReviewCenter(c: LatticeAIClient): Promise<void> {
  let proposals: ProposalSummary[];
  try {
    proposals = parseProposals(await c.listProposals());
  } catch (err: any) {
    vscode.window.showErrorMessage(`Lattice AI: review center unavailable (${err?.message || err}).`);
    return;
  }
  if (!proposals.length) {
    vscode.window.showInformationMessage("Lattice AI: no pending change proposals.");
    return;
  }
  const items: ProposalPickItem[] = proposals.map((proposal) => ({
    label: proposal.title.slice(0, 80),
    description: proposal.changeClass || undefined,
    detail: proposal.path || proposal.id,
    proposal,
  }));
  const chosen = await vscode.window.showQuickPick(items, {
    placeHolder: `${proposals.length} pending change proposal(s) — pick one to review`,
    matchOnDetail: true,
  });
  if (!chosen) return;
  const decision = await vscode.window.showInformationMessage(
    `Apply “${chosen.proposal.title}”?${chosen.proposal.path ? `\n${chosen.proposal.path}` : ""}`,
    { modal: true },
    "Approve & apply",
    "Reject",
    "Open in Web UI",
  );
  if (decision === "Open in Web UI") {
    await openAppSurface("/app#/act");
    return;
  }
  if (!decision) return;
  try {
    if (decision === "Approve & apply") {
      const result = await c.approveProposal(chosen.proposal.id);
      vscode.window.showInformationMessage(
        `Lattice AI: applied — ${String(result?.path || chosen.proposal.path || chosen.proposal.id)}`,
      );
    } else {
      await c.rejectProposal(chosen.proposal.id, "rejected from VS Code");
      vscode.window.showInformationMessage("Lattice AI: proposal rejected.");
    }
  } catch (err: any) {
    if (err?.status === 409) {
      vscode.window.showWarningMessage(
        "Lattice AI: the file changed since this proposal was staged — nothing was written. Re-run the request to restage it.",
      );
      return;
    }
    vscode.window.showErrorMessage(`Lattice AI: proposal action failed (${err?.message || err}).`);
  }
}

/** Run an agent task and report its steps + plain-language outcome. */
export async function runAgentWithSummary(c: LatticeAIClient): Promise<void> {
  const goal = await vscode.window.showInputBox({
    prompt: "What should the Lattice agent do?",
    placeHolder: "e.g. write a README for this project",
  });
  if (!goal) return;
  try {
    const payload = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: "Lattice AI: running agent..." },
      async () => c.runAgent(goal, { human_in_loop: true }),
    );
    const status = String(payload?.status || "");
    if (status === "awaiting_approval" || status === "waiting_approval") {
      const approve = "Review approvals";
      const pick = await vscode.window.showInformationMessage(
        `Lattice AI: the plan needs approval.\n${String(payload?.approval?.plan_summary || "").slice(0, 300)}`,
        approve,
      );
      if (pick === approve) await showPendingApprovals(c, true);
      return;
    }
    const output = outputChannel();
    output.appendLine(`--- ${goal} ---`);
    output.appendLine(runReport(payload));
    // Artifact cards carry the honesty flags a flat file list drops: whether
    // the content was repaired, and whether the server validated it.
    const cards = parseArtifacts(payload);
    setLastArtifacts(cards.length ? { goal, cards } : null);
    output.appendLine(artifactReport(payload));
    output.show(true);
    if (cards.length) {
      const open = "Show artifacts";
      const pick = await vscode.window.showInformationMessage(
        `Lattice AI: ${cards.length} file(s) produced.`,
        open,
      );
      if (pick === open) await vscode.commands.executeCommand("ltcai.showArtifacts");
    }
  } catch (err: any) {
    vscode.window.showErrorMessage(`Lattice AI: agent run failed (${err?.message || err}).`);
  }
}


// ── Capture: send a whole folder to the Brain (v10.4.0) ──────────────────────
//
// SURFACE_PARITY had VS Code capture at ◐ because `sendToLattice` only ever
// pushed the current file. The web Capture view ingests folders through
// `/api/ingestion/folder`; this is the same endpoint and the same approval
// dance, driven from the editor.

export async function captureFolder(c: LatticeAIClient): Promise<void> {
  const folders = vscode.workspace.workspaceFolders ?? [];
  const picked = folders.length === 1
    ? folders[0].uri
    : (await vscode.window.showOpenDialog({
        canSelectFolders: true,
        canSelectFiles: false,
        canSelectMany: false,
        openLabel: "Send to Brain",
      }))?.[0];
  if (!picked) return;

  const folderPath = picked.fsPath;
  const recursive = (await vscode.window.showQuickPick(
    [
      { label: "Include subfolders", value: true },
      { label: "This folder only", value: false },
    ],
    { title: `Lattice AI — capture ${folderPath}` },
  ))?.value;
  if (recursive === undefined) return;

  try {
    // First call is unapproved on purpose: reading local disk requires an
    // explicit, per-path approval, exactly as in the web app.
    const probe = await c.ingestFolder({ path: folderPath, recursive, approved: false });
    const token = String(probe?.approval_token || probe?.token || "");
    if (!token) {
      const detail = String(probe?.message || probe?.detail || "");
      vscode.window.showWarningMessage(
        `Lattice AI: approve folder access in the web app first.${detail ? ` (${detail})` : ""}`,
      );
      return;
    }
    const confirm = await vscode.window.showWarningMessage(
      `Lattice AI will read and index ${folderPath}${recursive ? " and its subfolders" : ""}.`,
      { modal: true },
      "Index folder",
    );
    if (confirm !== "Index folder") return;

    const summary = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: "Lattice AI: capturing folder..." },
      async () => c.ingestFolder({
        path: folderPath,
        recursive,
        approved: true,
        approval_token: token,
        background: true,
      }),
    );
    const jobId = String(summary?.job_id || "");
    const queued = Number(summary?.queued ?? summary?.total ?? 0);
    const output = outputChannel();
    output.appendLine(`--- capture ${folderPath} ---`);
    output.appendLine(
      jobId
        ? `queued ${queued} file(s) as job ${jobId}; progress is visible in the web app`
        : JSON.stringify(summary),
    );
    output.show(true);
    vscode.window.showInformationMessage(
      jobId
        ? `Lattice AI: indexing ${queued} file(s) in the background.`
        : "Lattice AI: folder sent to the Brain.",
    );
  } catch (err: any) {
    vscode.window.showErrorMessage(`Lattice AI: folder capture failed (${err?.message || err}).`);
  }
}

// ── Artifact cards (v10.4.0) ─────────────────────────────────────────────────
//
// The web app shows produced files as cards with their honesty flags. The
// editor shows the same fields as a QuickPick, and opening one opens the real
// file — a rendering difference, not a contract difference.

export async function showArtifactCards(): Promise<void> {
  if (!lastArtifacts || !lastArtifacts.cards.length) {
    vscode.window.showInformationMessage(
      "Lattice AI: no artifacts from this session yet. Run an agent task first.",
    );
    return;
  }
  type Item = vscode.QuickPickItem & { card: ArtifactCard };
  const items: Item[] = lastArtifacts.cards.map((card) => ({
    label: card.label,
    description: card.detail,
    detail: card.path,
    card,
  }));
  const pick = await vscode.window.showQuickPick(items, {
    title: `Lattice AI — artifacts from "${lastArtifacts.goal}"`,
    placeHolder: "Open a produced file",
    matchOnDetail: true,
  });
  if (!pick) return;

  const roots = vscode.workspace.workspaceFolders ?? [];
  if (!roots.length) {
    vscode.window.showWarningMessage(`Lattice AI: ${pick.card.path} (no workspace folder open).`);
    return;
  }
  const target = vscode.Uri.joinPath(roots[0].uri, pick.card.path);
  try {
    const doc = await vscode.workspace.openTextDocument(target);
    await vscode.window.showTextDocument(doc);
  } catch {
    // The agent workspace is not always the editor workspace; say where the
    // file is rather than pretending the open succeeded.
    vscode.window.showWarningMessage(
      `Lattice AI: ${pick.card.path} was written by the agent but is not in this workspace folder.`,
    );
  }
}
