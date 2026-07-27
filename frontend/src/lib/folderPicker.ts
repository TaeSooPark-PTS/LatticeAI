// Picking a folder means three different things depending on where Lattice is
// running, and every surface that offers a "폴더" button has to handle all of
// them or the button silently does nothing:
//
//   desktop shell (Tauri/Electron) → a real path the server can watch
//   browser with File System Access → a directory handle we read files from
//   browser without it             → a <input webkitdirectory> fallback
//
// This module owns that decision so the Brain home and the Capture page behave
// identically. Callers handle the returned outcome; they never probe the shell.

import { latticeApi } from "@/api/client";

export type BrowserFileHandle = {
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

type BrowserDirectoryPickerWindow = Window & {
  showDirectoryPicker?: () => Promise<BrowserDirectoryHandle>;
};

type DesktopFolderPickerWindow = Window & {
  __TAURI_INTERNALS__?: unknown;
  __TAURI__?: { core?: { invoke?: <T>(command: string, args?: Record<string, unknown>) => Promise<T> } };
  latticeDesktop?: { selectFolder?: () => Promise<string | null> };
};

/** Props for the last-resort `<input type="file">` folder fallback. */
export const browserDirectoryInputProps: React.InputHTMLAttributes<HTMLInputElement> & {
  webkitdirectory: string;
  directory: string;
} = {
  webkitdirectory: "",
  directory: "",
};

export function hasDesktopFolderPicker() {
  const shell = window as DesktopFolderPickerWindow;
  return Boolean(
    shell.__TAURI__?.core?.invoke
      || shell.latticeDesktop?.selectFolder
      || (shell.__TAURI_INTERNALS__ && window.location.protocol === "tauri:"),
  );
}

export function hasBrowserFolderPicker() {
  return typeof (window as BrowserDirectoryPickerWindow).showDirectoryPicker === "function";
}

/** True when some folder route exists, given a `<input webkitdirectory>` fallback. */
export function canPickFolder() {
  return hasDesktopFolderPicker() || hasBrowserFolderPicker();
}

export function isAbortError(error: unknown) {
  return error instanceof DOMException && error.name === "AbortError";
}

export function browserFolderNameFromFiles(files: File[]) {
  const firstPath = (files[0] as (File & { webkitRelativePath?: string }) | undefined)?.webkitRelativePath || "";
  return firstPath.split("/").filter(Boolean)[0] || "";
}

async function directoryEntries(handle: BrowserDirectoryHandle) {
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
  for (const entry of await directoryEntries(handle)) {
    if (entry.kind === "file") {
      files.push(await entry.getFile());
      continue;
    }
    files.push(...await filesFromBrowserDirectory(entry));
  }
  return files;
}

export type FolderSelection =
  /** A real filesystem path the server can connect to and keep watching. */
  | { kind: "path"; path: string }
  /** Browser-read files; the caller uploads them one by one. */
  | { kind: "files"; files: File[]; name: string }
  /** The user closed the picker — not an error, say nothing. */
  | { kind: "cancelled" }
  /** No picker route exists; the caller should offer its file-input fallback. */
  | { kind: "unavailable" };

/**
 * Ask the user for a folder using the best route this shell supports.
 * Never throws for a user cancel — that comes back as `cancelled`.
 */
export async function pickFolder(): Promise<FolderSelection> {
  try {
    if (hasDesktopFolderPicker()) {
      const selectedPath = await latticeApi.selectFolder();
      if (selectedPath) return { kind: "path", path: selectedPath };
      // The desktop dialog was dismissed; fall through to the browser route
      // rather than reporting a failure the user did not cause.
    }
    const browserPicker = (window as BrowserDirectoryPickerWindow).showDirectoryPicker;
    if (typeof browserPicker === "function") {
      const handle = await browserPicker.call(window);
      return { kind: "files", files: await filesFromBrowserDirectory(handle), name: handle.name };
    }
    return { kind: "unavailable" };
  } catch (error) {
    if (isAbortError(error)) return { kind: "cancelled" };
    return { kind: "unavailable" };
  }
}
