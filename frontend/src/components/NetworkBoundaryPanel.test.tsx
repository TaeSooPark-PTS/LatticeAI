import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { useAppStore } from "@/store/appStore";
import { NetworkBoundaryPanel } from "./NetworkBoundaryPanel";

const CATALOG = [
  {
    id: "local_only",
    label: "Local only", label_ko: "로컬만",
    summary: "Nothing leaves this machine.",
    summary_ko: "이 컴퓨터를 벗어나지 않습니다.",
    risk: "low", requires_ack: false,
  },
  {
    id: "cloud_allowed",
    label: "Cloud streaming allowed", label_ko: "클라우드 스트리밍 허용",
    summary: "Minimal related nodes may be sent to a cloud LLM.",
    summary_ko: "관련된 최소 노드만 클라우드 LLM으로 전송될 수 있습니다.",
    risk: "medium", requires_ack: true,
    warning: "Cloud mode sends a compact summary of selected local nodes.",
    warning_ko: "클라우드 모드는 선택된 로컬 노드의 압축 요약을 전송합니다.",
  },
  {
    // A mode added server-side: English-only copy, a risk word this client has
    // never heard of, an acknowledgement demanded but no warning shipped.
    id: "peer_sync",
    label: "Peer sync (beta)",
    summary: "Streams to paired devices.",
    risk: "experimental", requires_ack: true,
  },
];

function state(mode: string, policy: Record<string, unknown> = {}) {
  const entry = CATALOG.find((option) => option.id === mode)!;
  return {
    mode,
    label: entry.label,
    label_ko: entry.label_ko,
    allows_cloud: mode === "cloud_allowed",
    requires_ack: entry.requires_ack,
    warning_ko: entry.warning_ko ?? null,
    policy: {
      blocked_node_types: [],
      blocked_metadata_flags: ["sensitive", "private"],
      auto_commit: false,
      allow_multimodal: false,
      min_extraction_confidence: 0.55,
      ...policy,
    },
    token_budget: {},
    catalog: CATALOG,
  };
}

function mockGet(mode = "local_only", ok = true, policy: Record<string, unknown> = {}) {
  return vi.spyOn(latticeApi, "networkBoundary").mockResolvedValue({
    ok, status: ok ? 200 : 503, source: ok ? "live" : "unavailable",
    data: state(mode, policy), error: ok ? undefined : "server unavailable",
  } as never);
}

function mockSet(ok = true, error?: string) {
  return vi.spyOn(latticeApi, "setNetworkBoundary").mockResolvedValue({
    ok, status: ok ? 200 : 400, source: "live",
    data: { mode: "cloud_allowed" }, error,
  } as never);
}

function mockPreview(over: Record<string, unknown> = {}) {
  return vi.spyOn(latticeApi, "previewCloudContext").mockResolvedValue({
    ok: true, status: 200, source: "live",
    data: {
      mode: "local_only", allows_cloud: false,
      node_ids: ["n1", "n2"], keywords: ["release"],
      titles: ["릴리스 절차", "배포 체크리스트"], types: ["Document", "Note"],
      token_estimate: 412, quality: "ok", compact_preview: "...",
      token_budget: {}, would_block: null,
      ...over,
    },
  } as never);
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <NetworkBoundaryPanel />
    </QueryClientProvider>,
  );
}

