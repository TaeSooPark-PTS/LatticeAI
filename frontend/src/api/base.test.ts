/**
 * Every API call in the product goes through this module, and its whole job is
 * to never throw: a request that fails must come back as a typed `ApiResult`
 * with `ok: false`, an empty-but-correctly-shaped `data`, and a message a
 * non-developer can read.
 *
 * Only `emptyFor` was covered. The error-translation half — the part that
 * decides whether a user sees "the local service is unreachable" or a raw
 * `TypeError: Failed to fetch` — had no test, and a regression there is silent:
 * the page still renders, the list is still empty, and nobody learns that the
 * sidecar was down rather than the Brain being empty.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { emptyFor, friendlyCaughtError, friendlyError, workspaceHeaders } from "./base";
import { useAppStore } from "@/store/appStore";

beforeEach(() => {
  useAppStore.setState({ workspaceId: null, language: "en", apiBase: null });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("emptyFor", () => {
  it("returns an empty array for list response shapes", () => {
    expect(emptyFor([{ id: "not-a-fallback" }])).toEqual([]);
  });

  it("preserves the declared object shape without returning the same object", () => {
    const shape = { items: [], total: 0, available: false };
    const fallback = emptyFor(shape);

    expect(fallback).toEqual(shape);
    expect(fallback).not.toBe(shape);
  });

  it("does not let a caller mutate the shared shape through the copy", () => {
    const shape = { items: [] as string[], total: 0 };
    const fallback = emptyFor(shape);
    fallback.total = 9;
    expect(shape.total).toBe(0);
  });

  it("preserves primitive empty shapes", () => {
    expect(emptyFor(0)).toBe(0);
    expect(emptyFor("")).toBe("");
    expect(emptyFor(null)).toBeNull();
    expect(emptyFor(false)).toBe(false);
  });
});

describe("workspaceHeaders", () => {
  it("sends no scope header when there is no active workspace", () => {
    expect(workspaceHeaders()["X-Workspace-Id"]).toBeUndefined();
  });

  it("sends the active workspace as X-Workspace-Id", () => {
    useAppStore.setState({ workspaceId: "ws-42" });
    expect(workspaceHeaders()["X-Workspace-Id"]).toBe("ws-42");
  });

  // The server writes its own user-facing errors and cannot guess which
  // language to write them in. Every request carries the choice the person
  // made in the product, workspace or no workspace.
  it("always tells the server which language to answer in", () => {
    useAppStore.setState({ language: "en" });
    expect(workspaceHeaders()["X-Lattice-Language"]).toBe("en");

    useAppStore.setState({ language: "ko" });
    expect(workspaceHeaders()["X-Lattice-Language"]).toBe("ko");
  });
});

describe("friendlyError", () => {
  it("falls back when there is no error at all", () => {
    expect(friendlyError(null, "fallback")).toBe("fallback");
    expect(friendlyError(undefined, "fallback")).toBe("fallback");
  });

  it("prefers a plain string detail (FastAPI's default shape)", () => {
    expect(friendlyError({ detail: "Not permitted" }, "fallback")).toBe("Not permitted");
  });

  it.each([
    ["user_message", { user_message: "Approve it in the web app" }],
    ["reason", { reason: "file changed since staging" }],
    ["action", { action: "reindex" }],
    ["status", { status: "approval_expired" }],
  ])("reads %s out of a structured detail", (_label, detail) => {
    const message = friendlyError({ detail }, "fallback");
    expect(message).toBe(String(Object.values(detail)[0]));
  });

  it("prefers user_message over the other structured keys", () => {
    const message = friendlyError(
      {
        detail: {
          status: "conflict",
          reason: "stale",
          user_message: "Nothing was written",
        },
      },
      "fallback",
    );
    expect(message).toBe("Nothing was written");
  });

  it("falls back to a top-level message or error field", () => {
    expect(friendlyError({ message: "boom" }, "fallback")).toBe("boom");
    expect(friendlyError({ error: "nope" }, "fallback")).toBe("nope");
  });

  it("keeps falling when a structured detail has none of the known keys", () => {
    expect(friendlyError({ detail: { code: 42 } }, "fallback")).toBe("fallback");
  });

  it("uses the fallback for a shape it does not recognise", () => {
    expect(friendlyError({ unexpected: 1 }, "fallback")).toBe("fallback");
    expect(friendlyError("a bare string", "fallback")).toBe("fallback");
  });
});

describe("friendlyCaughtError", () => {
  it.each([
    "The operation was aborted",
    "signal is aborted without reason",
    "Request timed out",
    "request timeout",
  ])("reports %s as a timeout, not a raw exception", (raw) => {
    const message = friendlyCaughtError(new Error(raw), "fallback");
    expect(message).not.toBe(raw);
    expect(message).not.toBe("fallback");
    expect(message.length).toBeGreaterThan(0);
  });

  it.each([
    "Failed to fetch",
    "Load failed",
    "NetworkError when attempting to fetch resource",
    "Network request failed",
  ])("reports %s as 'the local service is unreachable'", (raw) => {
    const message = friendlyCaughtError(new Error(raw), "fallback");
    expect(message).not.toBe(raw);
    expect(message).not.toBe("fallback");
    expect(message.length).toBeGreaterThan(0);
  });

  it("hides JSON parse noise behind the caller's fallback", () => {
    expect(friendlyCaughtError(new Error("Unexpected token < in JSON"), "fallback")).toBe(
      "fallback",
    );
    expect(friendlyCaughtError(new Error("is not valid JSON"), "fallback")).toBe(
      "fallback",
    );
  });

  it("passes an unrecognised message through rather than inventing one", () => {
    expect(friendlyCaughtError(new Error("disk on fire"), "fallback")).toBe("disk on fire");
  });

  it("stringifies a non-Error throw", () => {
    expect(friendlyCaughtError("plain string throw", "fallback")).toBe(
      "plain string throw",
    );
  });

  it("uses the fallback for an empty message", () => {
    expect(friendlyCaughtError(new Error(""), "fallback")).toBe("fallback");
  });

  it("localises the timeout message with the stored UI language", () => {
    useAppStore.setState({ language: "en" });
    const english = friendlyCaughtError(new Error("aborted"), "fallback");
    useAppStore.setState({ language: "ko" });
    const korean = friendlyCaughtError(new Error("aborted"), "fallback");
    expect(korean).not.toBe(english);
  });
});

/**
 * The desktop shells. `tauriInvoke`, `selectFolder` and the Tauri half of
 * `apiBase` decide whether the app talks to a sidecar it discovered or to the
 * page's own origin. Wrong answers here don't throw — they quietly point every
 * request at the wrong place.
 */

