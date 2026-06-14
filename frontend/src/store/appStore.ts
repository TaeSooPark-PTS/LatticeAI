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
  setTheme: (theme: Theme) => void;
  setMode: (mode: WorkspaceMode) => void;
  setWorkspaceId: (workspaceId: string | null) => void;
  setApiBase: (apiBase: string | null) => void;
  setLanguage: (language: Language) => void;
};

function readTheme(): Theme {
  try {
    const saved = localStorage.getItem("lattice.theme");
    if (saved === "light" || saved === "dark") return saved;
  } catch {}
  return "dark";
}

function readMode(): WorkspaceMode {
  try {
    const saved = localStorage.getItem("lattice.mode");
    if (saved === "basic" || saved === "advanced" || saved === "admin") return saved;
  } catch {}
  return "basic";
}

function readWorkspaceId(): string | null {
  try {
    return localStorage.getItem("lattice.workspace") || null;
  } catch {}
  return null;
}

function readLanguage(): Language {
  try {
    const saved = localStorage.getItem("lattice.language");
    if (saved === "ko" || saved === "en") return saved;
  } catch {}
  const browser = typeof navigator !== "undefined" ? navigator.language.toLowerCase() : "";
  return browser.startsWith("ko") ? "ko" : "en";
}

export const useAppStore = create<AppState>((set) => ({
  theme: readTheme(),
  mode: readMode(),
  workspaceId: readWorkspaceId(),
  apiBase: null,
  language: readLanguage(),
  setTheme: (theme) => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("lattice.theme", theme); } catch {}
    set({ theme });
  },
  setMode: (mode) => {
    try { localStorage.setItem("lattice.mode", mode); } catch {}
    set({ mode });
  },
  setWorkspaceId: (workspaceId) => {
    if (workspaceId) {
      try { localStorage.setItem("lattice.workspace", workspaceId); } catch {}
    }
    set({ workspaceId });
  },
  setApiBase: (apiBase) => set({ apiBase }),
  setLanguage: (language) => {
    document.documentElement.lang = language === "ko" ? "ko" : "en";
    try { localStorage.setItem("lattice.language", language); } catch {}
    set({ language });
  },
}));
