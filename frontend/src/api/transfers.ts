import {
  apiBase,
  type ApiResult,
  friendlyCaughtError,
  friendlyError,
  post,
  workspaceHeaders,
} from "./base";

export type DemoCorpusDocument = {
  demo_id?: string;
  title?: string;
  source_uri?: string;
  status?: string;
  node_id?: string | null;
  duplicate?: boolean;
  chunk_count?: number;
};

export type DemoCorpusQuestion = {
  question?: string;
  expected_source_uri?: string;
  expected_title?: string;
};

export type DemoCorpusStatus = {
  installed: boolean;
  documents: DemoCorpusDocument[];
  document_count: number;
  suggested_questions: DemoCorpusQuestion[];
};

export type DemoCorpusInstallResult = {
  status: string;
  ingested: number;
  duplicates: number;
  failed?: number;
  documents: DemoCorpusDocument[];
  suggested_questions: DemoCorpusQuestion[];
};

export type DocumentVisibility = "workspace" | "private";

export async function uploadDocument(
  file: File,
  visibility: DocumentVisibility = "workspace",
): Promise<ApiResult<Record<string, unknown> | null>> {
  const base = await apiBase();
  const form = new FormData();
  form.append("file", file);
  const headers: Record<string, string> = { Accept: "application/json", ...workspaceHeaders() };
  if (visibility === "private") {
    form.append("visibility", visibility);
    headers["x-lattice-visibility"] = visibility;
  }
  try {
    const res = await fetch(`${base}/upload/document`, {
      method: "POST",
      credentials: "include",
      headers,
      body: form,
    });
    const data = await res.json().catch(() => null);
    return {
      ok: res.ok,
      status: res.status,
      data,
      source: res.ok ? "live" : "unavailable",
      error: res.ok ? undefined : friendlyError(data, res.statusText || "Upload failed"),
    };
  } catch (err) {
    return { ok: false, status: 0, data: null, source: "unavailable", error: String(err) };
  }
}

export async function saveChatFile(path: string, content: string): Promise<ApiResult<{ path?: string; bytes?: number }>> {
  return post("/tools/write_file", { path, content }, {});
}

export async function installDemoCorpus(): Promise<ApiResult<DemoCorpusInstallResult>> {
  const base = await apiBase();
  const shape: DemoCorpusInstallResult = {
    status: "", ingested: 0, duplicates: 0, documents: [], suggested_questions: [],
  };
  try {
    const res = await fetch(`${base}/api/setup/demo-corpus`, {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        ...workspaceHeaders(),
      } satisfies HeadersInit,
      body: JSON.stringify({}),
    });
    const data = await res.json().catch(() => null);
    if (!res.ok) {
      return {
        ok: false,
        status: res.status,
        data: shape,
        source: "unavailable",
        error: friendlyError(data, res.statusText || `HTTP ${res.status}`),
      };
    }
    return {
      ok: true,
      status: res.status,
      data: { ...shape, ...(data && typeof data === "object" ? data : {}) },
      source: "live",
    };
  } catch (err) {
    return { ok: false, status: 0, data: shape, source: "unavailable", error: friendlyCaughtError(err, "unreachable") };
  }
}

export async function runAgent(goal: string, roles: string[]): Promise<ApiResult<Record<string, unknown>>> {
  const result = await post<Record<string, unknown>>("/agents/api/run", { goal, roles }, {});
  if (!result.ok) {
    throw new Error(result.error || `Agent run failed with HTTP ${result.status}`);
  }
  return result;
}

export async function readWorkspaceFile(path: string): Promise<ApiResult<{ content: string }>> {
  const base = await apiBase();
  try {
    const res = await fetch(`${base}/tools/download?path=${encodeURIComponent(path)}`, {
      credentials: "include",
      headers: workspaceHeaders(),
    });
    if (!res.ok) {
      const payload = await res.json().catch(() => null);
      return {
        ok: false,
        status: res.status,
        data: { content: "" },
        source: "unavailable",
        error: friendlyError(payload, res.statusText || `HTTP ${res.status}`),
      };
    }
    const content = await res.text();
    return { ok: true, status: res.status, data: { content }, source: "live" };
  } catch (err) {
    return {
      ok: false,
      status: 0,
      data: { content: "" },
      source: "unavailable",
      error: friendlyCaughtError(err, "unreachable"),
    };
  }
}

export async function downloadWorkspaceFile(path: string, filename: string): Promise<{ ok: boolean; error?: string }> {
  const base = await apiBase();
  try {
    const res = await fetch(`${base}/tools/download?path=${encodeURIComponent(path)}`, {
      credentials: "include",
      headers: workspaceHeaders(),
    });
    if (!res.ok) {
      const payload = await res.json().catch(() => null);
      return { ok: false, error: friendlyError(payload, res.statusText || "Download failed") };
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename || path.split("/").pop() || "download";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
    return { ok: true };
  } catch (err) {
    return { ok: false, error: friendlyCaughtError(err, "Download failed") };
  }
}

export type VaultExportResult = {
  ok: boolean;
  error?: string;
  notes: number;
  history: number;
  withheld: number;
  omitted: number;
  truncated: number;
};

export async function exportBrainVault(workspaceId: string): Promise<VaultExportResult> {
  const empty = { notes: 0, history: 0, withheld: 0, omitted: 0, truncated: 0 };
  const base = await apiBase();
  try {
    const res = await fetch(`${base}/api/knowledge-graph/export-vault`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json", ...workspaceHeaders() } satisfies HeadersInit,
      body: JSON.stringify({ workspace_id: workspaceId }),
    });
    if (!res.ok) {
      const payload = await res.json().catch(() => null);
      return { ...empty, ok: false, error: friendlyError(payload, res.statusText || `HTTP ${res.status}`) };
    }
    const count = (name: keyof typeof empty) => Number(res.headers.get(`x-lattice-vault-${name}`)) || 0;
    const filename = /filename="([^"]+)"/.exec(res.headers.get("content-disposition") ?? "")?.[1];
    const url = URL.createObjectURL(await res.blob());
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename ?? "lattice-brain-vault.zip";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
    return {
      ok: true,
      notes: count("notes"),
      history: count("history"),
      withheld: count("withheld"),
      omitted: count("omitted"),
      truncated: count("truncated"),
    };
  } catch (err) {
    return { ...empty, ok: false, error: friendlyCaughtError(err, "unreachable") };
  }
}
