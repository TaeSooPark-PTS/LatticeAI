import * as vscode from "vscode";
import { ChatPanel } from "./ChatPanel";
import { LatticeAIClient } from "./client";
import { ModelPicker } from "./modelPicker";
import {
  artifactReport,
  citedSourceIds,
  groundingBadge,
  groundingLine,
  parseArtifacts,
  parseEvidenceActions,
  parseModelRecommendation,
  parseProposals,
  runReport,
  stepLine,
  type ArtifactCard,
  type EvidenceAction,
  type ProposalSummary,
} from "./surface";

let client: LatticeAIClient;
let statusBar: vscode.StatusBarItem;
let syncStatusBar: vscode.StatusBarItem;
let extensionVersion = "";
let syncTimer: NodeJS.Timeout | undefined;
let agentOutput: vscode.OutputChannel | undefined;

// Last recall's question + cited source ids, so "이 근거로 만들기" has real
// evidence to act on. Cleared implicitly by the next recall; never persisted.
let lastRecall: { question: string; sourceIds: string[] } | null = null;

// Artifacts from the most recent agent run, so "Show Artifacts" can open them
// as cards after the run notification is gone. In-memory only.
let lastArtifacts: { goal: string; cards: ArtifactCard[] } | null = null;

const _diffDocs = new Map<string, string>();

function outputChannel(): vscode.OutputChannel {
  if (!agentOutput) agentOutput = vscode.window.createOutputChannel("Lattice AI");
  return agentOutput;
}

class DiffContentProvider implements vscode.TextDocumentContentProvider {
  provideTextDocumentContent(uri: vscode.Uri): string {
    return _diffDocs.get(uri.toString()) ?? "";
  }
}