describe("NetworkBoundaryPanel", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useAppStore.setState({ language: "ko" });
  });

  it("renders the catalog the server serves, not a hardcoded mode list", async () => {
    mockGet("local_only");
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("network-boundary-panel")).toBeTruthy());
    for (const option of CATALOG) {
      expect(screen.getByTestId(`network-boundary-option-${option.id}`)).toBeTruthy();
    }
    expect(screen.getByTestId("network-boundary-active").textContent).toBe("로컬만");
  });

  it("uses the localized catalog copy for the active language", async () => {
    mockGet("local_only");
    useAppStore.setState({ language: "en" });
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("network-boundary-panel")).toBeTruthy());
    expect(screen.getByTestId("network-boundary-active").textContent).toBe("Local only");
    expect(screen.getByTestId("network-boundary-option-cloud_allowed").textContent)
      .toContain("Minimal related nodes may be sent to a cloud LLM.");

    // The warning too — the English reader gets the English caution.
    await userEvent.click(screen.getByTestId("network-boundary-option-cloud_allowed"));
    expect(screen.getByText("Cloud mode sends a compact summary of selected local nodes.")).toBeTruthy();
  });

  it("falls back to the server's English label for an active mode without Korean copy", async () => {
    mockGet("peer_sync");
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("network-boundary-panel")).toBeTruthy());
    expect(screen.getByTestId("network-boundary-active").textContent).toBe("Peer sync (beta)");
  });

  it("renders a server-added mode it has never heard of, warts and all", async () => {
    mockGet("local_only");
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("network-boundary-panel")).toBeTruthy());
    // No Korean copy shipped — fall back to the server's English rather than hiding it.
    const row = screen.getByTestId("network-boundary-option-peer_sync");
    expect(row.textContent).toContain("Peer sync (beta)");
    expect(row.textContent).toContain("Streams to paired devices.");

    // requires_ack still gates it, even with no warning copy to show.
    await userEvent.click(row);
    expect(screen.getByTestId("network-boundary-ack")).toBeTruthy();
    expect((screen.getByTestId("network-boundary-apply") as HTMLButtonElement).disabled).toBe(true);
  });

  it("blocks cloud until the risk is acknowledged, then sends the ack", async () => {
    mockGet("local_only");
    const setMode = mockSet();
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("network-boundary-panel")).toBeTruthy());
    await userEvent.click(screen.getByTestId("network-boundary-option-cloud_allowed"));

    // The server refuses cloud_allowed without acknowledge_risk, so the UI must
    // not offer to send the request until the box is ticked.
    expect((screen.getByTestId("network-boundary-apply") as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("클라우드 모드는 선택된 로컬 노드의 압축 요약을 전송합니다.")).toBeTruthy();
    expect(setMode).not.toHaveBeenCalled();

    await userEvent.click(screen.getByTestId("network-boundary-ack"));
    await userEvent.click(screen.getByTestId("network-boundary-apply"));
    await waitFor(() => expect(setMode).toHaveBeenCalledWith("cloud_allowed", true));
  });

  it("resets the acknowledgement when the selection changes", async () => {
    mockGet("local_only");
    const setMode = mockSet();
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("network-boundary-panel")).toBeTruthy());
    await userEvent.click(screen.getByTestId("network-boundary-option-cloud_allowed"));
    await userEvent.click(screen.getByTestId("network-boundary-ack"));
    await userEvent.click(screen.getByTestId("network-boundary-option-local_only"));
    await userEvent.click(screen.getByTestId("network-boundary-option-cloud_allowed"));

    expect((screen.getByTestId("network-boundary-apply") as HTMLButtonElement).disabled).toBe(true);
    expect(setMode).not.toHaveBeenCalled();
  });

  it("lists the actual memories a question would send, before sending anything", async () => {
    mockGet("local_only");
    mockPreview();
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("network-boundary-panel")).toBeTruthy());
    await userEvent.type(screen.getByTestId("network-boundary-probe"), "릴리스 어떻게 하지");
    await userEvent.click(screen.getByTestId("network-boundary-preview"));

    await waitFor(() =>
      expect(screen.getByTestId("network-boundary-preview-result")).toBeTruthy());
    expect(screen.getByText("· 릴리스 절차")).toBeTruthy();
    expect(screen.getByText("· 배포 체크리스트")).toBeTruthy();
    // On local_only the preview is hypothetical, and must say so rather than
    // implying data just left the machine.
    expect(screen.getByText(/아무것도 나가지 않습니다/)).toBeTruthy();
  });

  it("says plainly when a question would be refused by the token budget", async () => {
    mockGet("cloud_allowed");
    mockPreview({ allows_cloud: true, would_block: "session limit exceeded" });
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("network-boundary-panel")).toBeTruthy());
    await userEvent.type(screen.getByTestId("network-boundary-probe"), "긴 질문");
    await userEvent.click(screen.getByTestId("network-boundary-preview"));

    await waitFor(() =>
      expect(screen.getByText("이 질문은 사용량 한도에 걸려 전송되지 않습니다")).toBeTruthy());
  });

  it("reports an empty slice instead of implying everything would be sent", async () => {
    mockGet("local_only");
    mockPreview({ titles: [], node_ids: [], token_estimate: 0 });
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("network-boundary-panel")).toBeTruthy());
    await userEvent.type(screen.getByTestId("network-boundary-probe"), "관련 없는 질문");
    await userEvent.click(screen.getByTestId("network-boundary-preview"));

    await waitFor(() =>
      expect(screen.getByText("이 질문으로 내보낼 기억이 없습니다.")).toBeTruthy());
  });

  it("hides the write-back policy while nothing may leave the machine", async () => {
    mockGet("local_only");
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("network-boundary-panel")).toBeTruthy());
    // Dead switches invite the belief that they do something.
    expect(screen.queryByTestId("network-boundary-policy")).toBeNull();
  });

  it("shows the write-back policy once cloud is permitted, defaulting to review", async () => {
    mockGet("cloud_allowed");
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("network-boundary-policy")).toBeTruthy());
    const autoCommit = screen.getByTestId("network-boundary-auto-commit") as HTMLInputElement;
    const multimodal = screen.getByTestId("network-boundary-multimodal") as HTMLInputElement;
    expect(autoCommit.checked).toBe(false);
    expect(multimodal.checked).toBe(false);
  });

  it("persists a policy change through the policy endpoint", async () => {
    mockGet("cloud_allowed");
    const savePolicy = vi.spyOn(latticeApi, "setHybridPolicy").mockResolvedValue({
      ok: true, status: 200, source: "live", data: {},
    } as never);
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("network-boundary-policy")).toBeTruthy());
    await userEvent.click(screen.getByTestId("network-boundary-auto-commit"));

    await waitFor(() => expect(savePolicy).toHaveBeenCalledWith({ auto_commit: true }));
  });

  it("persists the multimodal switch the same way", async () => {
    mockGet("cloud_allowed");
    const savePolicy = vi.spyOn(latticeApi, "setHybridPolicy").mockResolvedValue({
      ok: true, status: 200, source: "live", data: {},
    } as never);
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("network-boundary-policy")).toBeTruthy());
    await userEvent.click(screen.getByTestId("network-boundary-multimodal"));

    await waitFor(() => expect(savePolicy).toHaveBeenCalledWith({ allow_multimodal: true }));
  });

  it("treats a missing policy object as review-everything defaults", async () => {
    vi.spyOn(latticeApi, "networkBoundary").mockResolvedValue({
      ok: true, status: 200, source: "live",
      data: { ...state("cloud_allowed"), policy: undefined },
    } as never);
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("network-boundary-policy")).toBeTruthy());
    expect((screen.getByTestId("network-boundary-auto-commit") as HTMLInputElement).checked).toBe(false);
    expect((screen.getByTestId("network-boundary-multimodal") as HTMLInputElement).checked).toBe(false);
  });

  it("says when the preview itself is unavailable", async () => {
    mockGet("local_only");
    vi.spyOn(latticeApi, "previewCloudContext").mockResolvedValue({
      ok: false, status: 503, source: "unavailable", data: {}, error: "preview engine down",
    } as never);
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("network-boundary-panel")).toBeTruthy());
    await userEvent.type(screen.getByTestId("network-boundary-probe"), "릴리스");
    await userEvent.click(screen.getByTestId("network-boundary-preview"));

    await waitFor(() => expect(screen.getByText("preview engine down")).toBeTruthy());
    expect(screen.queryByTestId("network-boundary-preview-result")).toBeNull();
  });

  it("offers no hold control for a memory the server did not identify", async () => {
    mockGet("cloud_allowed");
    mockPreview({ allows_cloud: true, titles: ["공개 메모", "이름 없는 메모"], node_ids: ["n1"] });
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("network-boundary-panel")).toBeTruthy());
    await userEvent.type(screen.getByTestId("network-boundary-probe"), "메모");
    await userEvent.click(screen.getByTestId("network-boundary-preview"));

    await waitFor(() => expect(screen.getByTestId("network-boundary-hold-0")).toBeTruthy());
    expect(screen.getByText("· 이름 없는 메모")).toBeTruthy();
    expect(screen.queryByTestId("network-boundary-hold-1")).toBeNull();
  });

  it("reports an unavailable dial instead of showing local_only as confirmed", async () => {
    mockGet("local_only", false);
    renderPanel();

    await waitFor(() => expect(screen.getByText("요청을 처리하지 못했어요")).toBeTruthy());
    expect(screen.queryByTestId("network-boundary-panel")).toBeNull();
  });

  it("surfaces a rejected change rather than claiming success", async () => {
    mockGet("local_only");
    mockSet(false, "cloud_allowed mode requires acknowledge_risk=true");
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("network-boundary-panel")).toBeTruthy());
    await userEvent.click(screen.getByTestId("network-boundary-option-cloud_allowed"));
    await userEvent.click(screen.getByTestId("network-boundary-ack"));
    await userEvent.click(screen.getByTestId("network-boundary-apply"));

    await waitFor(() =>
      expect(screen.getByText("cloud_allowed mode requires acknowledge_risk=true")).toBeTruthy());
    expect(screen.queryByTestId("network-boundary-applied")).toBeNull();
  });
});

