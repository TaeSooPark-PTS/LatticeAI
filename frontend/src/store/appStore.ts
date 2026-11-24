import { create } from "zustand";
import type { Language } from "@/i18n";

export type Theme = "dark" | "light";
export type WorkspaceMode = "basic" | "advanced" | "admin";

type AppState = {
  theme: Theme;
  mode: WorkspaceMode;
  workspaceId: string | null;
  apiBase: string | null;
  language: Language;
  focusedGraphNodeId: string | null;
  pendingChatPrompt: string | null;
  setTheme: (theme: Theme) => void;
  setMode: (mode: WorkspaceMode) => void;
  setWorkspaceId: (workspaceId: string | null) => void;
  setApiBase: (apiBase: string | null) => void;
  setLanguage: (language: Language) => void;
  setFocusedGraphNodeId: (nodeId: string | null) => void;
  setPendingChatPrompt: (prompt: string | null) => void;
};

function warnStorage(action: string, err: unknown): void {
  console.warn(`appStore localStorage.${action} failed`, err);
}

function readTheme(): Theme {
  try {
    const saved = localStorage.getItem("lattice.theme");
    if (saved === "light" || saved === "dark") return saved;
  } catch (err) {
    warnStorage("getItem(lattice.theme)", err);
  }
  return "light";
}

function readMode(): WorkspaceMode {
  try {
    const saved = localStorage.getItem("lattice.mode");
    if (saved === "basic" || saved === "advanced" || saved === "admin") return saved;
  } catch (err) {
    warnStorage("getItem(lattice.mode)", err);
  }
  return "basic";
}

function readWorkspaceId(): string | null {
  try {
    return localStorage.getItem("lattice.workspace") || null;
  } catch (err) {
    warnStorage("getItem(lattice.workspace)", err);
  }
  return null;
}

function readLanguage(): Language {
  try {
    const saved = localStorage.getItem("lattice.language");
    if (saved === "ko" || saved === "en") return saved;
  } catch (err) {
    warnStorage("getItem(lattice.language)", err);
  }
  const browser = typeof navigator !== "undefined" ? navigator.language.toLowerCase() : "";
  return browser.startsWith("ko") ? "ko" : "en";
}

export const useAppStore = create<AppState>((set) => ({
  theme: readTheme(),
  mode: readMode(),
  workspaceId: readWorkspaceId(),
  apiBase: null,
  language: readLanguage(),
  focusedGraphNodeId: null,
  pendingChatPrompt: null,
  setTheme: (theme) => {
    if (typeof document !== "undefined") {
      document.documentElement.dataset.theme = theme;
    }
    try {
      localStorage.setItem("lattice.theme", theme);
    } catch (err) {
      warnStorage("setItem(lattice.theme)", err);
    }
    set({ theme });
  },
  setMode: (mode) => {
    try {
      localStorage.setItem("lattice.mode", mode);
    } catch (err) {
      warnStorage("setItem(lattice.mode)", err);
    }
    set({ mode });
  },
  setWorkspaceId: (workspaceId) => {
    if (workspaceId) {
      try {
        localStorage.setItem("lattice.workspace", workspaceId);
      } catch (err) {
        warnStorage("setItem(lattice.workspace)", err);
      }
    } else {
      try {
        localStorage.removeItem("lattice.workspace");
      } catch (err) {
        warnStorage("removeItem(lattice.workspace)", err);
      }
    }
    set({ workspaceId });
  },
  setApiBase: (apiBase) => set({ apiBase }),
  setLanguage: (language) => {
    if (typeof document !== "undefined") {
      document.documentElement.lang = language === "ko" ? "ko" : "en";
    }
    try {
      localStorage.setItem("lattice.language", language);
    } catch (err) {
      warnStorage("setItem(lattice.language)", err);
    }
    set({ language });
  },
  setFocusedGraphNodeId: (focusedGraphNodeId) => set({ focusedGraphNodeId }),
  setPendingChatPrompt: (pendingChatPrompt) => set({ pendingChatPrompt }),
}));
