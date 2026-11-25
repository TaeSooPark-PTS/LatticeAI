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
  const readClientConfig = () => {
    const cfg = vscode.workspace.getConfiguration("ltcai");
    const url = cfg.get<string>("serverUrl") ?? "http://localhost:4825";
    const token =
      cfg.get<string>("authToken")?.trim() ||
      process.env.LATTICEAI_API_TOKEN?.trim() ||
      undefined;
    return { cfg, url, token };
  };

  const { cfg: config, url: serverUrl, token: authToken } = readClientConfig();
  setExtensionVersion(String(context.extension.packageJSON?.version || ""));

  const client = new LatticeAIClient(serverUrl, authToken);
  setClient(client);
  context.subscriptions.push(
    vscode.workspace.registerTextDocumentContentProvider("ltcai-diff", new DiffContentProvider()),
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (
        event.affectsConfiguration("ltcai.serverUrl") ||
        event.affectsConfiguration("ltcai.authToken")
      ) {
        const next = readClientConfig();
        client.setBaseUrl(next.url);
        client.setAuthToken(next.token);
        void refreshSyncStatus(false);
      }
    })
  );

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

  const defaultModel = (config.get<string>("defaultModel") ?? "").trim();
  if (config.get<boolean>("autoLoadModel") && defaultModel) {
    void loadModelSilently(defaultModel).catch((err) => {
      console.warn("Lattice AI auto-load model failed:", err);
      updateStatusBar(null);
    });
  } else {
    updateStatusBar(null);
  }

  registerCommands(context);

  let syncInFlight = false;
  const runSyncProbe = () => {
    if (syncInFlight) return;
    syncInFlight = true;
    void refreshSyncStatus(false)
      .catch((err) => console.warn("Lattice AI sync status refresh failed:", err))
      .finally(() => {
        syncInFlight = false;
      });
  };
  runSyncProbe();
  setSyncTimer(setInterval(runSyncProbe, 20_000));
  context.subscriptions.push({ dispose: () => syncTimer && clearInterval(syncTimer) });

  console.log("Lattice AI Knowledge OS extension activated.");
}

export function deactivate() { }
