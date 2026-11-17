import { del, get, post, type ApiResult } from "./base";

export type SelfModelKind =
  | "trait"
  | "preference"
  | "habit"
  | "decision"
  | "relationship";

export type SelfModelFact = {
  id: string;
  kind: SelfModelKind | string;
  text: string;
  origin?: string;
  confidence?: number;
  updated_at?: string;
};

export type SelfModelSnapshot = {
  available: boolean;
  count: number;
  facts: SelfModelFact[];
  by_kind: Record<string, SelfModelFact[]>;
  summary: string;
  summary_tokens?: number;
  kinds: string[];
};

const EMPTY_SELF_MODEL: SelfModelSnapshot = {
  available: false,
  count: 0,
  facts: [],
  by_kind: {},
  summary: "",
  kinds: ["trait", "preference", "habit", "decision", "relationship"],
};

export const selfModelApi = {
  selfModel: () => get<SelfModelSnapshot>("/api/memory/self-model", EMPTY_SELF_MODEL),
  upsertSelfModelFact: (kind: string, text: string) =>
    post<{ ok?: boolean; fact?: SelfModelFact }>("/api/memory/self-model", { kind, text }, {}),
  deleteSelfModelFact: (nodeId: string) =>
    del<{ ok?: boolean; deleted?: boolean }>(
      `/api/memory/self-model/${encodeURIComponent(nodeId)}`,
      {},
    ),
  proposeSelfModel: (text: string, source = "ui") =>
    post<{ count?: number; proposals?: Array<Record<string, unknown>> }>(
      "/api/memory/self-model/propose",
      { text, source },
      {},
    ),
  applySelfModelProposal: (itemId: string, text?: string) =>
    post<{ ok?: boolean; fact?: SelfModelFact }>(
      "/api/memory/self-model/apply",
      text !== undefined ? { item_id: itemId, text } : { item_id: itemId },
      {},
    ),
  resolveApprovedLocalServeUrl: async (path: string): Promise<ApiResult<{ url: string }>> => {
    const probe = await post<Record<string, unknown>>(
      "/local/read",
      { path, approved: false },
      {},
    );
    const token =
      typeof probe.data?.approval_token === "string" ? probe.data.approval_token : "";
    if (!token) {
      return {
        ok: false,
        status: probe.status,
        data: { url: "" },
        source: "unavailable",
        error: probe.error || "approval_required",
      };
    }
    const approved = await post<Record<string, unknown>>(
      `/permissions/approve/${encodeURIComponent(token)}`,
      {},
      {},
    );
    if (!approved.ok) {
      return {
        ok: false,
        status: approved.status,
        data: { url: "" },
        source: "unavailable",
        error: approved.error || "approval_failed",
      };
    }
    const qs = new URLSearchParams({ path, approval_token: token }).toString();
    return {
      ok: true,
      status: 200,
      data: { url: `/local/serve?${qs}` },
      source: "live",
    };
  },
};
