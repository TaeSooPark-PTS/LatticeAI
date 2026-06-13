import { create } from "zustand";

export type Theme = "dark" | "light";
export type WorkspaceMode = "basic" | "advanced" | "admin";

type AppState = {
  theme: Theme;
  mode: WorkspaceMode;
  workspaceId: string | null;
  apiBase: string | null;
  setTheme: (theme: Theme) => void;
  setMode: (mode: WorkspaceMode) => void;
  setWorkspaceId: (workspaceId: string | null) => void;
  setApiBase: (apiBase: string | null) => void;
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

export const useAppStore = create<AppState>((set) => ({
  theme: readTheme(),
  mode: readMode(),
  workspaceId: readWorkspaceId(),
  apiBase: null,
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
}));
