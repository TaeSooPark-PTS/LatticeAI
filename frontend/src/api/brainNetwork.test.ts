import { beforeEach, describe, expect, it, vi } from "vitest";
import * as baseApi from "./base";
import { brainNetworkApi } from "./brainNetwork";

describe("brainNetworkApi", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("calls all Brain Network and subgraph share endpoints with expected arguments", async () => {
    const okResult = { ok: true, status: 200, source: "live" as const, data: {} as never };
    const getSpy = vi.spyOn(baseApi, "get").mockResolvedValue(okResult);
    const postSpy = vi.spyOn(baseApi, "post").mockResolvedValue(okResult);
    const delSpy = vi.spyOn(baseApi, "del").mockResolvedValue(okResult);

    await brainNetworkApi.shareStatus();
    await brainNetworkApi.identity();
    await brainNetworkApi.peers();
    await brainNetworkApi.pairPeer({
      name: "Workstation",
      base_url: "http://10.0.0.5:4825",
      public_key: "ed25519-pub",
    });
    await brainNetworkApi.unpairPeer("peer/1");
    await brainNetworkApi.pushPeer("peer-1");
    await brainNetworkApi.pushPeer("peer-1", "ws-eng");
    await brainNetworkApi.shareExport({ query: "architecture", limit: 25 });
    await brainNetworkApi.shareImport({ artifact: { nodes: [] }, dry_run: false });

    expect(getSpy).toHaveBeenCalledWith("/api/knowledge-graph/share", expect.any(Object));
    expect(getSpy).toHaveBeenCalledWith("/network/identity", expect.any(Object));
    expect(getSpy).toHaveBeenCalledWith("/network/peers", expect.any(Object));
    expect(postSpy).toHaveBeenCalledWith(
      "/network/peers",
      { name: "Workstation", base_url: "http://10.0.0.5:4825", public_key: "ed25519-pub" },
      expect.any(Object),
    );
    expect(delSpy).toHaveBeenCalledWith("/network/peers/peer%2F1", expect.any(Object));
    expect(postSpy).toHaveBeenCalledWith("/network/push/peer-1", {}, expect.any(Object));
    expect(postSpy).toHaveBeenCalledWith(
      "/network/push/peer-1",
      { workspace_id: "ws-eng" },
      expect.any(Object),
    );
    expect(postSpy).toHaveBeenCalledWith(
      "/api/knowledge-graph/share/export",
      { query: "architecture", limit: 25 },
      expect.any(Object),
    );
    expect(postSpy).toHaveBeenCalledWith(
      "/api/knowledge-graph/share/import",
      { artifact: { nodes: [] }, dry_run: false },
      expect.any(Object),
    );
  });
});
