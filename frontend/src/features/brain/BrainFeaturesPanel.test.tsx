import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { FeatureCatalog, FeatureToggle } from "@/api/client";
import { t } from "@/i18n";
import { fail, ok, renderPage, type RenderPageOptions } from "@/test/renderPage";
import { BrainFeaturesPanel, patchCatalog } from "./BrainFeaturesPanel";

function toggle(overrides: Partial<FeatureToggle> = {}): FeatureToggle {
  return {
    id: "allow_multimodal",
    kind: "toggle",
    label: "사진·녹음도 기억하기",
    summary: "폴더를 읽을 때 사진과 녹음도 함께 저장합니다.",
    default: false,
    current: false,
    source: "default",
    env_var: "LATTICEAI_ALLOW_MULTIMODAL",
    live: true,
    restart_required: false,
    caution: null,
    parent: null,
    choices: [],
    ...overrides,
  };
}

const CATALOG: FeatureCatalog = {
  note: "모두 지금 바로 적용됩니다.",
  features: [
    toggle(),
    toggle({ id: "video_ingest", label: "영상도 함께", parent: "allow_multimodal", current: true, default: true }),
    toggle({
      id: "brain_network",
      label: "골라서 나누기",
      caution: "이 기능만 기억을 이 컴퓨터 밖으로 내보냅니다.",
    }),
    toggle({
      id: "vault_watch",
      label: "노트 보관함 지켜보기",
      source: "env",
      current: true,
      env_var: "LATTICEAI_VAULT_WATCH",
    }),
    toggle({
      id: "vector_backend",
      kind: "choice",
      label: "의미 검색 방식",
      default: "brute",
      current: "brute",
      env_var: "LATTICEAI_VECTOR_INDEX",
      choices: [
        { id: "brute", label: "전부 비교 (정확)", available: true, detail: null },
        { id: "quantized", label: "간추려 비교 (빠름)", available: true, detail: null },
        { id: "hnsw", label: "근사 검색", available: false, detail: "설치 필요 — hnswlib 없음" },
      ],
    }),
  ],
};

function renderPanel(
  api: NonNullable<RenderPageOptions["api"]> = {},
  language: "ko" | "en" = "ko",
) {
  return renderPage(<BrainFeaturesPanel language={language} />, {
    language,
    api: { features: () => Promise.resolve(ok(CATALOG)), ...api },
  });
}

const row = (id: string) => screen.getByTestId(`feature-row-${id}`);
const sw = (id: string) => screen.getByTestId(`feature-switch-${id}`);
/** The panel itself, whose `aria-busy` is the in-flight guard made observable. */
const panel = () => screen.getByRole("region", { name: t("ko", "brain.features.aria") });

