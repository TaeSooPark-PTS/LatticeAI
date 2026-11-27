import { del, get, post } from "./base";

export const MEMORY_KINDS = [
  "long_term",
  "preferences",
  "decisions",
  "working_style",
  "frequently_used_tools",
  "workspace",
  "short_term",
] as const;

export type MemoryKind = (typeof MEMORY_KINDS)[number];

export type WorkspaceMemory = {
  id: string;
  kind: string;
  content: string;
  tags?: string[];
  created_at?: string;
  updated_at?: string;
  workspace_id?: string;
  graph_node_id?: string | null;
};

type MemoryList = { memories: WorkspaceMemory[] };

export const memoryApi = {
  workspaceMemories: () => get<MemoryList>("/workspace/memories", { memories: [] }),
  searchWorkspaceMemories: (q: string) =>
    get<MemoryList>("/workspace/memories/search", { memories: [] }, { q, limit: 100 }),
  addWorkspaceMemory: (kind: MemoryKind, content: string) =>
    post<{ memory?: WorkspaceMemory }>("/workspace/memories", { kind, content }, {}),
  deleteWorkspaceMemory: (id: string) =>
    del<{ status?: string; memory_id?: string }>(`/workspace/memories/${encodeURIComponent(id)}`, {}),
};
