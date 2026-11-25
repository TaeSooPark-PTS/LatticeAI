
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
  | { kind: "path"; path: string }
  | { kind: "files"; files: File[]; name: string }
  | { kind: "cancelled" }
  | { kind: "unavailable" };

export async function pickFolder(): Promise<FolderSelection> {
  try {
    if (hasDesktopFolderPicker()) {
      const selectedPath = await latticeApi.selectFolder();
      if (selectedPath) return { kind: "path", path: selectedPath };
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