describe("BrainFeaturesPanel rendering", () => {
  it("renders exactly the switches the server sent, in the server's order", async () => {
    renderPanel();
    await screen.findByTestId("feature-row-allow_multimodal");

    const ids = Array.from(document.querySelectorAll(".brain-feature-row")).map(
      (node) => node.getAttribute("data-testid"),
    );
    expect(ids).toEqual(CATALOG.features.map((feature) => `feature-row-${feature.id}`));
    expect(screen.getByText(CATALOG.note)).toBeTruthy();
    expect(sw("allow_multimodal")).toHaveAttribute("aria-checked", "false");
    expect(sw("video_ingest")).toHaveAttribute("aria-checked", "true");
  });

  it("names the environment as the source when nobody has touched a switch", async () => {
    renderPanel();
    await screen.findByTestId("feature-row-vault_watch");

    const badge = row("vault_watch").querySelector(".brain-feature-source");
    expect(badge?.textContent).toBe(t("ko", "brain.features.source.env"));
    expect(badge?.getAttribute("title")).toContain("LATTICEAI_VAULT_WATCH");
    // A default-sourced switch claims nothing about where its value came from.
    expect(row("allow_multimodal").querySelector(".brain-feature-source")).toBeNull();
  });

  it("keeps the sharing caution next to the switch it belongs to", async () => {
    renderPanel();
    await screen.findByTestId("feature-row-brain_network");

    expect(row("brain_network").querySelector(".brain-feature-caution")?.textContent).toContain(
      "이 컴퓨터 밖으로",
    );
    expect(row("allow_multimodal").querySelector(".brain-feature-caution")).toBeNull();
  });

  it("marks a sub-switch dormant while its parent is off, and stops once it is on", async () => {
    const { unmount } = renderPanel();
    await screen.findByTestId("feature-row-video_ingest");
    expect(row("video_ingest").className).toContain("is-child");
    expect(row("video_ingest").textContent).toContain(t("ko", "brain.features.dormant"));
    unmount();

    const parentOn = {
      ...CATALOG,
      features: CATALOG.features.map((feature) =>
        feature.id === "allow_multimodal" ? { ...feature, current: true } : feature,
      ),
    };
    renderPanel({ features: () => Promise.resolve(ok(parentOn)) });
    await screen.findByTestId("feature-row-video_ingest");
    expect(row("video_ingest").textContent).not.toContain(t("ko", "brain.features.dormant"));
  });

  it("shows an uninstallable option disabled, with its reason, rather than hiding it", async () => {
    renderPanel();
    await screen.findByTestId("feature-choices-vector_backend");

    const hnsw = screen.getByTestId("feature-choice-vector_backend-hnsw");
    expect(hnsw).toBeDisabled();
    expect(hnsw.getAttribute("title")).toBe("설치 필요 — hnswlib 없음");
    expect(hnsw.textContent).toContain(t("ko", "brain.features.installRequired"));
    expect(screen.getByTestId("feature-choice-vector_backend-brute")).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  it("speaks the language it was given", async () => {
    renderPanel({}, "en");
    await screen.findByTestId("feature-row-allow_multimodal");

    expect(sw("allow_multimodal").textContent).toContain(t("en", "brain.features.off"));
  });
});

describe("BrainFeaturesPanel states", () => {
  it("says it is loading before the catalog lands", () => {
    renderPanel({ features: () => new Promise(() => {}) });

    expect(screen.getByText(t("ko", "ui.loading"))).toBeTruthy();
    expect(screen.queryByTestId("feature-row-allow_multimodal")).toBeNull();
  });

  it("renders an empty state instead of a panel of switches that all look off", async () => {
    renderPanel({
      features: () => Promise.resolve(fail("서버에 연결할 수 없습니다", { features: [], note: "" })),
    });

    expect(await screen.findByText(t("ko", "brain.features.empty"))).toBeTruthy();
    expect(screen.getByText("서버에 연결할 수 없습니다")).toBeTruthy();
    expect(screen.queryByRole("switch")).toBeNull();
  });
});

describe("BrainFeaturesPanel writes", () => {
  it("moves the switch immediately and confirms once the server agrees", async () => {
    // The write is held open on purpose: the point of an optimistic update is
    // that the switch has already moved *while the request is still in flight*.
    let settle = (_result: unknown) => {};
    const pending = new Promise((resolve) => {
      settle = resolve;
    });
    const setFeature = vi.fn(() => pending);
    const written = { ...toggle(), current: true, source: "user" };
    renderPanel({
      setFeature,
      // The refetch after a successful write sees the server's new answer, so
      // the switch has to stay moved rather than snapping back.
      features: () =>
        Promise.resolve(
          ok(setFeature.mock.calls.length
            ? { ...CATALOG, features: [written, ...CATALOG.features.slice(1)] }
            : CATALOG),
        ),
    });
    await screen.findByTestId("feature-switch-allow_multimodal");

    fireEvent.click(sw("allow_multimodal"));

    await waitFor(() => expect(sw("allow_multimodal")).toHaveAttribute("aria-checked", "true"));
    expect(setFeature).toHaveBeenCalledWith("allow_multimodal", true);

    settle(ok(written));

    expect(await screen.findByText(t("ko", "brain.features.saved"))).toBeTruthy();
    expect(sw("allow_multimodal")).toHaveAttribute("aria-checked", "true");
  });

  it("puts the switch back and says so when the server refuses", async () => {
    renderPanel({
      setFeature: () => Promise.resolve(fail("그런 기능은 없습니다", {} as FeatureToggle, 400)),
    });
    await screen.findByTestId("feature-switch-brain_network");

    fireEvent.click(sw("brain_network"));
    fireEvent.click(screen.getByTestId("feature-preview-ack-brain_network"));
    fireEvent.click(screen.getByTestId("feature-preview-confirm-brain_network"));

    expect(await screen.findByText("그런 기능은 없습니다")).toBeTruthy();
    await waitFor(() => expect(sw("brain_network")).toHaveAttribute("aria-checked", "false"));
  });

  it("still explains a refusal that arrived without a reason", async () => {
    renderPanel({
      setFeature: () =>
        Promise.resolve({ ok: false, status: 400, source: "live", data: {} as FeatureToggle }),
    });
    await screen.findByTestId("feature-switch-brain_network");

    fireEvent.click(sw("brain_network"));
    fireEvent.click(screen.getByTestId("feature-preview-ack-brain_network"));
    fireEvent.click(screen.getByTestId("feature-preview-confirm-brain_network"));

    expect(await screen.findByText(t("ko", "brain.features.failed"))).toBeTruthy();
    await waitFor(() => expect(sw("brain_network")).toHaveAttribute("aria-checked", "false"));
  });

  it("puts the switch back when the request never lands at all", async () => {
    renderPanel({ setFeature: () => Promise.reject(new Error("offline")) });
    await screen.findByTestId("feature-switch-brain_network");

    fireEvent.click(sw("brain_network"));
    fireEvent.click(screen.getByTestId("feature-preview-ack-brain_network"));
    fireEvent.click(screen.getByTestId("feature-preview-confirm-brain_network"));

    expect(await screen.findByText(t("ko", "brain.features.failed"))).toBeTruthy();
    expect(sw("brain_network")).toHaveAttribute("aria-checked", "false");
  });

  it("sends the option id for a choice, and never sends a disabled one", async () => {
    const setFeature = vi.fn(() => Promise.resolve(ok(toggle({ id: "vector_backend" }))));
    renderPanel({ setFeature });
    await screen.findByTestId("feature-choices-vector_backend");

    fireEvent.click(screen.getByTestId("feature-choice-vector_backend-quantized"));
    await waitFor(() =>
      expect(setFeature).toHaveBeenCalledWith("vector_backend", "quantized"),
    );

    fireEvent.click(screen.getByTestId("feature-choice-vector_backend-hnsw"));
    expect(setFeature).toHaveBeenCalledTimes(1);
  });

  it("ignores a second move while one is still in flight, without going numb", async () => {
    // The guard is a click check, not `disabled`: disabling the button blurs
    // it, and the drawer's Escape handler lives on the drawer node — a blurred
    // switch means Escape stops closing the panel.
    //
    // Both halves wait on `aria-busy`, never on a call count or a bare await.
    // `aria-busy` *is* the guard's own state rendered, so "armed" and
    // "disarmed" are observable facts here rather than assumptions about when
    // a resolved promise has flushed through React — which is exactly the
    // timing this test used to guess at, and lose on a loaded CI box.
    let settle = (_result: unknown) => {};
    const setFeature = vi.fn(
      () =>
        new Promise((resolve) => {
          settle = resolve;
        }),
    );
    renderPanel({ setFeature });
    await screen.findByTestId("feature-switch-allow_multimodal");

    // Open the risk preview first so we can try to confirm it while busy.
    fireEvent.click(sw("brain_network"));
    expect(screen.getByTestId("feature-preview-brain_network")).toBeTruthy();

    fireEvent.click(sw("allow_multimodal"));
    await waitFor(() => expect(panel()).toHaveAttribute("aria-busy", "true"));
    expect(setFeature).toHaveBeenCalledTimes(1);

    // Armed: a second move — on either kind of control — is dropped. The
    // `act` flush is what makes the *negative* assertion real: react-query
    // reaches `mutationFn` a few microtasks after the click, so draining them
    // is the difference between "was ignored" and "had not started yet".
    fireEvent.click(sw("vault_watch"));
    fireEvent.click(screen.getByTestId("feature-choice-vector_backend-quantized"));
    fireEvent.click(screen.getByTestId("feature-preview-ack-brain_network"));
    fireEvent.click(screen.getByTestId("feature-preview-confirm-brain_network"));
    await act(async () => {});
    expect(setFeature).toHaveBeenCalledTimes(1);
    // Not even optimistically: `onMutate` runs before the request, so a switch
    // that moved here would prove the click got through.
    expect(sw("brain_network")).toHaveAttribute("aria-checked", "false");
    expect(screen.getByTestId("feature-choice-vector_backend-brute")).toHaveAttribute(
      "aria-checked",
      "true",
    );
    // …but nothing went numb: the switch is still enabled and still focusable,
    // which is what keeps Escape closing the drawer it lives in.
    expect(sw("allow_multimodal")).not.toBeDisabled();
    sw("allow_multimodal").focus();
    expect(document.activeElement).toBe(sw("allow_multimodal"));

    settle(ok(toggle({ current: true })));

    // Disarmed: the next move is accepted. Risk-carrying switches still open
    // the preview first — confirming it is what sends the write.
    await waitFor(() => expect(panel()).toHaveAttribute("aria-busy", "false"));
    fireEvent.click(sw("brain_network"));
    fireEvent.click(screen.getByTestId("feature-preview-ack-brain_network"));
    fireEvent.click(screen.getByTestId("feature-preview-confirm-brain_network"));
    await waitFor(() => expect(setFeature).toHaveBeenCalledTimes(2));
  });

  it("shows a catalog preview and requires an ack before enabling a risk-carrying switch", async () => {
    const setFeature = vi.fn(() => Promise.resolve(ok(toggle({ id: "brain_network", current: true }))));
    renderPanel({ setFeature });
    await screen.findByTestId("feature-switch-brain_network");

    fireEvent.click(sw("brain_network"));
    expect(setFeature).not.toHaveBeenCalled();
    expect(screen.getByTestId("feature-preview-brain_network").textContent).toContain("켜면 이렇게 달라져요");
    expect(sw("brain_network")).toHaveAttribute("aria-checked", "false");

    fireEvent.click(screen.getByTestId("feature-preview-confirm-brain_network"));
    expect(setFeature).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("feature-preview-ack-brain_network"));
    fireEvent.click(screen.getByTestId("feature-preview-confirm-brain_network"));
    await waitFor(() => expect(setFeature).toHaveBeenCalledWith("brain_network", true));
    await waitFor(() => expect(document.activeElement).toBe(sw("brain_network")));
  });

  it("cancels a pending enable without writing", async () => {
    const setFeature = vi.fn(() => Promise.resolve(ok(toggle({ id: "brain_network" }))));
    renderPanel({ setFeature });
    await screen.findByTestId("feature-switch-brain_network");

    fireEvent.click(sw("brain_network"));
    fireEvent.click(screen.getByTestId("feature-preview-cancel-brain_network"));
    expect(screen.queryByTestId("feature-preview-brain_network")).toBeNull();
    expect(setFeature).not.toHaveBeenCalled();
  });

  it("turns a risk-carrying switch off without a preview", async () => {
    const setFeature = vi.fn(() => Promise.resolve(ok(toggle({ id: "brain_network", current: false }))));
    const onCatalog = {
      ...CATALOG,
      features: CATALOG.features.map((feature) =>
        feature.id === "brain_network" ? { ...feature, current: true } : feature,
      ),
    };
    renderPanel({
      features: () => Promise.resolve(ok(onCatalog)),
      setFeature,
    });
    await screen.findByTestId("feature-switch-brain_network");

    fireEvent.click(sw("brain_network"));
    await waitFor(() => expect(setFeature).toHaveBeenCalledWith("brain_network", false));
    expect(screen.queryByTestId("feature-preview-brain_network")).toBeNull();
  });

  it("turns an on switch back off", async () => {
    const setFeature = vi.fn(() => Promise.resolve(ok(toggle({ id: "video_ingest" }))));
    renderPanel({ setFeature });
    await screen.findByTestId("feature-switch-video_ingest");

    fireEvent.click(sw("video_ingest"));

    await waitFor(() => expect(setFeature).toHaveBeenCalledWith("video_ingest", false));
  });
});

describe("patchCatalog", () => {
  it("moves one feature and marks the change as this person's", () => {
    const patched = patchCatalog(ok(CATALOG), "allow_multimodal", true) as {
      data: FeatureCatalog;
    };

    expect(patched.data.features[0].current).toBe(true);
    expect(patched.data.features[0].source).toBe("user");
    // Nothing else moved.
    expect(patched.data.features[1]).toEqual(CATALOG.features[1]);
  });

  it("refuses to invent a catalog over a failed read", () => {
    const failed = fail("down", { features: [], note: "" });

    expect(patchCatalog(failed, "allow_multimodal", true)).toBe(failed);
    expect(patchCatalog(undefined, "allow_multimodal", true)).toBeUndefined();
    expect(patchCatalog({ ok: true }, "allow_multimodal", true)).toEqual({ ok: true });
  });
});