import { apiBase, selectFolder, tauriInvoke } from "./base";

const tauriCore = vi.hoisted(() => ({ invoke: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: tauriCore.invoke }));

type ShellWindow = Record<string, unknown>;
const shell = () => window as unknown as ShellWindow;

function clearShellBridges() {
  delete shell().__TAURI__;
  delete shell().__TAURI_INTERNALS__;
  delete shell().latticeDesktop;
}

afterEach(() => {
  clearShellBridges();
  tauriCore.invoke.mockReset();
});

describe("tauriInvoke", () => {
  it("answers null in a plain browser with no bridge at all", async () => {
    expect(await tauriInvoke("backend_status")).toBeNull();
  });

  it("uses the injected global bridge when the shell provides one", async () => {
    const invoke = vi.fn().mockResolvedValue({ running: true });
    shell().__TAURI__ = { core: { invoke } };

    expect(await tauriInvoke("backend_status", { probe: 1 })).toEqual({ running: true });
    expect(invoke).toHaveBeenCalledWith("backend_status", { probe: 1 });
  });

  it("turns a global-bridge failure into null instead of throwing", async () => {
    shell().__TAURI__ = { core: { invoke: vi.fn().mockRejectedValue(new Error("ipc down")) } };

    expect(await tauriInvoke("backend_status")).toBeNull();
  });

  it("falls back to the imported API when only the internals marker exists", async () => {
    shell().__TAURI_INTERNALS__ = {};
    tauriCore.invoke.mockResolvedValue("pong");

    expect(await tauriInvoke("ping")).toBe("pong");
    expect(tauriCore.invoke).toHaveBeenCalledWith("ping", undefined);
  });

  it("turns an imported-API failure into null instead of throwing", async () => {
    shell().__TAURI_INTERNALS__ = {};
    tauriCore.invoke.mockRejectedValue(new Error("no such command"));

    expect(await tauriInvoke("ping")).toBeNull();
  });
});

