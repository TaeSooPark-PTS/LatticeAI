import * as React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@/i18n/brain";
import { brainNetworkApi } from "@/api/brainNetwork";
import { BrainNetworkPanel } from "./BrainNetworkPanel";

function renderWithQuery(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const IDENTITY = {
  fingerprint: "fp-local-01",
  public_key: "ed25519-local-pub",
  algorithm: "ed25519",
  storage: "file",
};

describe("BrainNetworkPanel", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("shows opt-in banner and fallback identity when Brain Network is disabled", async () => {
    vi.spyOn(brainNetworkApi, "shareStatus").mockResolvedValue({
      ok: true,
      status: 200,
      source: "live",
      data: { enabled: false, flag: "LATTICEAI_BRAIN_NETWORK", device: IDENTITY },
    });
    vi.spyOn(brainNetworkApi, "identity").mockResolvedValue({
      ok: true,
      status: 200,
      source: "live",
      data: { fingerprint: "", public_key: "", algorithm: "ed25519" },
    });
    vi.spyOn(brainNetworkApi, "peers").mockResolvedValue({
      ok: true,
      status: 200,
      source: "live",
      data: { peers: [] },
    });

    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    renderWithQuery(<BrainNetworkPanel language="en" />);
    await waitFor(() => expect(screen.getByTestId("device-fingerprint")).toHaveTextContent("fp-local-01"));
    expect(screen.getByTestId("brain-network-disabled-banner")).toBeInTheDocument();
    expect(screen.getByText("Off (opt-in)")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Copy Key" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("ed25519-local-pub"));
    expect(screen.getByRole("button", { name: "Copied" })).toBeInTheDocument();
  });

  it("manages peers, subgraph export, and staged import when enabled", async () => {
    const shareSpy = vi.spyOn(brainNetworkApi, "shareStatus").mockResolvedValue({
      ok: true,
      status: 200,
      source: "live",
      data: { enabled: true },
    });
    vi.spyOn(brainNetworkApi, "identity").mockResolvedValue({
      ok: true,
      status: 200,
      source: "live",
      data: IDENTITY,
    });
    const PEER_LIST = {
      ok: true,
      status: 200,
      source: "live" as const,
      data: {
        peers: [
          {
            id: "peer-1",
            name: "Laptop",
            base_url: "http://10.0.0.2:4825",
            public_key: "pk-1",
            fingerprint: "fp-1",
          },
        ],
      },
    };
    vi.spyOn(brainNetworkApi, "peers")
      .mockResolvedValueOnce(PEER_LIST)
      .mockResolvedValueOnce(PEER_LIST)
      .mockResolvedValue({ ok: true, status: 200, source: "live", data: { peers: [] } });

    const pairSpy = vi
      .spyOn(brainNetworkApi, "pairPeer")
      .mockResolvedValueOnce({ ok: false, status: 400, source: "unavailable", error: "bad peer", data: {} as never })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        source: "live",
        data: { status: "paired" } as never,
      });
    const pushSpy = vi
      .spyOn(brainNetworkApi, "pushPeer")
      .mockResolvedValueOnce({ ok: false, status: 502, source: "unavailable", data: {} })
      .mockResolvedValueOnce({ ok: true, status: 200, source: "live", data: {} });
    const unpairSpy = vi
      .spyOn(brainNetworkApi, "unpairPeer")
      .mockResolvedValueOnce({ ok: false, status: 404, source: "unavailable", data: {} })
      .mockResolvedValueOnce({ ok: true, status: 200, source: "live", data: { status: "removed" } });
    const exportSpy = vi
      .spyOn(brainNetworkApi, "shareExport")
      .mockResolvedValueOnce({ ok: false, status: 500, source: "unavailable", data: {} })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        source: "live",
        data: { counts: { nodes: 4, edges: 3 }, signed: true },
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        source: "live",
        data: {},
      });
    const importSpy = vi
      .spyOn(brainNetworkApi, "shareImport")
      .mockResolvedValueOnce({ ok: false, status: 400, source: "unavailable", data: {} as never })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        source: "live",
        data: { staged: true, status: "pending_review", proposal_id: "prop-7" },
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        source: "live",
        data: { staged: true, status: "pending_review" },
      });

    renderWithQuery(<BrainNetworkPanel language="en" />);
    await waitFor(() => expect(screen.getByText("Laptop")).toBeInTheDocument());
    expect(screen.getByText("Enabled")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Device name"), { target: { value: "Studio" } });
    fireEvent.change(screen.getByLabelText("Base URL (http:// or https://)"), {
      target: { value: "https://s.local" },
    });
    fireEvent.change(screen.getByLabelText("Peer Public Key (Base64 Ed25519)"), {
      target: { value: "pk-2" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Pair Peer" }));
    await waitFor(() => expect(screen.getByTestId("brain-network-feedback")).toHaveTextContent("bad peer"));

    fireEvent.click(screen.getByRole("button", { name: "Pair Peer" }));
    await waitFor(() => expect(pairSpy).toHaveBeenCalledTimes(2));

    fireEvent.click(screen.getByRole("button", { name: "Push Subgraph" }));
    await waitFor(() => expect(pushSpy).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByTestId("brain-network-feedback")).toHaveTextContent("Push failed"));
    fireEvent.click(screen.getByRole("button", { name: "Push Subgraph" }));
    await waitFor(() => expect(screen.getByTestId("brain-network-feedback")).toHaveTextContent("pushed"));

    fireEvent.click(screen.getByRole("button", { name: "Unpair" }));
    await waitFor(() => expect(unpairSpy).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByTestId("brain-network-feedback")).toHaveTextContent("Unpair failed"));
    fireEvent.click(screen.getByRole("button", { name: "Unpair" }));
    await waitFor(() => expect(unpairSpy).toHaveBeenCalledTimes(2));

    fireEvent.click(screen.getByRole("button", { name: "Export Signed Bundle" }));
    await waitFor(() => expect(screen.getByTestId("brain-network-feedback")).toHaveTextContent("Export failed"));
    fireEvent.change(screen.getByLabelText("Topic or query filter (optional)"), {
      target: { value: "rust" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Export Signed Bundle" }));
    await waitFor(() =>
      expect(screen.getByTestId("brain-network-feedback")).toHaveTextContent(
        "Exported signed bundle: 4 nodes, 3 edges",
      ),
    );
    expect(exportSpy).toHaveBeenLastCalledWith({ query: "rust", limit: 40 });
    fireEvent.change(screen.getByLabelText("Topic or query filter (optional)"), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Export Signed Bundle" }));
    await waitFor(() =>
      expect(screen.getByTestId("brain-network-feedback")).toHaveTextContent(
        "Exported signed bundle: 0 nodes, 0 edges",
      ),
    );

    const importInput = screen.getByLabelText("Paste incoming signed subgraph bundle JSON");
    fireEvent.change(importInput, { target: { value: "{bad" } });
    fireEvent.click(screen.getByRole("button", { name: "Stage for Review" }));
    expect(screen.getByTestId("brain-network-feedback")).toHaveTextContent("Invalid JSON bundle");

    fireEvent.change(importInput, { target: { value: "[]" } });
    fireEvent.click(screen.getByRole("button", { name: "Stage for Review" }));
    expect(screen.getByTestId("brain-network-feedback")).toHaveTextContent("JSON must be a subgraph bundle object");

    fireEvent.change(importInput, { target: { value: '{"nodes":[]}' } });
    fireEvent.click(screen.getByRole("button", { name: "Stage for Review" }));
    await waitFor(() => expect(screen.getByTestId("brain-network-feedback")).toHaveTextContent("Import failed"));

    fireEvent.click(screen.getByRole("button", { name: "Stage for Review" }));
    await waitFor(() =>
      expect(screen.getByTestId("brain-network-feedback")).toHaveTextContent(
        "Staged in Review Center (proposal prop-7)",
      ),
    );
    fireEvent.change(importInput, { target: { value: '{"nodes":[]}' } });
    fireEvent.click(screen.getByRole("button", { name: "Stage for Review" }));
    await waitFor(() =>
      expect(screen.getByTestId("brain-network-feedback")).toHaveTextContent(
        "Staged in Review Center (proposal pending)",
      ),
    );
    expect(importSpy).toHaveBeenCalledTimes(3);

    const beforeRefresh = shareSpy.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(shareSpy.mock.calls.length).toBeGreaterThan(beforeRefresh));
  });

  it("handles missing device identity gracefully", async () => {
    vi.spyOn(brainNetworkApi, "shareStatus").mockResolvedValue({
      ok: true,
      status: 200,
      source: "live",
      data: { enabled: false },
    });
    vi.spyOn(brainNetworkApi, "identity").mockResolvedValue({
      ok: true,
      status: 200,
      source: "live",
      data: { fingerprint: "", public_key: "", algorithm: "ed25519" },
    });
    vi.spyOn(brainNetworkApi, "peers").mockResolvedValue({
      ok: true,
      status: 200,
      source: "live",
      data: { peers: [] },
    });

    renderWithQuery(<BrainNetworkPanel language="ko" />);
    await waitFor(() => expect(screen.getByTestId("device-fingerprint")).toHaveTextContent("-"));
    expect(screen.getByTestId("device-public-key")).toHaveTextContent("-");
  });
});