export async function activate(context: vscode.ExtensionContext) {
  const config = vscode.workspace.getConfiguration("ltcai");
  const serverUrl: string = config.get("serverUrl") ?? "http://localhost:4825";
  extensionVersion = String(context.extension.packageJSON?.version || "");

  client = new LatticeAIClient(serverUrl);
  context.subscriptions.push(
    vscode.workspace.registerTextDocumentContentProvider("ltcai-diff", new DiffContentProvider())
  );

  // ── Status Bar ───────────────────────────────────────────────────────────
  statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusBar.command = "ltcai.loadModel";
  statusBar.text = "$(loading~spin) Lattice AI";
  statusBar.show();
  context.subscriptions.push(statusBar);

  syncStatusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 99);
  syncStatusBar.command = "ltcai.showSyncStatus";
  syncStatusBar.text = "$(sync~spin) Lattice sync";
  syncStatusBar.tooltip = "Checking Lattice AI workspace sync...";
  syncStatusBar.show();
  context.subscriptions.push(syncStatusBar);

  // ── Auto-load model ──────────────────────────────────────────────────────
  if (config.get("autoLoadModel")) {
    const defaultModel = config.get<string>("defaultModel") ?? "";
    if (defaultModel) {
      loadModelSilently(defaultModel);
    }
  } else {
    updateStatusBar(null);
  }

  // ── Commands ─────────────────────────────────────────────────────────────

  context.subscriptions.push(
    vscode.commands.registerCommand("ltcai.chat", () => {
      ChatPanel.createOrShow(context.extensionUri, client);
    }),

    vscode.commands.registerCommand("ltcai.loadModel", async () => {
      const picker = new ModelPicker(client);
      const chosen = await picker.show();
      if (chosen) {
        await loadModelWithProgress(chosen);
      }
    }),

    vscode.commands.registerCommand("ltcai.editSelection", async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) return;
      const selection = editor.selection;
      const selectedText = editor.document.getText(selection);
      if (!selectedText) {
        vscode.window.showWarningMessage("Lattice AI: No text selected.");
        return;
      }

      const instruction = await vscode.window.showInputBox({
        prompt: "How should I edit this code?",
        placeHolder: "e.g. Add type annotations, Refactor to async/await, Add error handling...",
      });
      if (!instruction) return;

      const message = `Edit this code as instructed.\n\nInstruction: ${instruction}\n\nCode:\n\`\`\`\n${selectedText}\n\`\`\`\n\nReturn ONLY the edited code, no explanation.`;
      await diffEditSelection(message, editor, selection);
    }),

    vscode.commands.registerCommand("ltcai.attachFile", async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) {
        vscode.window.showWarningMessage("Lattice AI: No active editor.");
        return;
      }
      const content = editor.document.getText();
      const lang = editor.document.languageId;
      const fileName = editor.document.fileName.split("/").pop() ?? "file";
      ChatPanel.createOrShow(context.extensionUri, client);
      ChatPanel.sendMessage(`\`\`\`${lang}\n// ${fileName}\n${content}\n\`\`\``);
    }),

    vscode.commands.registerCommand("ltcai.explainSelection", async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) return;
      const selectedText = editor.document.getText(editor.selection);
      if (!selectedText) return;

      ChatPanel.createOrShow(context.extensionUri, client);
      ChatPanel.sendMessage(`Explain this code:\n\`\`\`\n${selectedText}\n\`\`\``);
    }),

    vscode.commands.registerCommand("ltcai.refactorSelection", async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) return;
      const selection = editor.selection;
      const selectedText = editor.document.getText(selection);
      if (!selectedText) {
        vscode.window.showWarningMessage("Lattice AI: No text selected.");
        return;
      }
      const instruction = await vscode.window.showInputBox({
        prompt: "Refactor goal",
        placeHolder: "e.g. simplify control flow, extract helper, improve naming",
      });
      const message = [
        "Refactor this selection while preserving behavior.",
        instruction ? `Goal: ${instruction}` : "Goal: improve maintainability.",
        `Language: ${editor.document.languageId}`,
        "Return ONLY the refactored code, no markdown fences.",
        "Code:",
        "```",
        selectedText,
        "```",
      ].join("\n");
      await diffEditSelection(message, editor, selection);
    }),

    vscode.commands.registerCommand("ltcai.generateTests", async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) return;
      const selectedText = editor.document.getText(editor.selection);
      const source = selectedText || editor.document.getText();
      const fileName = editor.document.fileName.split("/").pop() ?? "current file";
      const message = [
        `Generate focused tests for ${fileName}.`,
        `Language: ${editor.document.languageId}`,
        "Return ONLY test code, no markdown fences.",
        "Source:",
        "```",
        source.slice(0, 12000),
        "```",
      ].join("\n");
      await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: "Lattice AI: Generating tests..." },
        async () => {
          const generated = await client.generate(message);
          const doc = await vscode.workspace.openTextDocument({
            language: editor.document.languageId,
            content: stripCodeFences(generated),
          });
          await vscode.window.showTextDocument(doc, vscode.ViewColumn.Beside);
        }
      );
    }),

    vscode.commands.registerCommand("ltcai.sendToLattice", async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) {
        vscode.window.showWarningMessage("Lattice AI: No active editor.");
        return;
      }
      const selection = editor.document.getText(editor.selection);
      const content = editor.document.getText();
      updateSyncStatus("indexing", "Sending current file to Lattice...");
      await client.sendToWorkspace({
        action: "send_to_lattice",
        file_path: editor.document.fileName,
        language: editor.document.languageId,
        content,
        selection,
        extension_version: extensionVersion,
        workspace_folder: currentWorkspaceFolder(),
      });
      await reportSyncStatus("synced", "synced", editor.document.fileName);
      vscode.window.showInformationMessage("Lattice AI: Sent to Workspace OS.");
    }),

    vscode.commands.registerCommand("ltcai.askCurrentFile", async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) return;
      const question = await vscode.window.showInputBox({
        prompt: "Ask Lattice AI about this file",
        placeHolder: "What should I understand or change here?",
      });
      if (!question) return;
      const fileName = editor.document.fileName.split("/").pop() ?? "file";
      const message = [
        question,
        "",
        `Current file: ${editor.document.fileName}`,
        `Language: ${editor.document.languageId}`,
        "```",
        `// ${fileName}`,
        editor.document.getText().slice(0, 12000),
        "```",
      ].join("\n");
      ChatPanel.createOrShow(context.extensionUri, client);
      ChatPanel.sendMessage(message);
      await client.sendToWorkspace({
        action: "ask_current_file",
        file_path: editor.document.fileName,
        language: editor.document.languageId,
        prompt: question,
        extension_version: extensionVersion,
        workspace_folder: currentWorkspaceFolder(),
      });
      await reportSyncStatus("synced", "synced", editor.document.fileName);
      // Recall parity (SURFACE_PARITY v9.9.6): the web app badges every
      // answer with the server's grounding verdict. Ask the same /chat
      // surface for the verdict on this question so the editor is not the
      // one place that hides whether the answer used the Brain.
      await showRecallGrounding(question, message);
    }),

    vscode.commands.registerCommand("ltcai.askBrain", async () => {
      const question = await vscode.window.showInputBox({
        prompt: "Ask your Brain",
        placeHolder: "Anything your Brain already knows",
      });
      if (!question) return;
      ChatPanel.createOrShow(context.extensionUri, client);
      ChatPanel.sendMessage(question);
      await showRecallGrounding(question, question);
    }),

    vscode.commands.registerCommand("ltcai.createFile", async () => {
      const description = await vscode.window.showInputBox({
        prompt: "Describe the file to create",
        placeHolder: "e.g. Python FastAPI server with /health and /chat endpoints",
      });
      if (!description) return;

      const filename = await vscode.window.showInputBox({
        prompt: "Filename (with extension)",
        placeHolder: "e.g. server.py",
      });
      if (!filename) return;

      const workspaceFolders = vscode.workspace.workspaceFolders;
      if (!workspaceFolders) {
        vscode.window.showErrorMessage("No workspace folder open.");
        return;
      }

      const filePath = vscode.Uri.joinPath(workspaceFolders[0].uri, filename);
      const message = `Create a complete, production-ready ${filename} file.\nDescription: ${description}\nReturn ONLY the file content, no explanation, no markdown fences.`;

      await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: `Lattice AI: Creating ${filename}...` },
        async () => {
          const result = await client.generate(message);
          await vscode.workspace.fs.writeFile(filePath, Buffer.from(result, "utf-8"));
          const doc = await vscode.workspace.openTextDocument(filePath);
          await vscode.window.showTextDocument(doc);
        }
      );
    }),

    vscode.commands.registerCommand("ltcai.runTerminal", async () => {
      const description = await vscode.window.showInputBox({
        prompt: "What do you want to run in the terminal?",
        placeHolder: "e.g. Install dependencies, run tests, build the project",
      });
      if (!description) return;

      const result = await client.generate(
        `Generate a single terminal command (bash/zsh) for macOS to: ${description}.\nReturn ONLY the command, nothing else.`
      );
      const command = result.trim().replace(/^```[a-z]*\n?/, "").replace(/\n?```$/, "");

      const confirm = await vscode.window.showInformationMessage(
        `Run: ${command}`,
        { modal: true },
        "Run",
        "Copy only"
      );

      if (confirm === "Run") {
        let terminal = vscode.window.activeTerminal ?? vscode.window.createTerminal("Lattice AI");
        terminal.show();
        terminal.sendText(command);
      } else if (confirm === "Copy only") {
        await vscode.env.clipboard.writeText(command);
        vscode.window.showInformationMessage("Command copied to clipboard.");
      }
    }),

    vscode.commands.registerCommand("ltcai.garden", async () => {
      const editor = vscode.window.activeTextEditor;
      const rawData = editor?.document.getText(editor.selection) ?? "";
      if (!rawData) {
        vscode.window.showWarningMessage("Select text to save to the Knowledge Garden.");
        return;
      }
      const result = await client.garden(rawData);
      vscode.window.showInformationMessage(
        `📚 Saved to ${result.folder}/${result.filename}`
      );
    }),

    vscode.commands.registerCommand("ltcai.showGarden", async () => {
      const tree = await client.gardenTree();
      const panel = vscode.window.createWebviewPanel("gardenTree", "Knowledge Garden", vscode.ViewColumn.Beside, {});
      panel.webview.html = renderGardenHTML(tree);
    }),

    vscode.commands.registerCommand("ltcai.showSyncStatus", async () => {
      await refreshSyncStatus(true);
    }),

    // ── Agent approval flow (SURFACE_PARITY, v9.9.5) ─────────────────────
    vscode.commands.registerCommand("ltcai.listApprovals", async () => {
      await showPendingApprovals(client, false);
    }),

    vscode.commands.registerCommand("ltcai.approveAgent", async () => {
      await showPendingApprovals(client, true);
    }),

    vscode.commands.registerCommand("ltcai.rejectAgent", async () => {
      await rejectPendingApproval(client);
    }),

    // ── Review Center + agent run summary (SURFACE_PARITY, v9.9.6) ───────
    vscode.commands.registerCommand("ltcai.reviewCenter", async () => {
      await showReviewCenter(client);
    }),

    vscode.commands.registerCommand("ltcai.runAgent", async () => {
      await runAgentWithSummary(client);
    }),

    // ── Evidence → action + live step timeline (SURFACE_PARITY, v9.9.7) ──
    vscode.commands.registerCommand("ltcai.evidenceActions", async () => {
      await showEvidenceActions(client, context);
    }),

    vscode.commands.registerCommand("ltcai.runAgentLive", async () => {
      await runAgentLive(client);
    }),

    // ── v10.4.0 surface parity ───────────────────────────────────────────
    vscode.commands.registerCommand("ltcai.captureFolder", async () => {
      await captureFolder(client);
    }),

    vscode.commands.registerCommand("ltcai.showArtifacts", async () => {
      await showArtifactCards();
    })
  );

  void refreshSyncStatus(false);
  syncTimer = setInterval(() => void refreshSyncStatus(false), 20000);
  context.subscriptions.push({ dispose: () => syncTimer && clearInterval(syncTimer) });

  console.log("Lattice AI Knowledge OS extension activated.");
}

