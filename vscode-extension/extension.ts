/**
 * Extension entry point.
 *
 * Everything this file does is start-up: build the client, put the two status
 * bar items on screen, register the commands, and park the sync poller. The
 * command bodies live in `commands.ts`, the surfaces they drive in
 * `agentSurface.ts` / `approvals.ts` / `editorActions.ts`, and the state they
 * share in `extensionState.ts`. `activate` and `deactivate` stay here because
 * `package.json` points `main` at this module.
 */
import * as vscode from "vscode";
import { LatticeAIClient } from "./client";
import { registerCommands } from "./commands";
import { DiffContentProvider } from "./editorActions";
import {
  setClient,
  setExtensionVersion,
  setStatusBar,
  setSyncStatusBar,
  setSyncTimer,
  syncTimer,
} from "./extensionState";
import { loadModelSilently, refreshSyncStatus, updateStatusBar } from "./syncStatus";

export async function activate(context: vscode.ExtensionContext) {
  const config = vscode.workspace.getConfiguration("ltcai");
  const serverUrl: string = config.get("serverUrl") ?? "http://localhost:4825";
  setExtensionVersion(String(context.extension.packageJSON?.version || ""));

  setClient(new LatticeAIClient(serverUrl));
  context.subscriptions.push(
    vscode.workspace.registerTextDocumentContentProvider("ltcai-diff", new DiffContentProvider())
  );

  // ── Status Bar ───────────────────────────────────────────────────────────
  const statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  setStatusBar(statusBar);
  statusBar.command = "ltcai.loadModel";
  statusBar.text = "$(loading~spin) Lattice AI";
  statusBar.show();
  context.subscriptions.push(statusBar);

  const syncStatusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 99);
  setSyncStatusBar(syncStatusBar);
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
  registerCommands(context);

  void refreshSyncStatus(false);
  setSyncTimer(setInterval(() => void refreshSyncStatus(false), 20000));
  context.subscriptions.push({ dispose: () => syncTimer && clearInterval(syncTimer) });

  console.log("Lattice AI Knowledge OS extension activated.");
}

export function deactivate() { }
