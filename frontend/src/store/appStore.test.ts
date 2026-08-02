/**
 * The app store is the only thing that survives a reload.
 *
 * Theme, workspace mode, active workspace and language all round-trip through
 * localStorage, and every write is wrapped in a bare `try {} catch {}`. That
 * makes the failure mode invisible: in a browser with storage disabled (Safari
 * private mode, some corporate profiles) a throwing `setItem` must still leave
 * the in-memory state correct rather than aborting the setter.
 *
 * These tests cover both halves — what gets persisted, and what happens when
 * persistence is unavailable.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

async function freshStore() {
  vi.resetModules();
  return (await import("./appStore")).useAppStore;
}

const realNavigator = window.navigator;

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.removeAttribute("lang");
});

afterEach(() => {
  vi.restoreAllMocks();
  Object.defineProperty(window, "navigator", {
    value: realNavigator,
    configurable: true,
  });
});

describe("initial state", () => {
  it("defaults to a light, basic, English workspace with no scope", async () => {
    Object.defineProperty(window, "navigator", {
      value: { language: "en-US" },
      configurable: true,
    });
    const store = await freshStore();
    const state = store.getState();
    expect(state.theme).toBe("light");
    expect(state.mode).toBe("basic");
    expect(state.workspaceId).toBeNull();
    expect(state.apiBase).toBeNull();
    expect(state.language).toBe("en");
  });

  it.each([
    ["lattice.theme", "dark", "theme", "dark"],
    ["lattice.mode", "admin", "mode", "admin"],
    ["lattice.language", "ko", "language", "ko"],
    ["lattice.workspace", "ws-42", "workspaceId", "ws-42"],
  ])("restores %s from storage", async (key, saved, field, expected) => {
    localStorage.setItem(key, saved);
    const store = await freshStore();
    expect(store.getState()[field as "theme"]).toBe(expected);
  });

  it.each([
    ["lattice.theme", "chartreuse", "theme", "light"],
    ["lattice.mode", "superuser", "mode", "basic"],
    ["lattice.language", "fr", "language", "en"],
  ])("ignores an invalid stored %s", async (key, saved, field, fallback) => {
    localStorage.setItem(key, saved);
    const store = await freshStore();
    expect(store.getState()[field as "theme"]).toBe(fallback);
  });

  it("falls back to Korean when the browser asks for it", async () => {
    Object.defineProperty(window, "navigator", {
      value: { language: "ko-KR" },
      configurable: true,
    });
    const store = await freshStore();
    expect(store.getState().language).toBe("ko");
  });

  it("treats an empty stored workspace as no workspace", async () => {
    localStorage.setItem("lattice.workspace", "");
    const store = await freshStore();
    expect(store.getState().workspaceId).toBeNull();
  });
});

describe("setters", () => {
  it("setTheme persists and stamps the document element", async () => {
    const store = await freshStore();
    store.getState().setTheme("dark");
    expect(store.getState().theme).toBe("dark");
    expect(localStorage.getItem("lattice.theme")).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("setLanguage persists and sets the document language", async () => {
    const store = await freshStore();
    store.getState().setLanguage("ko");
    expect(document.documentElement.lang).toBe("ko");
    expect(localStorage.getItem("lattice.language")).toBe("ko");
    store.getState().setLanguage("en");
    expect(document.documentElement.lang).toBe("en");
  });

  it("setMode persists the workspace mode", async () => {
    const store = await freshStore();
    store.getState().setMode("advanced");
    expect(localStorage.getItem("lattice.mode")).toBe("advanced");
    expect(store.getState().mode).toBe("advanced");
  });

  it("setWorkspaceId(null) clears the stored scope rather than writing 'null'", async () => {
    const store = await freshStore();
    store.getState().setWorkspaceId("ws-7");
    expect(localStorage.getItem("lattice.workspace")).toBe("ws-7");
    store.getState().setWorkspaceId(null);
    expect(localStorage.getItem("lattice.workspace")).toBeNull();
    expect(store.getState().workspaceId).toBeNull();
  });

  it("setApiBase is in-memory only — the backend origin is never persisted", async () => {
    const store = await freshStore();
    store.getState().setApiBase("http://127.0.0.1:8765");
    expect(store.getState().apiBase).toBe("http://127.0.0.1:8765");
    expect(localStorage.length).toBe(0);
  });
});

describe("when localStorage is unavailable", () => {
  it("still updates in-memory state and the document", async () => {
    const store = await freshStore();
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("storage disabled");
    });

    expect(() => store.getState().setTheme("dark")).not.toThrow();
    expect(store.getState().theme).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");

    expect(() => store.getState().setMode("admin")).not.toThrow();
    expect(store.getState().mode).toBe("admin");

    expect(() => store.getState().setLanguage("ko")).not.toThrow();
    expect(store.getState().language).toBe("ko");
    expect(document.documentElement.lang).toBe("ko");
  });

  it("still clears the workspace when removeItem throws", async () => {
    const store = await freshStore();
    store.getState().setWorkspaceId("ws-1");
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new Error("storage disabled");
    });
    expect(() => store.getState().setWorkspaceId(null)).not.toThrow();
    expect(store.getState().workspaceId).toBeNull();
  });

  it("starts from defaults when reading throws", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("storage disabled");
    });
    Object.defineProperty(window, "navigator", {
      value: { language: "en-GB" },
      configurable: true,
    });
    const store = await freshStore();
    expect(store.getState()).toMatchObject({
      theme: "light",
      mode: "basic",
      workspaceId: null,
      language: "en",
    });
  });
});
