import { beforeEach, describe, expect, it, vi } from "vitest";

import * as base from "./base";
import { selfModelApi } from "./selfModel";

describe("selfModelApi", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("wires GET, POST, and DELETE endpoints for Self-Model operations", async () => {
    const getSpy = vi.spyOn(base, "get").mockResolvedValue({
      ok: true,
      status: 200,
      source: "live",
      data: { available: true, count: 0, facts: [], by_kind: {}, summary: "", kinds: [] },
    });
    const postSpy = vi.spyOn(base, "post").mockResolvedValue({
      ok: true,
      status: 200,
      source: "live",
      data: { ok: true },
    });
    const delSpy = vi.spyOn(base, "del").mockResolvedValue({
      ok: true,
      status: 200,
      source: "live",
      data: { ok: true, deleted: true },
    });

    await selfModelApi.selfModel();
    expect(getSpy).toHaveBeenCalledWith("/api/memory/self-model", expect.objectContaining({ available: false }));

    await selfModelApi.upsertSelfModelFact("preference", "한국어 요약 선호");
    expect(postSpy).toHaveBeenCalledWith("/api/memory/self-model", { kind: "preference", text: "한국어 요약 선호" }, {});

    await selfModelApi.deleteSelfModelFact("self:preference:abc");
    expect(delSpy).toHaveBeenCalledWith("/api/memory/self-model/self%3Apreference%3Aabc", {});

    await selfModelApi.proposeSelfModel("매일 아침 회고를 씁니다");
    expect(postSpy).toHaveBeenCalledWith(
      "/api/memory/self-model/propose",
      { text: "매일 아침 회고를 씁니다", source: "ui" },
      {},
    );

    await selfModelApi.applySelfModelProposal("rev-1");
    expect(postSpy).toHaveBeenCalledWith("/api/memory/self-model/apply", { item_id: "rev-1" }, {});

    await selfModelApi.applySelfModelProposal("rev-2", "수정된 문장");
    expect(postSpy).toHaveBeenCalledWith(
      "/api/memory/self-model/apply",
      { item_id: "rev-2", text: "수정된 문장" },
      {},
    );
  });

  it("mints an approval token, approves it, and builds /local/serve URL", async () => {
    const postSpy = vi
      .spyOn(base, "post")
      .mockResolvedValueOnce({
        ok: false,
        status: 403,
        source: "unavailable",
        data: { approval_token: "tok-123" },
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        source: "live",
        data: { status: "approved" },
      });

    const okRes = await selfModelApi.resolveApprovedLocalServeUrl("/tmp/diagram.png");
    expect(okRes.ok).toBe(true);
    expect(okRes.data.url).toBe("/local/serve?path=%2Ftmp%2Fdiagram.png&approval_token=tok-123");
    expect(postSpy).toHaveBeenNthCalledWith(1, "/local/read", { path: "/tmp/diagram.png", approved: false }, {});
    expect(postSpy).toHaveBeenNthCalledWith(2, "/permissions/approve/tok-123", {}, {});
  });

  it("surfaces errors when probe returns no token or approval fails", async () => {
    vi.spyOn(base, "post").mockResolvedValueOnce({
      ok: false,
      status: 404,
      source: "unavailable",
      data: {},
      error: "missing_file",
    });
    const noTok = await selfModelApi.resolveApprovedLocalServeUrl("/tmp/missing.png");
    expect(noTok.ok).toBe(false);
    expect(noTok.error).toBe("missing_file");

    vi.spyOn(base, "post").mockResolvedValueOnce({
      ok: false,
      status: 403,
      source: "unavailable",
      data: {},
    });
    const fallbackTokErr = await selfModelApi.resolveApprovedLocalServeUrl("/tmp/denied.png");
    expect(fallbackTokErr.error).toBe("approval_required");

    vi.spyOn(base, "post")
      .mockResolvedValueOnce({
        ok: false,
        status: 403,
        source: "unavailable",
        data: { approval_token: "tok-bad" },
      })
      .mockResolvedValueOnce({
        ok: false,
        status: 400,
        source: "unavailable",
        data: {},
        error: "expired_token",
      });
    const badApprove = await selfModelApi.resolveApprovedLocalServeUrl("/tmp/expired.png");
    expect(badApprove.ok).toBe(false);
    expect(badApprove.error).toBe("expired_token");

    vi.spyOn(base, "post")
      .mockResolvedValueOnce({
        ok: false,
        status: 403,
        source: "unavailable",
        data: { approval_token: "tok-bad2" },
      })
      .mockResolvedValueOnce({
        ok: false,
        status: 500,
        source: "unavailable",
        data: {},
      });
    const badApproveFallback = await selfModelApi.resolveApprovedLocalServeUrl("/tmp/expired2.png");
    expect(badApproveFallback.error).toBe("approval_failed");
  });
});
