import * as React from "react";
import { FolderPlus, Globe2, Upload } from "lucide-react";
import { latticeApi } from "@/api/client";
import { t, type Language } from "@/i18n";
import { isRecord } from "@/lib/utils";

export type CaptureMethod = "files" | "local" | "browser";

export const captureMethods: Array<{ id: CaptureMethod; labelKey: string; icon: typeof Upload; path: string }> = [
  { id: "files", labelKey: "capture.method.files", icon: Upload, path: "capture" },
  { id: "local", labelKey: "capture.method.local", icon: FolderPlus, path: "my-computer" },
  { id: "browser", labelKey: "capture.method.browser", icon: Globe2, path: "capture-browser" },
];

export function normalizeCaptureMethod(tab?: string): CaptureMethod {
  return captureMethods.some((item) => item.id === tab) ? (tab as CaptureMethod) : "files";
}

export type UploadQueueItem = {
  id: string;
  file: File;
  name: string;
  size: number;
  status: "queued" | "uploading" | "done" | "failed";
  result?: Awaited<ReturnType<typeof latticeApi.uploadDocument>>;
};

export function uploadResultDetail(item: UploadQueueItem, language: Language) {
  if (item.status === "queued") return t(language, "capture.files.queued");
  if (item.status === "uploading") return t(language, "capture.files.uploading");
  if (!item.result?.ok) return item.result?.error || t(language, "capture.files.failed");
  const data = item.result.data || {};
  const node = String(data.node_id || data.graph_node || data.provenance_id || "");
  return node ? t(language, "capture.files.capturedWithNode", { node }) : t(language, "capture.files.captured");
}

type BrowserFileHandle = {
  kind: "file";
  name: string;
  getFile: () => Promise<File>;
};

export type BrowserDirectoryHandle = {
  kind: "directory";
  name: string;
  values?: () => AsyncIterable<BrowserFileHandle | BrowserDirectoryHandle>;
  entries?: () => AsyncIterable<[string, BrowserFileHandle | BrowserDirectoryHandle]>;
};

export type BrowserDirectoryPickerWindow = Window & {
  showDirectoryPicker?: () => Promise<BrowserDirectoryHandle>;
};

type DesktopFolderPickerWindow = Window & {
  __TAURI_INTERNALS__?: unknown;
  __TAURI__?: {
    core?: {
      invoke?: <T>(command: string, args?: Record<string, unknown>) => Promise<T>;
    };
  };
  latticeDesktop?: {
    selectFolder?: () => Promise<string | null>;
  };
};

export const browserDirectoryInputProps: React.InputHTMLAttributes<HTMLInputElement> & {
  webkitdirectory: string;
  directory: string;
} = {
  webkitdirectory: "",
  directory: "",
};

export function displayFileName(file: File) {
  return (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
}

export function browserFolderNameFromFiles(files: File[]) {
  const firstPath = (files[0] as (File & { webkitRelativePath?: string }) | undefined)?.webkitRelativePath || "";
  return firstPath.split("/").filter(Boolean)[0] || "";
}

export function hasDesktopFolderPicker() {
  const shell = window as DesktopFolderPickerWindow;
  return Boolean(shell.__TAURI__?.core?.invoke || shell.latticeDesktop?.selectFolder || (shell.__TAURI_INTERNALS__ && window.location.protocol === "tauri:"));
}

export function isAbortError(error: unknown) {
  return error instanceof DOMException && error.name === "AbortError";
}

async function browserDirectoryEntries(handle: BrowserDirectoryHandle) {
  const entries: Array<BrowserFileHandle | BrowserDirectoryHandle> = [];
  if (typeof handle.values === "function") {
    for await (const entry of handle.values()) entries.push(entry);
    return entries;
  }
  if (typeof handle.entries === "function") {
    for await (const [, entry] of handle.entries()) entries.push(entry);
  }
  return entries;
}

export async function filesFromBrowserDirectory(handle: BrowserDirectoryHandle): Promise<File[]> {
  const files: File[] = [];
  for (const entry of await browserDirectoryEntries(handle)) {
    if (entry.kind === "file") {
      files.push(await entry.getFile());
      continue;
    }
    files.push(...await filesFromBrowserDirectory(entry));
  }
  return files;
}

export function normalizeWebUrl(value: string) {
  const raw = value.trim();
  if (!raw) return "";
  if (/^https?:\/\//i.test(raw)) return raw;
  if (/^[\w.-]+\.[a-z]{2,}([/:?#].*)?$/i.test(raw)) return `https://${raw}`;
  return raw;
}

export type JourneyState = "done" | "working" | "waiting";

/**
 * What a person can actually see of their own material.
 *
 * The two counts and the three states below are read tolerantly from whichever
 * shape the server sends: `/api/index/status` reports per-stage pipelines, and
 * older/simpler builds report a flat `{ pending, total }`. Neither is shown as
 * a number the reader cannot check — every count says what it counted.
 */
export function readJourney(pipelineStatus: unknown, index: unknown, stats: unknown) {
  const statusData = isRecord(pipelineStatus) ? pipelineStatus : {};
  const indexData = isRecord(index) ? index : {};
  const statsData = isRecord(stats) ? stats : {};

  const num = (...values: unknown[]) => {
    for (const value of values) {
      if (typeof value === "number" && Number.isFinite(value) && value >= 0) return Math.round(value);
      const parsed = Number(value);
      if (Number.isFinite(parsed) && parsed > 0) return Math.round(parsed);
    }
    return undefined;
  };

  const received = num(statusData.received);
  const extracted = num(statusData.extracted);
  const connected = num(statusData.connected);

  const remembered = num(extracted, indexData.total, indexData.total_items, statsData.nodes, statsData.total_nodes) || 0;
  const connections = num(connected, statsData.edges, statsData.total_edges) || 0;
  const waiting = num(indexData.pending, indexData.pending_items) || 0;

  const getStageState = (stageKeys: string[], fallbackState: JourneyState): JourneyState => {
    const stages = (statusData.stages || {}) as Record<string, any>;
    for (const k of stageKeys) {
      const raw = stages[k];
      const st = (typeof raw === "object" && raw !== null ? raw.status : raw);
      if (st === "done" || st === "working" || st === "waiting" || st === "error") {
        return st as JourneyState;
      }
    }
    return fallbackState;
  };

  const fallbackReadState: JourneyState = (received !== undefined ? (received > 0 ? "done" : "waiting") : (waiting ? "working" : (remembered ? "done" : "waiting")));
  const fallbackUnderstandState: JourneyState = (extracted !== undefined ? (extracted > 0 ? "done" : "waiting") : (remembered ? "done" : "waiting"));
  const fallbackConnectState: JourneyState = (connected !== undefined ? (connected > 0 ? "done" : "waiting") : (connections ? "done" : "waiting"));

  const readState: JourneyState = getStageState(["read", "received"], fallbackReadState);
  const understandState: JourneyState = getStageState(["understand", "extracted"], fallbackUnderstandState);
  const connectState: JourneyState = getStageState(["connect", "connected"], fallbackConnectState);

  return {
    remembered,
    connections,
    waiting,
    received: received ?? waiting,
    extracted: extracted ?? remembered,
    connected: connected ?? connections,
    steps: [
      { key: "read", state: readState, count: received ?? (remembered || waiting) },
      { key: "understand", state: understandState, count: extracted ?? remembered },
      { key: "connect", state: connectState, count: connected ?? connections },
    ] as Array<{ key: string; state: JourneyState; count: number }>,
  };
}
