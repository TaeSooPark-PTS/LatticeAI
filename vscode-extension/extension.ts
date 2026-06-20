import * as vscode from "vscode";
import { ChatPanel } from "./ChatPanel";
import { LatticeAIClient } from "./client";
import { ModelPicker } from "./modelPicker";

let client: LatticeAIClient;
let statusBar: vscode.StatusBarItem;
let syncStatusBar: vscode.StatusBarItem;
let extensionVersion = "";
let syncTimer: NodeJS.Timeout | undefined;

const _diffDocs = new Map<string, string>();

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
      ChatPanel.createOrShow(context.extensionUri, client);
      ChatPanel.sendMessage([
        question,
        "",
        `Current file: ${editor.document.fileName}`,
        `Language: ${editor.document.languageId}`,
        "```",
        `// ${fileName}`,
        editor.document.getText().slice(0, 12000),
        "```",
      ].join("\n"));
      await client.sendToWorkspace({
        action: "ask_current_file",
        file_path: editor.document.fileName,
        language: editor.document.languageId,
        prompt: question,
        extension_version: extensionVersion,
        workspace_folder: currentWorkspaceFolder(),
      });
      await reportSyncStatus("synced", "synced", editor.document.fileName);
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
    })
  );

  void refreshSyncStatus(false);
  syncTimer = setInterval(() => void refreshSyncStatus(false), 20000);
  context.subscriptions.push({ dispose: () => syncTimer && clearInterval(syncTimer) });

  console.log("Lattice AI Knowledge OS extension activated.");
}

// ── Helpers ──────────────────────────────────────────────────────────────────

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
