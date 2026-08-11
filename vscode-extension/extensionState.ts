/**
 * Extension-wide mutable state.
 *
 * `extension.ts` used to hold these as module-level `let`s next to every
 * command handler. Splitting the handlers into their own modules leaves the
 * state itself needing exactly one home: a second copy of `client` or
 * `lastRecall` would silently answer with a stale value. Live bindings
 * (`export let` + a setter) keep the single-owner property the original file
 * had for free — a reader always sees the current value, never a snapshot.
 */
import * as vscode from "vscode";
import type { LatticeAIClient } from "./client";
import type { ArtifactCard } from "./surface";

export let client: LatticeAIClient;
export let statusBar: vscode.StatusBarItem;
export let syncStatusBar: vscode.StatusBarItem;
export let extensionVersion = "";
export let syncTimer: NodeJS.Timeout | undefined;
let agentOutput: vscode.OutputChannel | undefined;

// Last recall's question + cited source ids, so "이 근거로 만들기" has real
// evidence to act on. Cleared implicitly by the next recall; never persisted.
export let lastRecall: { question: string; sourceIds: string[] } | null = null;

// Artifacts from the most recent agent run, so "Show Artifacts" can open them
// as cards after the run notification is gone. In-memory only.
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