// ── Helpers ──────────────────────────────────────────────────────────────────

/** Ask /chat for the grounding verdict on a recall and report it honestly. */
async function showRecallGrounding(question: string, message: string): Promise<void> {
  try {
    const payload = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Window, title: "Lattice AI: checking evidence..." },
      async () => client.chat(message),
    );
    const line = groundingLine(payload);
    // Remember what this answer actually cited so "이 근거로 만들기" has real
    // evidence to work from (SURFACE_PARITY v9.9.7).
    const sourceIds = citedSourceIds(payload);
    lastRecall = { question, sourceIds };
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
async function showEvidenceActions(
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
async function runAgentLive(c: LatticeAIClient, presetGoal?: string): Promise<void> {
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

async function openAppSurface(path: string): Promise<void> {
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
async function showReviewCenter(c: LatticeAIClient): Promise<void> {
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
async function runAgentWithSummary(c: LatticeAIClient): Promise<void> {
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
    lastArtifacts = cards.length ? { goal, cards } : null;
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

async function captureFolder(c: LatticeAIClient): Promise<void> {
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

async function showArtifactCards(): Promise<void> {
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

interface PendingApprovalItem extends vscode.QuickPickItem {
  runId: string;
  token: string;
  planSummary: string;
}

async function fetchPendingApprovals(c: LatticeAIClient): Promise<PendingApprovalItem[]> {
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

async function resolveToken(c: LatticeAIClient, item: PendingApprovalItem): Promise<string | undefined> {
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

async function showPendingApprovals(c: LatticeAIClient, approveSelected: boolean) {
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

async function rejectPendingApproval(c: LatticeAIClient) {
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

async function loadModelSilently(modelId: string) {
  try {
    statusBar.text = `$(loading~spin) Loading ${shortName(modelId)}...`;
    await client.loadModel(modelId);
    updateStatusBar(modelId);
  } catch {
    updateStatusBar(null);
  }
}

async function loadModelWithProgress(modelId: string) {
  await vscode.window.withProgress(
    {
      location: vscode.ProgressLocation.Notification,
      title: `Lattice AI: Loading ${shortName(modelId)}...`,
      cancellable: false,
    },
    async () => {
      await client.loadModel(modelId);
      updateStatusBar(modelId);
    }
  );
  vscode.window.showInformationMessage(`✅ Lattice AI: ${shortName(modelId)} ready`);
}

function updateStatusBar(modelId: string | null) {
  if (modelId) {
    statusBar.text = `$(brain) ${shortName(modelId)}`;
    statusBar.tooltip = `Lattice AI — ${modelId}\nClick to switch model`;
  } else {
    statusBar.text = `$(brain) Lattice AI — No model`;
    statusBar.tooltip = "Lattice AI — Click to load a model";
  }
}

function currentWorkspaceFolder(): string {
  return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || "";
}

function activeFile(): string {
  return vscode.window.activeTextEditor?.document.fileName || "";
}

function updateSyncStatus(state: "checking" | "connected" | "indexing" | "synced" | "offline", detail?: string) {
  if (!syncStatusBar) return;
  const icon =
    state === "checking" ? "$(sync~spin)" :
    state === "offline" ? "$(circle-slash)" :
    state === "indexing" ? "$(loading~spin)" :
    state === "synced" ? "$(check)" :
    "$(plug)";
  const label =
    state === "offline" ? "Lattice offline" :
    state === "indexing" ? "Lattice indexing" :
    state === "synced" ? "Lattice synced" :
    state === "checking" ? "Lattice sync" :
    "Lattice connected";
  syncStatusBar.text = `${icon} ${label}`;
  syncStatusBar.tooltip = [
    "Lattice AI VS Code bridge",
    `State: ${state}`,
    currentWorkspaceFolder() ? `Workspace: ${currentWorkspaceFolder()}` : "Workspace: none",
    activeFile() ? `Active file: ${activeFile()}` : "Active file: none",
    detail ? `Detail: ${detail}` : "",
    "Click for current app sync status.",
  ].filter(Boolean).join("\n");
}

async function reportSyncStatus(status: "connected" | "indexing" | "synced", indexStatus = "unknown", filePath = activeFile()) {
  try {
    const result = await client.reportWorkspaceStatus({
      status,
      index_status: indexStatus,
      workspace_folder: currentWorkspaceFolder(),
      extension_version: extensionVersion,
      active_file: filePath,
    });
    updateSyncStatus(status, String(result.detail || result.status || ""));
  } catch (error) {
    updateSyncStatus("offline", error instanceof Error ? error.message : String(error));
  }
}

async function refreshSyncStatus(showMessage: boolean) {
  updateSyncStatus("checking");
  try {
    await client.reportWorkspaceStatus({
      status: "connected",
      index_status: "checking",
      workspace_folder: currentWorkspaceFolder(),
      extension_version: extensionVersion,
      active_file: activeFile(),
    });
    const status = await client.workspaceStatus();
    const connected = Boolean(status.connected);
    const indexStatus = String(status.index_status || "unknown");
    const next =
      !connected ? "offline" :
      /index|build|running|pending/i.test(indexStatus) ? "indexing" :
      /sync|ok|ready|connected/i.test(indexStatus) ? "synced" :
      "connected";
    updateSyncStatus(next as "connected" | "indexing" | "synced" | "offline", indexStatus);
    if (showMessage) {
      vscode.window.showInformationMessage(`Lattice AI sync: ${next} (${indexStatus})`);
    }
  } catch (error) {
    updateSyncStatus("offline", error instanceof Error ? error.message : String(error));
    if (showMessage) {
      vscode.window.showWarningMessage(`Lattice AI sync unavailable: ${error instanceof Error ? error.message : String(error)}`);
    }
  }
}

function shortName(modelId: string): string {
  return modelId.split("/").pop()?.replace(/-4bit$/, "").slice(0, 28) ?? modelId;
}

function stripCodeFences(text: string): string {
  return text.trim()
    .replace(/^```[a-zA-Z0-9_-]*\n?/, "")
    .replace(/\n?```$/, "");
}

async function streamIntoEditor(
  message: string,
  editor: vscode.TextEditor,
  selection: vscode.Selection
) {
  let accumulated = "";
  await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: "Lattice AI: Editing..." },
    async () => {
      for await (const chunk of client.streamGenerate(message)) {
        accumulated += chunk;
      }
    }
  );

  const cleaned = accumulated.trim()
    .replace(/^```[a-z]*\n?/, "")
    .replace(/\n?```$/, "");

  await editor.edit((eb) => eb.replace(selection, cleaned));
}

async function diffEditSelection(
  message: string,
  editor: vscode.TextEditor,
  selection: vscode.Selection
) {
  let accumulated = "";
  await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: "Lattice AI: Generating edit..." },
    async () => {
      for await (const chunk of client.streamGenerate(message)) {
        accumulated += chunk;
      }
    }
  );

  const cleaned = accumulated.trim()
    .replace(/^```[a-z]*\n?/, "")
    .replace(/\n?```$/, "");

  const originalUri = editor.document.uri;
  const lang = editor.document.languageId;
  const selectedText = editor.document.getText(selection);

  // Write proposed edit to a temp virtual document for diff view
  const proposedUri = originalUri.with({ scheme: "ltcai-diff", path: originalUri.path + ".proposed" });
  const originalFull = editor.document.getText();
  const proposedFull = originalFull.slice(0, editor.document.offsetAt(selection.start))
    + cleaned
    + originalFull.slice(editor.document.offsetAt(selection.end));

  _diffDocs.set(proposedUri.toString(), proposedFull);

  await vscode.commands.executeCommand(
    "vscode.diff",
    originalUri,
    proposedUri,
    `Lattice AI Edit — ${originalUri.path.split("/").pop()}`
  );

  const choice = await vscode.window.showInformationMessage(
    "Apply this edit?",
    { modal: false },
    "Apply",
    "Discard"
  );
  _diffDocs.delete(proposedUri.toString());

  if (choice === "Apply") {
    await editor.edit((eb) => eb.replace(selection, cleaned));
    await vscode.commands.executeCommand("workbench.action.closeActiveEditor");
  }
}

function renderGardenHTML(tree: any): string {
  const rows = Object.entries(tree.structure)
    .map(([folder, info]: [string, any]) => `
      <tr>
        <td><b>${folder}</b></td>
        <td>${info.description}</td>
        <td>${info.count} files</td>
        <td>${info.recent.slice(0, 3).join("<br>")}</td>
      </tr>`)
    .join("");

  return `<!DOCTYPE html><html><body style="font-family:monospace;padding:16px">
    <h2>🧠 Knowledge Garden</h2>
    <p><code>${tree.brain_dir}</code></p>
    <table border="1" cellpadding="6" style="border-collapse:collapse;width:100%">
      <thead><tr><th>Folder</th><th>Purpose</th><th>Files</th><th>Recent</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></body></html>`;
}

export function deactivate() { }
