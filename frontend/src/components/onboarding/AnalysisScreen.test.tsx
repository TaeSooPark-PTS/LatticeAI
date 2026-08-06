import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { t } from "@/i18n";
import { useAppStore } from "@/store/appStore";
import { AnalysisScreen } from "./AnalysisScreen";
import type { FlowAnalysis } from "./recommendationModel";

/**
 * The hardware read-out shown while the setup probes run. What matters is that
 * it never claims more than the probes returned: no analysis means "checking",
 * a partial analysis fills only the cells it can prove, and the continue
 * button waits for either an answer or an explicit failure.
 */

function renderScreen(analysis: FlowAnalysis | null, error: string | null = null, onContinue = vi.fn()) {
  render(<AnalysisScreen analysis={analysis} error={error} onContinue={onContinue} />);
  return onContinue;
}

beforeEach(() => {
  useAppStore.setState({ language: "ko" });
});

describe("AnalysisScreen", () => {
  it("shows five checking facts and a disabled continue while probes run", () => {
    renderScreen(null);
    expect(screen.getAllByText(t("ko", "flow.analysis.checking"))).toHaveLength(5);
    expect(screen.getByText(t("ko", "flow.analysis.finding"))).toBeTruthy();
    expect(screen.getByText(t("ko", "flow.analysis.wait"))).toBeTruthy();
    const cta = screen.getByRole("button", { name: t("ko", "flow.analysis.continue") }) as HTMLButtonElement;
    expect(cta.disabled).toBe(true);
  });

  it("announces an error and lets the person continue past it", async () => {
    const onContinue = renderScreen(null, "probe exploded");
    expect(screen.getByRole("alert").textContent).toBe("probe exploded");
    const cta = screen.getByRole("button", { name: t("ko", "flow.analysis.continue") }) as HTMLButtonElement;
    expect(cta.disabled).toBe(false);
    await userEvent.click(cta);
    expect(onContinue).toHaveBeenCalledTimes(1);
  });

  it("reads a full Apple Silicon analysis into ready facts", () => {
    renderScreen({
      setup: { environment: { installed_runtimes: ["mlx"] } },
      recommendations: {
        profile: {},
        recommendations: { ram_gb: 32, apple_silicon: true, top_pick: { name: "Gemma 4 12B" } },
      },
      models: { loaded: [{ id: "a" }, { id: "b" }] },
      sysinfo: {},
    });
    expect(screen.getByText(t("ko", "flow.analysis.apple"))).toBeTruthy();
    expect(screen.getByText("32 GB")).toBeTruthy();
    expect(screen.getByText(t("ko", "flow.analysis.supportReady"))).toBeTruthy();
    expect(screen.getByText(t("ko", "flow.analysis.modelsInstalled", { count: 2 }))).toBeTruthy();
    expect(screen.getByText(t("ko", "flow.analysis.bestFit", { model: "Gemma 4 12B" }))).toBeTruthy();
    expect(screen.getByText(t("ko", "flow.analysis.ready"))).toBeTruthy();
  });

  it("derives the same facts from the alternate profile fields", () => {
    renderScreen({
      setup: null,
      recommendations: {
        profile: { arch: "arm64", ram_mb: 16384, installed_runtimes: ["mlx-lm"], gpu: { vendor: "Apple" } },
        recommendations: { top_pick: { id: "mlx-community/Qwen3-VL-8B-Instruct-4bit" } },
      },
      models: {},
      sysinfo: { gpu_mem_gb: 16 },
    });
    // arm arch counts as Apple Silicon even without the explicit flag.
    expect(screen.getByText(t("ko", "flow.analysis.apple"))).toBeTruthy();
    expect(screen.getByText("16 GB")).toBeTruthy();
    expect(screen.getByText(t("ko", "flow.analysis.localReady"))).toBeTruthy();
    // A top pick with only an id still names a friendly model.
    expect(screen.getByText(t("ko", "flow.analysis.bestFit", { model: "Qwen 3 8B" }))).toBeTruthy();
    expect(screen.getByText(t("ko", "flow.analysis.noModels"))).toBeTruthy();
  });

  it("claims nothing for an empty analysis payload", () => {
    renderScreen({});
    expect(screen.getByText(t("ko", "flow.analysis.fact.computer"))).toBeTruthy();
    expect(screen.getByText(t("ko", "flow.analysis.detected"))).toBeTruthy();
    expect(screen.getByText(t("ko", "flow.analysis.standardLocal"))).toBeTruthy();
    expect(screen.getByText(t("ko", "flow.analysis.supportInstall"))).toBeTruthy();
    expect(screen.getByText(t("ko", "flow.analysis.privateRecommended"))).toBeTruthy();
  });

  it.each([
    ["Darwin 23", "Mac"],
    ["Windows NT", t("ko", "flow.analysis.os.windows")],
    ["linux-gnu", t("ko", "flow.analysis.os.linux")],
    ["solaris", t("ko", "flow.analysis.fact.computer")],
  ])("names the operating system %s as %s on non-Apple hardware", (os, label) => {
    renderScreen({ setup: { environment: { os } }, recommendations: {}, models: {}, sysinfo: {} });
    expect(screen.getByText(label)).toBeTruthy();
  });

  it("shows a GPU-mem-only machine as locally ready without vendor data", () => {
    renderScreen({ setup: {}, recommendations: {}, models: {}, sysinfo: { gpu_mem_gb: 8 } });
    expect(screen.getByText(t("ko", "flow.analysis.localReady"))).toBeTruthy();
  });

  it("collects runtimes announced by the recommendations profile alone", () => {
    renderScreen({
      setup: {},
      recommendations: { recommendations: { installed_runtimes: ["local"] } },
      models: {},
      sysinfo: {},
    });
    expect(screen.getByText(t("ko", "flow.analysis.supportReady"))).toBeTruthy();
  });
});
