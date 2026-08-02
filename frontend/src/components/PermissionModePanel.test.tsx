import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { useAppStore } from "@/store/appStore";
import { PermissionModePanel } from "./PermissionModePanel";

const CATALOG = [
  {
    id: "strict",
    label: "Strict", label_ko: "엄격",
    summary: "Reads auto; writes need approval.",
    summary_ko: "읽기는 자동, 쓰기는 승인.",
    risk: "low", requires_ack: false,
  },
  {
    id: "trusted",
    label: "Trusted", label_ko: "신뢰",
    summary: "Workspace writes auto-run.",
    summary_ko: "워크스페이스 쓰기 자동.",
    risk: "medium", requires_ack: false,
  },
  {
    id: "bypass",
    label: "Bypass", label_ko: "바이패스",
    summary: "YOLO inside the workspace.",
    summary_ko: "워크스페이스 안에서 전부 자동.",
    risk: "high", requires_ack: true,
    warning: "Bypass skips routine approval prompts.",
    warning_ko: "바이패스는 일상 승인 프롬프트를 건너뜁니다.",
  },
];

function state(mode: string) {
  const entry = CATALOG.find((option) => option.id === mode)!;
  return {
    mode,
    label: entry.label,
    label_ko: entry.label_ko,
    risk: entry.risk,
    requires_ack: entry.requires_ack,
    proposal_first: mode === "strict",
    workspace_writes_auto: mode !== "strict",
    knowledge_reads_auto: mode !== "strict",
    exec_auto: mode === "bypass",
    computer_observation_auto: mode !== "strict",
    computer_control_auto: mode === "bypass",
    circuit_breakers: true,
    catalog: CATALOG,
  };
}

function mockGet(mode = "strict", ok = true) {
  return vi.spyOn(latticeApi, "permissionMode").mockResolvedValue({
    ok, status: ok ? 200 : 503, source: ok ? "live" : "unavailable",
    data: state(mode), error: ok ? undefined : "server unavailable",
  } as never);
}

function mockSet(ok = true, error?: string) {
  return vi.spyOn(latticeApi, "setPermissionMode").mockResolvedValue({
    ok, status: ok ? 200 : 400, source: "live",
    data: state("trusted"), error,
  } as never);
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PermissionModePanel />
    </QueryClientProvider>,
  );
}

