/**
 * Command registration.
 *
 * One place that maps every `ltcai.*` id in package.json to the function that
 * runs it. Keeping the map here — and the work in the surface modules — means
 * a missing `activationEvents` entry or a renamed command shows up as a diff in
 * one short file instead of somewhere in a thousand-line activate().
 */
import * as vscode from "vscode";
import { ChatPanel } from "./ChatPanel";
import { ModelPicker } from "./modelPicker";
import {
  captureFolder,
  runAgentLive,
  runAgentWithSummary,
  showArtifactCards,
  showEvidenceActions,
  showRecallGrounding,
  showReviewCenter,
} from "./agentSurface";
import { rejectPendingApproval, showPendingApprovals } from "./approvals";
import { diffEditSelection, renderGardenHTML, stripCodeFences } from "./editorActions";
import { client, extensionVersion } from "./extensionState";
import {
  currentWorkspaceFolder,
  loadModelWithProgress,
  refreshSyncStatus,
  reportSyncStatus,
  updateSyncStatus,
} from "./syncStatus";

export function registerCommands(context: vscode.ExtensionContext): void {
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
}