describe("NetworkBoundaryPanel — holding a memory back", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useAppStore.setState({ language: "ko" });
  });

  it("marks a previewed memory as never-leaving", async () => {
    mockGet("cloud_allowed");
    mockPreview({ allows_cloud: true });
    const mark = vi.spyOn(latticeApi, "setNodeSensitivity").mockResolvedValue({
      ok: true, status: 200, source: "live",
      data: { ok: true, node_id: "n1", local_only: true },
    } as never);
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("network-boundary-panel")).toBeTruthy());
    await userEvent.type(screen.getByTestId("network-boundary-probe"), "릴리스");
    await userEvent.click(screen.getByTestId("network-boundary-preview"));

    await waitFor(() => expect(screen.getByTestId("network-boundary-hold-0")).toBeTruthy());
    await userEvent.click(screen.getByTestId("network-boundary-hold-0"));

    await waitFor(() =>
      expect(mark).toHaveBeenCalledWith("n1", true));
    // Held rows read as excluded, so the list stays an honest picture of what
    // would actually be sent.
    await waitFor(() =>
      expect(screen.getByTestId("network-boundary-hold-0").textContent).toBe("다시 허용"));
  });

  it("releases a held memory on a second press", async () => {
    mockGet("cloud_allowed");
    mockPreview({ allows_cloud: true });
    const mark = vi.spyOn(latticeApi, "setNodeSensitivity").mockResolvedValue({
      ok: true, status: 200, source: "live",
      data: { ok: true, node_id: "n1", local_only: true },
    } as never);
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("network-boundary-panel")).toBeTruthy());
    await userEvent.type(screen.getByTestId("network-boundary-probe"), "릴리스");
    await userEvent.click(screen.getByTestId("network-boundary-preview"));

    await waitFor(() => expect(screen.getByTestId("network-boundary-hold-0")).toBeTruthy());
    await userEvent.click(screen.getByTestId("network-boundary-hold-0"));
    await waitFor(() =>
      expect(screen.getByTestId("network-boundary-hold-0").textContent).toBe("다시 허용"));

    await userEvent.click(screen.getByTestId("network-boundary-hold-0"));
    await waitFor(() => expect(mark).toHaveBeenLastCalledWith("n1", false));
    // Released: the row offers to hold it again and loses its strike-through.
    await waitFor(() =>
      expect(screen.getByTestId("network-boundary-hold-0").textContent).toBe("내보내지 않기"));
  });

  it("does not mark anything when the server rejects the change", async () => {
    mockGet("cloud_allowed");
    mockPreview({ allows_cloud: true });
    vi.spyOn(latticeApi, "setNodeSensitivity").mockResolvedValue({
      ok: false, status: 404, source: "live",
      data: { ok: false, node_id: "n1", local_only: true }, error: "node not found",
    } as never);
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("network-boundary-panel")).toBeTruthy());
    await userEvent.type(screen.getByTestId("network-boundary-probe"), "릴리스");
    await userEvent.click(screen.getByTestId("network-boundary-preview"));
    await waitFor(() => expect(screen.getByTestId("network-boundary-hold-0")).toBeTruthy());
    await userEvent.click(screen.getByTestId("network-boundary-hold-0"));

    // Still offering to hold it: the UI must not claim a change the server refused.
    await waitFor(() =>
      expect(screen.getByTestId("network-boundary-hold-0").textContent).toBe("내보내지 않기"));
  });
});