describe("PermissionModePanel", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useAppStore.setState({ language: "ko" });
  });

  it("renders the catalog the server serves, not a hardcoded mode list", async () => {
    mockGet("strict");
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("permission-mode-panel")).toBeTruthy());
    for (const option of CATALOG) {
      expect(screen.getByTestId(`permission-mode-option-${option.id}`)).toBeTruthy();
    }
    // The server ships "엄격"/"Strict" — the engineering name for the mode.
    // This dial is on the first screen, so the mode is named by what it does.
    expect(screen.getByTestId("permission-mode-active").textContent).toBe("먼저 물어보기");
  });

  it("names the modes in the reader's language, not the server's", async () => {
    mockGet("strict");
    useAppStore.setState({ language: "en" });
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("permission-mode-panel")).toBeTruthy());
    expect(screen.getByTestId("permission-mode-active").textContent).toBe("Ask me first");
    expect(screen.getByTestId("permission-mode-option-trusted").textContent)
      .toContain("Still asks before running programs");
    // "Bypass" is the word this pass exists to remove from the first screen.
    expect(document.body.textContent).not.toMatch(/Bypass|YOLO/);
  });

  it("falls back to the server's own copy for a mode it does not know", async () => {
    // The catalog is the server's to define. A mode added server-side must
    // still render — translating by id must not become a hidden allowlist.
    const extended = [...CATALOG, {
      id: "supervised",
      label: "Supervised", label_ko: "감독",
      summary: "A mode this client has never heard of.",
      summary_ko: "이 클라이언트가 모르는 모드.",
      risk: "low", requires_ack: false,
    }];
    vi.spyOn(latticeApi, "permissionMode").mockResolvedValue({
      ok: true, status: 200, source: "live",
      data: { ...state("strict"), catalog: extended },
    } as never);
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("permission-mode-panel")).toBeTruthy());
    const row = screen.getByTestId("permission-mode-option-supervised");
    expect(row.textContent).toContain("감독");
    expect(row.textContent).toContain("이 클라이언트가 모르는 모드.");
  });

  it("keeps apply disabled until a different mode is chosen", async () => {
    mockGet("strict");
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("permission-mode-panel")).toBeTruthy());
    const apply = screen.getByTestId("permission-mode-apply") as HTMLButtonElement;
    expect(apply.disabled).toBe(true);

    await userEvent.click(screen.getByTestId("permission-mode-option-trusted"));
    expect((screen.getByTestId("permission-mode-apply") as HTMLButtonElement).disabled).toBe(false);
  });

  it("applies a non-acknowledged mode without an extra prompt", async () => {
    mockGet("strict");
    const setMode = mockSet();
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("permission-mode-panel")).toBeTruthy());
    await userEvent.click(screen.getByTestId("permission-mode-option-trusted"));
    await userEvent.click(screen.getByTestId("permission-mode-apply"));

    await waitFor(() => expect(setMode).toHaveBeenCalledWith("trusted", false));
  });

  it("blocks bypass until the risk is acknowledged, then sends the ack", async () => {
    mockGet("strict");
    const setMode = mockSet();
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("permission-mode-panel")).toBeTruthy());
    await userEvent.click(screen.getByTestId("permission-mode-option-bypass"));

    // The server refuses bypass without acknowledge_risk, so the UI must not
    // even offer to send the request until the box is ticked.
    expect((screen.getByTestId("permission-mode-apply") as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/평소의 확인 창이 뜨지 않습니다/)).toBeTruthy();
    expect(setMode).not.toHaveBeenCalled();

    await userEvent.click(screen.getByTestId("permission-mode-ack"));
    expect((screen.getByTestId("permission-mode-apply") as HTMLButtonElement).disabled).toBe(false);

    await userEvent.click(screen.getByTestId("permission-mode-apply"));
    await waitFor(() => expect(setMode).toHaveBeenCalledWith("bypass", true));
  });

  it("resets the acknowledgement when the selection changes", async () => {
    mockGet("strict");
    const setMode = mockSet();
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("permission-mode-panel")).toBeTruthy());
    await userEvent.click(screen.getByTestId("permission-mode-option-bypass"));
    await userEvent.click(screen.getByTestId("permission-mode-ack"));
    await userEvent.click(screen.getByTestId("permission-mode-option-trusted"));
    await userEvent.click(screen.getByTestId("permission-mode-option-bypass"));

    expect((screen.getByTestId("permission-mode-apply") as HTMLButtonElement).disabled).toBe(true);
    expect(setMode).not.toHaveBeenCalled();
  });

  it("does not require an acknowledgement to re-confirm the active mode", async () => {
    mockGet("bypass");
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("permission-mode-panel")).toBeTruthy());
    // bypass is already active — no warning box, and apply stays disabled
    // because nothing would change.
    expect(screen.queryByTestId("permission-mode-ack")).toBeNull();
    expect((screen.getByTestId("permission-mode-apply") as HTMLButtonElement).disabled).toBe(true);
  });

  it("reports an unavailable dial instead of showing a default mode as real", async () => {
    mockGet("strict", false);
    renderPanel();

    await waitFor(() =>
      expect(screen.getByText("요청을 처리하지 못했어요")).toBeTruthy());
    expect(screen.queryByTestId("permission-mode-panel")).toBeNull();
  });

  it("surfaces a rejected change rather than claiming success", async () => {
    mockGet("strict");
    mockSet(false, "bypass mode requires acknowledge_risk=true");
    renderPanel();

    await waitFor(() => expect(screen.getByTestId("permission-mode-panel")).toBeTruthy());
    await userEvent.click(screen.getByTestId("permission-mode-option-trusted"));
    await userEvent.click(screen.getByTestId("permission-mode-apply"));

    await waitFor(() =>
      expect(screen.getByText("bypass mode requires acknowledge_risk=true")).toBeTruthy());
    expect(screen.queryByTestId("permission-mode-applied")).toBeNull();
  });
});
