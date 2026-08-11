/**
 * Status bar + workspace sync reporting.
 *
 * Two status bar items and everything that writes to them: the model badge
 * (`$(brain) …`) and the sync badge (`$(check) Lattice synced`). Both read the
 * live bindings in `extensionState`, so the commands that trigger a sync do not
 * have to thread the items through every call.
 */
import * as vscode from "vscode";
import { client, extensionVersion, statusBar, syncStatusBar } from "./extensionState";

export async function loadModelSilently(modelId: string) {
  try {
    statusBar.text = `$(loading~spin) Loading ${shortName(modelId)}...`;
    await client.loadModel(modelId);
    updateStatusBar(modelId);
  } catch {
    updateStatusBar(null);
  }
}

export async function loadModelWithProgress(modelId: string) {
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

export function updateStatusBar(modelId: string | null) {
  if (modelId) {
    statusBar.text = `$(brain) ${shortName(modelId)}`;
    statusBar.tooltip = `Lattice AI — ${modelId}\nClick to switch model`;
  } else {
    statusBar.text = `$(brain) Lattice AI — No model`;
    statusBar.tooltip = "Lattice AI — Click to load a model";
  }
}

export function currentWorkspaceFolder(): string {
  return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || "";
}

export function activeFile(): string {
  return vscode.window.activeTextEditor?.document.fileName || "";
}

export function updateSyncStatus(state: "checking" | "connected" | "indexing" | "synced" | "offline", detail?: string) {
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

export async function reportSyncStatus(status: "connected" | "indexing" | "synced", indexStatus = "unknown", filePath = activeFile()) {
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

export async function refreshSyncStatus(showMessage: boolean) {
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

export function shortName(modelId: string): string {
  return modelId.split("/").pop()?.replace(/-4bit$/, "").slice(0, 28) ?? modelId;
}
