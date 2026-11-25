import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

import "@/i18n/brain";
import "@/i18n/onboarding";
import "@/i18n/shell";
import "@/i18n/workspace";

if (!("ResizeObserver" in globalThis)) {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}

if (typeof globalThis.matchMedia !== "function") {
  globalThis.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
    dispatchEvent: () => false,
  })) as typeof globalThis.matchMedia;
}

if (!globalThis.localStorage) {
  const entries = new Map<string, string>();
  const memoryStorage: Storage = {
    get length() { return entries.size; },
    key: (index: number) => Array.from(entries.keys())[index] ?? null,
    getItem: (key: string) => entries.get(String(key)) ?? null,
    setItem: (key: string, value: string) => { entries.set(String(key), String(value)); },
    removeItem: (key: string) => { entries.delete(String(key)); },
    clear: () => entries.clear(),
  };
  Object.defineProperty(globalThis, "localStorage", { configurable: true, get: () => memoryStorage });
}

afterEach(() => {
  cleanup();
  localStorage.clear();
});
