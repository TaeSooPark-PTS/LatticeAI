import * as vscode from "vscode";
import type { LatticeAIClient } from "./client";
import type { ArtifactCard } from "./surface";

export let client: LatticeAIClient;
export let statusBar: vscode.StatusBarItem;
export let syncStatusBar: vscode.StatusBarItem;
export let extensionVersion = "";
export let syncTimer: NodeJS.Timeout | undefined;
let agentOutput: vscode.OutputChannel | undefined;

export let lastRecall: { question: string; sourceIds: string[] } | null = null;

export let lastArtifacts: { goal: string; cards: ArtifactCard[] } | null = null;

export function setClient(next: LatticeAIClient): void {
  client = next;
}

export function setStatusBar(next: vscode.StatusBarItem): void {
  statusBar = next;
}

export function setSyncStatusBar(next: vscode.StatusBarItem): void {
  syncStatusBar = next;
}

export function setExtensionVersion(next: string): void {
  extensionVersion = next;
}

export function setSyncTimer(next: NodeJS.Timeout | undefined): void {
  syncTimer = next;
}

export function setLastRecall(next: { question: string; sourceIds: string[] } | null): void {
  lastRecall = next;
}

export function setLastArtifacts(next: { goal: string; cards: ArtifactCard[] } | null): void {
  lastArtifacts = next;
}

export function outputChannel(): vscode.OutputChannel {
  if (!agentOutput) agentOutput = vscode.window.createOutputChannel("Lattice AI");
  return agentOutput;
}
