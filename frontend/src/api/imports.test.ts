import { afterEach, describe, expect, it, vi } from "vitest";

import * as base from "./base";
import { IMPORT_TIMEOUT_MS, importsApi } from "./imports";
import { latticeApi } from "./client";
import { jsonResponse, recordFetch, resetDispatcher } from "@/test/apiClientHarness";

const reply = { ok: true, status: 200, source: "live" as const, data: {} };

describe("importsApi", () => {
  afterEach(() => vi.restoreAllMocks());

  it("reads the interop catalog", async () => {
    const get = vi.spyOn(base, "get").mockResolvedValue(reply);
    await importsApi.interopStatus();
    expect(get).toHaveBeenCalledWith("/api/ingestion/interop", { sources: {} });
  });

  it("routes obsidian to its own endpoint without a source field", async () => {
    const post = vi.spyOn(base, "post").mockResolvedValue(reply);
    await importsApi.importFromTool({ source: "obsidian", path: "/v", approved: false });
    expect(post).toHaveBeenCalledWith("/api/ingestion/obsidian", { path: "/v", approved: false }, {}, IMPORT_TIMEOUT_MS);
  });

  it("routes the other tools through interop with the source field", async () => {
    const post = vi.spyOn(base, "post").mockResolvedValue(reply);
    await importsApi.importFromTool({ source: "git", path: "/r", approved: true, approval_token: "t", dry_run: true });
    expect(post).toHaveBeenCalledWith(
      "/api/ingestion/interop",
      { source: "git", path: "/r", approved: true, approval_token: "t", dry_run: true },
      {},
      IMPORT_TIMEOUT_MS,
    );
  });

  it("encodes the history search query", async () => {
    const calls = recordFetch(() => jsonResponse({ results: [], query: "" }));
    await latticeApi.searchHistory("검색 어");
    expect(calls[0]!.url.pathname).toBe("/history/search");
    expect(calls[0]!.url.searchParams.get("q")).toBe("검색 어");
    resetDispatcher();
  });
});
