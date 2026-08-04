import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import {
  browserDirectoryInputProps,
  browserFolderNameFromFiles,
  canPickFolder,
  filesFromBrowserDirectory,
  hasBrowserFolderPicker,
  hasDesktopFolderPicker,
  isAbortError,
  pickFolder,
  type BrowserDirectoryHandle,
  type BrowserFileHandle,
} from "./folderPicker";

/**
 * "Pick a folder" means three different things depending on where Lattice is
 * running, and the whole point of this module is that the Brain home and the
 * Capture page never find out which. The failure mode it exists to prevent is
 * a 폴더 button that silently does nothing, so these tests are written from
 * that angle: for every shell, does the caller get something it can act on?
 */

type MutableWindow = Record<string, unknown>;

const shell = () => window as unknown as MutableWindow;

function clearShell() {
  delete shell().__TAURI__;
  delete shell().__TAURI_INTERNALS__;
  delete shell().latticeDesktop;
  delete shell().showDirectoryPicker;
}

function fileHandle(name: string, body = "x"): BrowserFileHandle {
  return {
    kind: "file",
    name,
    getFile: async () => new File([body], name),
  };
}

function directoryHandle(
  name: string,
  children: Array<BrowserFileHandle | BrowserDirectoryHandle>,
  style: "values" | "entries" = "values",
): BrowserDirectoryHandle {
  const handle: BrowserDirectoryHandle = { kind: "directory", name };
  if (style === "values") {
    handle.values = async function* () {
      for (const child of children) yield child;
    };
  } else {
    handle.entries = async function* () {
      for (const child of children) yield [child.name, child] as [string, typeof child];
    };
  }
  return handle;
}

beforeEach(clearShell);
afterEach(() => {
  clearShell();
  vi.restoreAllMocks();
});

describe("shell detection", () => {
  it("finds no picker in a plain browser", () => {
    expect(hasDesktopFolderPicker()).toBe(false);
    expect(hasBrowserFolderPicker()).toBe(false);
    expect(canPickFolder()).toBe(false);
  });

  it("recognises the Tauri core bridge", () => {
    shell().__TAURI__ = { core: { invoke: vi.fn() } };
    expect(hasDesktopFolderPicker()).toBe(true);
    expect(canPickFolder()).toBe(true);
  });

  it("recognises the Electron preload bridge", () => {
    shell().latticeDesktop = { selectFolder: vi.fn() };
    expect(hasDesktopFolderPicker()).toBe(true);
  });

  it("does not mistake bare Tauri internals on an http page for a desktop shell", () => {
    // `__TAURI_INTERNALS__` only counts under the `tauri:` protocol; jsdom
    // serves http, so this is the negative half of that condition.
    shell().__TAURI_INTERNALS__ = {};
    expect(hasDesktopFolderPicker()).toBe(false);
  });

  it("recognises the File System Access API", () => {
    shell().showDirectoryPicker = vi.fn();
    expect(hasBrowserFolderPicker()).toBe(true);
    expect(canPickFolder()).toBe(true);
  });
});

describe("reading a directory the browser handed us", () => {
  it("walks nested directories into a flat file list", async () => {
    const tree = directoryHandle("project", [
      fileHandle("a.md"),
      directoryHandle("docs", [fileHandle("b.md"), fileHandle("c.md")]),
    ]);
    const files = await filesFromBrowserDirectory(tree);
    expect(files.map((file) => file.name)).toEqual(["a.md", "b.md", "c.md"]);
  });

  it("reads handles that expose entries() instead of values()", async () => {
    // Both iteration styles exist across browser versions; a handle that only
    // has `entries()` used to come back as an empty folder.
    const tree = directoryHandle("project", [fileHandle("only.md")], "entries");
    expect((await filesFromBrowserDirectory(tree)).map((f) => f.name)).toEqual(["only.md"]);
  });

  it("returns nothing for a handle that iterates neither way", async () => {
    expect(await filesFromBrowserDirectory({ kind: "directory", name: "opaque" })).toEqual([]);
  });
});

describe("pickFolder", () => {
  it("returns a real path from the desktop shell", async () => {
    shell().latticeDesktop = { selectFolder: vi.fn() };
    vi.spyOn(latticeApi, "selectFolder").mockResolvedValue("/Users/me/Notes");

    expect(await pickFolder()).toEqual({ kind: "path", path: "/Users/me/Notes" });
  });

  it("falls through to the browser picker when the desktop dialog is dismissed", async () => {
    // Dismissing the native dialog is not a failure the user caused, and the
    // browser route may still work — reporting "unavailable" here would make
    // the button look broken.
    shell().latticeDesktop = { selectFolder: vi.fn() };
    vi.spyOn(latticeApi, "selectFolder").mockResolvedValue("");
    shell().showDirectoryPicker = vi.fn(async () =>
      directoryHandle("Notes", [fileHandle("note.md")]),
    );

    const result = await pickFolder();
    expect(result).toMatchObject({ kind: "files", name: "Notes" });
    expect(result.kind === "files" && result.files).toHaveLength(1);
  });

  it("reports a user cancel as cancelled, not as a failure", async () => {
    shell().showDirectoryPicker = vi.fn(async () => {
      throw new DOMException("The user aborted a request.", "AbortError");
    });
    expect(await pickFolder()).toEqual({ kind: "cancelled" });
  });

  it("reports any other failure as unavailable so the caller can fall back", async () => {
    shell().showDirectoryPicker = vi.fn(async () => {
      throw new Error("permission denied");
    });
    expect(await pickFolder()).toEqual({ kind: "unavailable" });
  });

  it("reports unavailable when no route exists at all", async () => {
    expect(await pickFolder()).toEqual({ kind: "unavailable" });
  });
});

describe("the <input webkitdirectory> fallback", () => {
  it("carries both the standard and the legacy attribute", () => {
    // React drops unknown camelCase props, and Safari only honours the
    // lowercase one — a fallback missing either attribute picks a single file.
    expect(browserDirectoryInputProps.webkitdirectory).toBe("");
    expect(browserDirectoryInputProps.directory).toBe("");
  });

  it("names the folder from the first file's relative path", () => {
    const file = new File(["x"], "note.md");
    Object.defineProperty(file, "webkitRelativePath", { value: "Notes/sub/note.md" });
    expect(browserFolderNameFromFiles([file])).toBe("Notes");
  });

  it("returns an empty name rather than guessing when there is no path", () => {
    expect(browserFolderNameFromFiles([])).toBe("");
    expect(browserFolderNameFromFiles([new File(["x"], "loose.md")])).toBe("");
  });
});

describe("isAbortError", () => {
  it("is true only for a real AbortError DOMException", () => {
    expect(isAbortError(new DOMException("nope", "AbortError"))).toBe(true);
    expect(isAbortError(new DOMException("nope", "NotAllowedError"))).toBe(false);
    expect(isAbortError(new Error("AbortError"))).toBe(false);
    expect(isAbortError(null)).toBe(false);
  });
});