describe("selectFolder", () => {
  it("prefers the Tauri dialog when it returns a path", async () => {
    shell().__TAURI__ = { core: { invoke: vi.fn().mockResolvedValue("/Users/me/Notes") } };

    expect(await selectFolder()).toBe("/Users/me/Notes");
  });

  it("falls through to the Electron preload bridge", async () => {
    shell().latticeDesktop = { selectFolder: vi.fn().mockResolvedValue("/Users/me/Docs") };

    expect(await selectFolder()).toBe("/Users/me/Docs");
  });

  it("reports a dismissed Electron dialog as null, not as an empty string", async () => {
    shell().latticeDesktop = { selectFolder: vi.fn().mockResolvedValue("") };

    expect(await selectFolder()).toBeNull();
  });

  it("reports null when no shell offers a picker", async () => {
    expect(await selectFolder()).toBeNull();
  });

  it("turns an Electron bridge failure into null instead of throwing", async () => {
    shell().latticeDesktop = { selectFolder: vi.fn().mockRejectedValue(new Error("dialog crashed")) };

    expect(await selectFolder()).toBeNull();
  });
});

describe("apiBase inside the desktop shell", () => {
  // Each scenario needs a fresh module: `base.ts` caches the discovered origin
  // for the lifetime of the module, which is exactly the behavior under test.
  async function freshBase() {
    vi.resetModules();
    const base = await import("./base");
    const store = (await import("@/store/appStore")).useAppStore;
    store.setState({ apiBase: null });
    return { base, store };
  }

  afterEach(() => {
    vi.resetModules();
  });

  it("adopts the sidecar origin the Tauri backend reports, then caches it", async () => {
    shell().__TAURI_INTERNALS__ = {};
    tauriCore.invoke.mockResolvedValue("http://127.0.0.1:8765");
    const { base, store } = await freshBase();

    expect(await base.apiBase()).toBe("http://127.0.0.1:8765");
    expect(store.getState().apiBase).toBe("http://127.0.0.1:8765");
    expect(tauriCore.invoke).toHaveBeenCalledWith("backend_origin");

    // Second read: the stored base answers without a second invoke.
    expect(await base.apiBase()).toBe("http://127.0.0.1:8765");
    // And even with the store cleared, the module-level promise is reused.
    store.setState({ apiBase: null });
    expect(await base.apiBase()).toBe("http://127.0.0.1:8765");
    expect(tauriCore.invoke).toHaveBeenCalledTimes(1);
  });

  it("treats an empty origin report as 'no sidecar' and stays on the page origin", async () => {
    shell().__TAURI_INTERNALS__ = {};
    tauriCore.invoke.mockResolvedValue("");
    const { base, store } = await freshBase();

    expect(await base.apiBase()).toBe("");
    expect(store.getState().apiBase).toBeNull();
  });

  it("treats a failed origin probe as 'no sidecar' instead of throwing", async () => {
    shell().__TAURI_INTERNALS__ = {};
    tauriCore.invoke.mockRejectedValue(new Error("not ready"));
    const { base } = await freshBase();

    expect(await base.apiBase()).toBe("");
  });
});

describe("uiLanguage resilience", () => {
  it("falls back to Korean copy when the store itself is unreadable", () => {
    const spy = vi.spyOn(useAppStore, "getState").mockImplementation(() => {
      throw new Error("store torn down");
    });
    try {
      const message = friendlyCaughtError(new Error("The operation was aborted"), "fallback");
      expect(message).not.toBe("fallback");
      expect(message.length).toBeGreaterThan(0);
    } finally {
      spy.mockRestore();
    }
  });
});
