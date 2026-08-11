/**
 * Everything that writes into the editor itself.
 *
 * The diff flow keeps the proposed text in an in-memory virtual document
 * (`ltcai-diff:`) so VS Code's own diff viewer can show it before a single byte
 * of the user's file changes — the provider and the map it reads are one unit
 * and stay in one module.
 */
import * as vscode from "vscode";
import { client } from "./extensionState";

const _diffDocs = new Map<string, string>();

export class DiffContentProvider implements vscode.TextDocumentContentProvider {
  provideTextDocumentContent(uri: vscode.Uri): string {
    return _diffDocs.get(uri.toString()) ?? "";
  }
}

export function stripCodeFences(text: string): string {
  return text.trim()
    .replace(/^```[a-zA-Z0-9_-]*\n?/, "")
    .replace(/\n?```$/, "");
}

export async function streamIntoEditor(
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

export async function diffEditSelection(
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

export function renderGardenHTML(tree: any): string {
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
