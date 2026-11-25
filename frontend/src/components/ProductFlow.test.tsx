import { act, fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { t } from "@/i18n";
import { fail, ok, renderPage, type RenderPageOptions } from "@/test/renderPage";
import { brainStateForStep, pickInstallModel, ProductFlow } from "./ProductFlow";
import { fallbackModel, type RecommendedModel } from "@/components/onboarding/recommendationModel";


const FLOW_COMPLETE_KEY = "lattice.productFlow.complete";

const supportedRow = {
  id: "mlx-community/Gemma-3-12B-Instruct-4bit",
  display_name: "Gemma 3 12B",
  load_status: "ready",
  runtime_compatibility: { supported: true },
};

const readyProbes: RenderPageOptions["api"] = {
  models: ok({ recommended: [supportedRow], catalog: [], loaded: [] }),
  modelRecommendations: ok({ profile: { arch: "arm64", ram_mb: 16384 }, recommendations: {} }),
  setupScan: ok({ environment: { arch: "arm64" } }),
  sysinfo: ok({}),
};

function renderFlow(api: RenderPageOptions["api"] = readyProbes) {
  const onComplete = vi.fn();
  renderPage(<ProductFlow onComplete={onComplete} />, { language: "ko", api });
  return onComplete;
}

async function goToLogin() {
  fireEvent.click(screen.getByRole("button", { name: new RegExp(t("ko", "flow.wake.primary")) }));
  await screen.findByRole("heading", { name: t("ko", "flow.login.title") });
}

async function signIn() {
  fireEvent.change(screen.getByLabelText(t("ko", "flow.password")), { target: { value: "pw" } });
  fireEvent.submit(screen.getByRole("form", { name: t("ko", "flow.login.title") }));
  await screen.findByText(t("ko", "flow.recommend.title"));
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("ProductFlow", () => {
  it("wakes with the hero Brain, the promise cards and the three-step plan", () => {
    renderFlow();
    expect(screen.getByText(t("ko", "flow.wake.title"))).toBeTruthy();
    expect(screen.getByText(t("ko", "flow.wake.value.local.k"))).toBeTruthy();
    expect(screen.getByText(t("ko", "flow.wake.value.instant.k"))).toBeTruthy();
    expect(screen.getByText(t("ko", "flow.wake.value.brain.k"))).toBeTruthy();
    expect(screen.getByText(t("ko", "flow.wake.step.identity"))).toBeTruthy();
    expect(document.querySelector(".ritual-brain")?.getAttribute("data-scale")).toBe("hero");
    expect(screen.getByTestId("living-brain")).toBeTruthy();
    expect(screen.getByText(t("ko", "brain.edition"))).toBeTruthy();
  });

  it("lets an existing owner skip straight into the product", () => {
    const onComplete = renderFlow();
    fireEvent.click(screen.getByRole("button", { name: t("ko", "flow.wake.existing") }));
    expect(onComplete).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem(FLOW_COMPLETE_KEY)).toBe("true");
  });

  it("switches the whole ritual to English from the chooser", async () => {
    renderFlow();
    await userEvent.click(screen.getByRole("button", { name: "English" }));
    expect(screen.getByText(t("en", "flow.wake.title"))).toBeTruthy();
  });

  it("shrinks the Brain to a wordmark once the ritual moves past the welcome", async () => {
    renderFlow();
    await goToLogin();
    expect(document.querySelector(".ritual-brain")?.getAttribute("data-scale")).toBe("mark");
  });

  it("walks wake → login → recommendation → install and back", async () => {
    const onComplete = renderFlow();
    await goToLogin();
    await signIn();

    const hero = await screen.findByRole("heading", { level: 2, name: "Gemma 3 12B" });
    expect(hero).toBeTruthy();

    fireEvent.click(document.querySelector(".ritual-primary-model-button") as HTMLButtonElement);
    await screen.findByText(t("ko", "flow.install.title.ready"));

    fireEvent.click(screen.getByRole("button", { name: t("ko", "flow.install.back") }));
    await screen.findByRole("heading", { level: 2, name: "Gemma 3 12B" });

    fireEvent.click(screen.getByRole("button", { name: t("ko", "flow.recommend.back") }));
    await screen.findByRole("heading", { name: t("ko", "flow.login.title") });
    await signIn();

    fireEvent.click(document.querySelector(".ritual-primary-model-button") as HTMLButtonElement);
    await screen.findByText(t("ko", "flow.install.title.ready"));
    fireEvent.click(screen.getByRole("button", { name: t("ko", "flow.install.later") }));
    expect(onComplete).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem(FLOW_COMPLETE_KEY)).toBe("true");
  });

  it("skips model setup from the recommendation step", async () => {
    const onComplete = renderFlow();
    await goToLogin();
    await signIn();
    await screen.findByRole("heading", { level: 2, name: "Gemma 3 12B" });
    fireEvent.click(screen.getByRole("button", { name: t("ko", "flow.recommend.skip") }));
    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it("completes the flow when the install finishes and the person enters", async () => {
    const prepare = vi.fn(async (_body: unknown, handlers: { onDone?: (d: Record<string, unknown>) => void }) => {
      handlers.onDone?.({ status: "done" });
      return ok({ status: "done" });
    });
    const onComplete = renderFlow({ ...readyProbes, streamModelPrepare: prepare });
    await goToLogin();
    await signIn();
    await screen.findByRole("heading", { level: 2, name: "Gemma 3 12B" });
    fireEvent.click(document.querySelector(".ritual-primary-model-button") as HTMLButtonElement);
    await screen.findByText(t("ko", "flow.install.title.ready"));

    vi.useFakeTimers();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: t("ko", "flow.install.startReady") }));
    });
    await act(async () => {
      vi.advanceTimersByTime(700);
    });
    expect(onComplete).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem(FLOW_COMPLETE_KEY)).toBe("true");
  });

  it("admits an unreadable environment and recovers on retry", async () => {
    const models = vi.fn()
      .mockResolvedValueOnce(fail("down", {}, 503))
      .mockResolvedValue(ok({ recommended: [supportedRow], catalog: [], loaded: [] }));
    const recommendations = vi.fn()
      .mockResolvedValueOnce(fail("down", {}, 503))
      .mockResolvedValue(ok({ profile: {}, recommendations: {} }));
    const onComplete = renderFlow({
      models,
      modelRecommendations: recommendations,
      setupScan: fail("down", {}, 503),
      sysinfo: fail("down", {}, 503),
    });
    await goToLogin();
    await signIn();

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(t("ko", "flow.recommend.unavailable.title"));

    fireEvent.click(screen.getByRole("button", { name: new RegExp(t("ko", "flow.recommend.unavailable.retry")) }));
    await screen.findByRole("heading", { level: 2, name: "Gemma 3 12B" });
    expect(models).toHaveBeenCalledTimes(2);
    expect(recommendations).toHaveBeenLastCalledWith("local_mlx");
    expect(onComplete).not.toHaveBeenCalled();
  });

  it("ignores probe results that land after the flow unmounted", async () => {
    let releaseModels: (value: unknown) => void = () => {};
    const models = vi.fn(() => new Promise((resolve) => { releaseModels = resolve; }));
    const view = renderPage(<ProductFlow onComplete={vi.fn()} />, {
      language: "ko",
      api: { ...readyProbes, models },
    });
    view.unmount();
    await act(async () => {
      releaseModels(ok({ recommended: [supportedRow], catalog: [], loaded: [] }));
    });
    expect(models).toHaveBeenCalledTimes(1);
  });

  it("shows the PC-tailored EmbeddingGemma 2 recommendation card and lets the user prepare it", async () => {
    renderFlow();
    await goToLogin();
    await signIn();

    const embCard = await screen.findByTestId("flow-embedding-recommendation");
    expect(embCard.textContent).toContain("Google EmbeddingGemma 2");
    expect(embCard.textContent).toContain("google/embeddinggemma-2");
    const readiness = screen.getByTestId("flow-embedding-readiness");
    expect(readiness.getAttribute("data-state")).toBe("hash");

    fireEvent.click(screen.getByTestId("flow-select-embedding-model"));
    await screen.findByText(t("ko", "flow.install.title.ready"));
  });

  it("reports active semantic embedding readiness when embeddingsStatus names a semantic provider", async () => {
    renderFlow({
      ...readyProbes,
      embeddingsStatus: ok({
        provider: "huggingface",
        model_id: "huggingface:google/embeddinggemma-2:768",
      }),
    });
    await goToLogin();
    await signIn();

    const readiness = await screen.findByTestId("flow-embedding-readiness");
    expect(readiness.getAttribute("data-state")).toBe("semantic");
    expect(readiness.textContent).toContain("huggingface:google/embeddinggemma-2:768");
  });

  it("falls back to hash readiness badge when embeddingsStatus fails", async () => {
    renderFlow({
      ...readyProbes,
      embeddingsStatus: { ok: false, status: 503, error: "unavailable", data: null },
    });
    await goToLogin();
    await signIn();

    const readiness = await screen.findByTestId("flow-embedding-readiness");
    expect(readiness.getAttribute("data-state")).toBe("hash");
  });
});

describe("brainStateForStep", () => {
  it("maps every step of the ritual onto a Brain state", () => {
    expect(brainStateForStep("wake")).toBe("idle");
    expect(brainStateForStep("analysis")).toBe("listening");
    expect(brainStateForStep("recommend")).toBe("recalling");
    expect(brainStateForStep("install")).toBe("thinking");
    expect(brainStateForStep("login")).toBe("idle");
  });
});

describe("pickInstallModel", () => {
  const chosen: RecommendedModel = { ...fallbackModel(), id: "chosen" };
  const first: RecommendedModel = { ...fallbackModel(), id: "first" };

  it("prefers the explicit selection, then the top recommendation, then the fallback", () => {
    expect(pickInstallModel(chosen, [first]).id).toBe("chosen");
    expect(pickInstallModel(null, [first]).id).toBe("first");
    expect(pickInstallModel(null, []).id).toBe(fallbackModel().id);
  });
});
