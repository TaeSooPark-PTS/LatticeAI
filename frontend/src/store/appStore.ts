import { create } from "zustand";
import type { Language } from "@/i18n";
import type { FiringTrace } from "@/features/brain3d/brainGrowth";

export type Theme = "dark" | "light";
export type WorkspaceMode = "basic" | "advanced" | "admin";
export type GraphViewMode = "3d" | "2d";

type AppState = {
  theme: Theme;
  mode: WorkspaceMode;
  workspaceId: string | null;
  apiBase: string | null;
  language: Language;
  focusedGraphNodeId: string | null;
  pendingChatPrompt: string | null;
  briefingRequested: boolean;
  graphViewMode: GraphViewMode;
  showSuperseded: boolean;
  activeFiringTrace: FiringTrace | null;
  setTheme: (theme: Theme) => void;
  setMode: (mode: WorkspaceMode) => void;
  setWorkspaceId: (workspaceId: string | null) => void;
  setApiBase: (apiBase: string | null) => void;
  setLanguage: (language: Language) => void;
  setFocusedGraphNodeId: (nodeId: string | null) => void;
  setPendingChatPrompt: (prompt: string | null) => void;
  setBriefingRequested: (requested: boolean) => void;
  setGraphViewMode: (mode: GraphViewMode) => void;
  setShowSuperseded: (show: boolean) => void;
  setActiveFiringTrace: (trace: FiringTrace | null) => void;
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

function readGraphViewMode(): GraphViewMode {
  try {
    const saved = localStorage.getItem("lattice.graphViewMode");
    if (saved === "2d" || saved === "3d") return saved;
  } catch (err) {
    warnStorage("getItem(lattice.graphViewMode)", err);
  }
  return "3d";
}

export const useAppStore = create<AppState>((set) => ({
  theme: readTheme(),
  mode: readMode(),
  workspaceId: readWorkspaceId(),
  apiBase: null,
  language: readLanguage(),
  focusedGraphNodeId: null,
  pendingChatPrompt: null,
  briefingRequested: false,
  graphViewMode: readGraphViewMode(),
  showSuperseded: true,
  activeFiringTrace: null,
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
    set((state) => (state.workspaceId === workspaceId ? { workspaceId } : { workspaceId, activeFiringTrace: null }));
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
  setBriefingRequested: (briefingRequested) => set({ briefingRequested }),
  setGraphViewMode: (graphViewMode) => {
    try {
      localStorage.setItem("lattice.graphViewMode", graphViewMode);
    } catch (err) {
      warnStorage("setItem(lattice.graphViewMode)", err);
    }
    set({ graphViewMode });
  },
  setShowSuperseded: (showSuperseded) => set({ showSuperseded }),
  setActiveFiringTrace: (activeFiringTrace) => set({ activeFiringTrace }),
}));
