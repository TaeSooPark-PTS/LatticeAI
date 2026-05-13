import * as vscode from "vscode";
import { ChatPanel } from "./ChatPanel";
import { ConnectAIClient } from "./client";
import { ModelPicker } from "./modelPicker";

let client: ConnectAIClient;
let statusBar: vscode.StatusBarItem;

export async function activate(context: vscode.ExtensionContext) {
  const config = vscode.workspace.getConfiguration("connectai");
  const serverUrl: string = config.get("serverUrl") ?? "http://localhost:4825";

  client = new ConnectAIClient(serverUrl);

  // ── Status Bar ───────────────────────────────────────────────────────────
  statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusBar.command = "connectai.loadModel";
  statusBar.text = "$(loading~spin) Connect AI";
  statusBar.show();
  context.subscriptions.push(statusBar);

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
    vscode.commands.registerCommand("connectai.chat", () => {
      ChatPanel.createOrShow(context.extensionUri, client);
    }),

    vscode.commands.registerCommand("connectai.loadModel", async () => {
      const picker = new ModelPicker(client);
      const chosen = await picker.show();
      if (chosen) {
        await loadModelWithProgress(chosen);
      }
    }),

    vscode.commands.registerCommand("connectai.editSelection", async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) return;
      const selection = editor.selection;
      const selectedText = editor.document.getText(selection);
      if (!selectedText) {
        vscode.window.showWarningMessage("Connect AI: No text selected.");
        return;
      }

      const instruction = await vscode.window.showInputBox({
        prompt: "How should I edit this code?",
        placeHolder: "e.g. Add type annotations, Refactor to async/await, Add error handling...",
      });
      if (!instruction) return;

      const message = `Edit this code as instructed.\n\nInstruction: ${instruction}\n\nCode:\n\`\`\`\n${selectedText}\n\`\`\`\n\nReturn ONLY the edited code, no explanation.`;
      await streamIntoEditor(message, editor, selection);
    }),

    vscode.commands.registerCommand("connectai.explainSelection", async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) return;
      const selectedText = editor.document.getText(editor.selection);
      if (!selectedText) return;

      ChatPanel.createOrShow(context.extensionUri, client);
      ChatPanel.sendMessage(`Explain this code:\n\`\`\`\n${selectedText}\n\`\`\``);
    }),

    vscode.commands.registerCommand("connectai.createFile", async () => {
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
        { location: vscode.ProgressLocation.Notification, title: `Connect AI: Creating ${filename}...` },
        async () => {
          const result = await client.generate(message);
          await vscode.workspace.fs.writeFile(filePath, Buffer.from(result, "utf-8"));
          const doc = await vscode.workspace.openTextDocument(filePath);
          await vscode.window.showTextDocument(doc);
        }
      );
    }),

    vscode.commands.registerCommand("connectai.runTerminal", async () => {
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
        let terminal = vscode.window.activeTerminal ?? vscode.window.createTerminal("Connect AI");
        terminal.show();
        terminal.sendText(command);
      } else if (confirm === "Copy only") {
        await vscode.env.clipboard.writeText(command);
        vscode.window.showInformationMessage("Command copied to clipboard.");
      }
    }),

    vscode.commands.registerCommand("connectai.garden", async () => {
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

    vscode.commands.registerCommand("connectai.showGarden", async () => {
      const tree = await client.gardenTree();
      const panel = vscode.window.createWebviewPanel("gardenTree", "Knowledge Garden", vscode.ViewColumn.Beside, {});
      panel.webview.html = renderGardenHTML(tree);
    })
  );

  console.log("Connect AI MLX extension activated.");
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
      title: `Connect AI: Loading ${shortName(modelId)}...`,
      cancellable: false,
    },
    async () => {
      await client.loadModel(modelId);
      updateStatusBar(modelId);
    }
  );
  vscode.window.showInformationMessage(`✅ Connect AI: ${shortName(modelId)} ready`);
}

function updateStatusBar(modelId: string | null) {
  if (modelId) {
    statusBar.text = `$(brain) ${shortName(modelId)}`;
    statusBar.tooltip = `Connect AI MLX — ${modelId}\nClick to switch model`;
  } else {
    statusBar.text = `$(brain) Connect AI — No model`;
    statusBar.tooltip = "Connect AI MLX — Click to load a model";
  }
}

function shortName(modelId: string): string {
  return modelId.split("/").pop()?.replace(/-4bit$/, "").slice(0, 28) ?? modelId;
}

async function streamIntoEditor(
  message: string,
  editor: vscode.TextEditor,
  selection: vscode.Selection
) {
  let accumulated = "";
  await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: "Connect AI: Editing..." },
    async () => {
      for await (const chunk of client.streamGenerate(message)) {
        accumulated += chunk;
      }
    }
  );

  // 코드 블록 펜스 제거
  const cleaned = accumulated.trim()
    .replace(/^```[a-z]*\n?/, "")
    .replace(/\n?```$/, "");

  await editor.edit((eb) => eb.replace(selection, cleaned));
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
