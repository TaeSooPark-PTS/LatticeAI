import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { t } from "@/i18n";
import { fail, ok, renderPage } from "@/test/renderPage";
import { describeComputer, embeddingStateLabel, isUntranslatedProse, LibraryPage } from "./Library";

/**
 * The model library. The thing this screen must never do is imply a model is
 * usable when it is not — the ordering, the state labels and the embedding
 * banner all exist to keep "you can click this" and "this will work" aligned.
 */

const MODELS = {
  models: [
    { id: "mlx-community/gemma-4-26b-a4b-it-4bit", name: "Gemma 4 26B", state: "loaded", engine: "local_mlx" },
    { id: "mlx-community/qwen-3-8b-4bit", name: "Qwen 3 8B", state: "downloaded", engine: "local_mlx" },
    { id: "openai/gpt-4o-mini", name: "GPT-4o mini", state: "available", engine: "openai" },
  ],
  current: "mlx-community/gemma-4-26b-a4b-it-4bit",
};

function render(overrides = {}, options = {}) {
  return renderPage(<LibraryPage />, {
    api: {
      models: ok(MODELS),
      modelRecommendations: ok({ recommendations: [] }),
      embeddingsStatus: ok({ grade: "ok", provider: "local", dim: 384, ready: true }),
      skills: ok({ skills: [] }),
      pluginsRegistry: ok({ plugins: [] }),
      mcpTools: ok({ tools: [] }),
      ...overrides,
    },
    ...options,
  });
}

describe("LibraryPage", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("renders the model surface", async () => {
    render();
    await waitFor(() => expect(document.body.textContent).toMatch(/AI 모델|모델/));
  });

  it("never prints a raw registry coordinate as a model name", async () => {
    // 10.0.0 replaced ids with readable names across the Brain surface; this
    // panel used to fall straight through to the coordinate.
    render();
    await waitFor(() => expect(document.body.textContent).toBeTruthy());
    expect(document.body.textContent).not.toMatch(/mlx-community\//);
  });

  it("reports an unavailable model list instead of an empty catalogue", async () => {
    render({ models: fail("server unavailable", { models: [] }) });
    await waitFor(() =>
      expect(document.body.textContent).toMatch(/요청을 처리하지 못했어요|사용할 수 없|unavailable/i));
  });

  it("says plainly when no model is installed rather than showing a blank list", async () => {
    render({ models: ok({ models: [], current: null }) });
    await waitFor(() => expect(document.body.textContent).toBeTruthy());
    expect(document.body.textContent).not.toMatch(/undefined|NaN|\[object Object\]/);
  });

  it("leads the embedding panel with plain language, not a grade code", async () => {
    render({ embeddingsStatus: ok({ grade: "fallback", provider: "hash", dim: 256, ready: false }) });
    await waitFor(() => expect(document.body.textContent).toBeTruthy());
    // "Grade: fallback" is developer output; the user needs the consequence.
    const text = document.body.textContent || "";
    if (text.includes("fallback")) {
      expect(text).toMatch(/검색|의미|search|semantic/i);
    }
  });

  it("renders in English when the language is en", async () => {
    render({}, { language: "en" });
    await waitFor(() => expect(document.body.textContent).toBeTruthy());
    expect(document.body.textContent).not.toMatch(/모델 고르기|불러온 모델/);
  });

  it("does not translate a server sentence one word at a time", async () => {
    // The registry ships compatibility prose in English. Swapping terms inside
    // it put "uses the 이 로컬 모델 형식 로컬 모델 지원 format. The installed
    // 로컬 모델 지원 모델 지원 does not include that loader…" into a released
    // README screenshot — readable in neither language.
    render({
      models: ok({
        catalog: [{
          id: "mlx-community/gemma-4-12b-it",
          name: "Gemma 4 12B",
          load_status: "unsupported",
          runtime_compatibility: {
            supported: false,
            user_message: "Gemma 4 12B uses the gemma4_unified MLX format. The installed MLX-VLM runtime does not include that loader, so this local model cannot load until MLX-VLM is updated.",
          },
        }],
        loaded: [],
        current: null,
      }),
    });
    // Wait for the card itself: an unresolved panel would pass the negative
    // assertions below without ever rendering the message under test.
    await waitFor(() => expect(document.body.textContent).toMatch(/Gemma 4 12B/));
    const text = document.body.textContent || "";
    // Neither the raw English sentence nor a half-substituted hybrid survives.
    expect(text).not.toMatch(/does not include that loader/);
    expect(text).not.toMatch(/The installed/);
    expect(text).toMatch(/이 컴퓨터에서 실행할 수 없어요/);
  });

  it("survives a model entry missing half its fields", async () => {
    // Real registries return partial rows; a missing name must not blank the row.
    render({ models: ok({ models: [{ id: "x/y" }], current: null }) });
    await waitFor(() => expect(document.body.textContent).toBeTruthy());
    expect(document.body.textContent).not.toMatch(/undefined/);
  });

  /**
   * "Which model is running, and can I change it?" is the question that brings
   * people to this screen. It used to be answered by a stat cell partway down a
   * catalogue, below a hero, a tab strip and a six-step setup track. It is the
   * first block on the page now — and the alternatives it offers have to be
   * ones a single click can really switch to, or the card's primary action is a
   * button that always fails.
   */
  it("names the running model first, without printing its registry coordinate", async () => {
    render();
    const card = () => screen.getByTestId("library-active-model");
    await waitFor(() => expect(card().textContent).toContain("Gemma 4 26B"));
    expect(card().textContent).toContain("지금 작동 중인 모델");
    expect(card().textContent).not.toMatch(/mlx-community\//);
  });

  it("says plainly that nothing is loaded rather than leaving the card blank", async () => {
    render({ models: ok({ catalog: [], loaded: [], current: null }) });
    await waitFor(() =>
      expect(screen.getByTestId("library-active-model").textContent).toContain("아직 사용할 모델이 없어요"));
  });

  it("a failed request does not become a claim that no model is running", async () => {
    // The response cannot support that claim. "Could not check" is what is true.
    render({ models: fail("server unavailable", {}) });
    const card = () => screen.getByTestId("library-active-model");
    await waitFor(() => expect(card().textContent).toContain("확인하지 못했어요"));
    expect(card().textContent).not.toContain("아직 사용할 모델이 없어요");
  });

  it("only offers a one-click switch to models that can actually load", async () => {
    render({
      models: ok({
        catalog: [
          { id: "local/running", name: "Running One", load_available: true },
          { id: "local/ready", name: "Ready One", load_available: true },
          // On disk, but the installed runtime has no loader for it.
          { id: "local/broken", name: "Broken One", pulled: true, load_available: false, runtime_compatibility: { supported: false } },
          // Would need a download, which is consent-gated in the catalogue below.
          { id: "local/remote", name: "Remote One", download_required: true, load_available: true },
        ],
        loaded: ["local/running"],
        current: "local/running",
      }),
    });
    const card = () => screen.getByTestId("library-active-model");
    await waitFor(() => expect(card().textContent).toContain("Ready One"));
    expect(card().textContent).not.toContain("Broken One");
    expect(card().textContent).not.toContain("Remote One");
  });
});

/**
 * Everything below drives the four panels this screen switches between, and
 * the model catalogue's two hardest promises: a button that is offered must be
 * a button that can work, and no server enum, registry coordinate or English
 * sentence may reach a Korean reader unchanged.
 */

describe("library copy helpers", () => {
  it("names what search can do, not which provider is wired in", () => {
    expect(embeddingStateLabel("production", "ko")).toBe(t("ko", "library.embedding.state.production"));
    for (const state of ["fallback", "hash", "local", "LOCAL"]) {
      expect(embeddingStateLabel(state, "ko")).toBe(t("ko", "library.embedding.state.fallback"));
    }
    expect(embeddingStateLabel(undefined, "ko")).toBe(t("ko", "library.embedding.state.unknown"));
  });

  it("names the machine the way its owner would, not the way uname does", () => {
    expect(describeComputer(undefined, "ko")).toBe(t("ko", "library.value.detected"));
    expect(describeComputer({ os: "Darwin", arch: "arm64" }, "ko")).toBe(t("ko", "library.runtime.appleSilicon"));
    expect(describeComputer({ os: "darwin", arch: "x86_64" }, "ko")).toBe("Mac");
    expect(describeComputer({ os: "win32" }, "ko")).toBe("Windows");
    expect(describeComputer({ os: "windows_nt" }, "ko")).toBe("Windows");
    expect(describeComputer({ os: "linux" }, "ko")).toBe("Linux");
    expect(describeComputer({ os: "freebsd" }, "ko")).toBe(t("ko", "library.runtime.thisComputer"));
  });

  it("tells a model name from an English sentence nobody translated", () => {
    // A name may legitimately be Latin script; a six-word English sentence
    // with no Hangul in it is copy that was never translated.
    expect(isUntranslatedProse("Gemma 4 26B Instruct")).toBe(false);
    expect(isUntranslatedProse("이 모델은 이 컴퓨터에서 실행할 수 없습니다 아마도")).toBe(false);
    expect(isUntranslatedProse("This model uses a format the runtime cannot load")).toBe(true);
  });
});

function renderLibrary(
  overrides: Record<string, unknown> = {},
  options: { language?: "ko" | "en"; mode?: "basic" | "advanced" | "admin" } = {},
  initialTab?: string,
) {
  return renderPage(<LibraryPage initialTab={initialTab} />, { api: overrides, ...options });
}

const body = () => document.body.textContent || "";

describe("LibraryPage tabs", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("offers only the model catalogue in everyday mode", async () => {
    renderLibrary({}, { mode: "basic" });
    await screen.findByRole("tab", { name: t("ko", "library.tab.models") });
    expect(screen.getAllByRole("tab")).toHaveLength(1);
  });

  it("switches panel and address bar together", async () => {
    renderLibrary();
    await userEvent.click(await screen.findByRole("tab", { name: t("ko", "library.tab.skills") }));
    expect(window.location.hash).toBe("#/skills");
    expect(await screen.findByText(t("ko", "library.skills.installed"))).toBeTruthy();

    await userEvent.click(screen.getByRole("tab", { name: t("ko", "library.tab.marketplace") }));
    expect(await screen.findByText(t("ko", "library.market.templates"))).toBeTruthy();
  });

  it("opens on the tab the hash named, and ignores one that is not a tab", async () => {
    const mcp = renderLibrary({}, {}, "mcp");
    expect(await screen.findByText(t("ko", "library.connector.mcpTools"))).toBeTruthy();
    mcp.unmount();

    // The tab strip still names every screen, so a hash nobody recognises
    // leaves the reader with a way forward rather than a dead end.
    renderLibrary({}, {}, "not-a-tab");
    expect(await screen.findByRole("tab", { name: t("ko", "library.tab.models") })).toBeTruthy();
    expect(screen.queryByText(t("ko", "library.models.setup.title"))).toBeNull();
  });
});

describe("switching the running model", () => {
  beforeEach(() => vi.restoreAllMocks());

  const SWITCHABLE = {
    catalog: [
      { id: "local/running", name: "Running One", load_available: true },
      {
        model_id: "local/spare",
        recommended_load_id: "local/spare@v2",
        recommended_engine: "local_mlx",
        load_status: "ready",
      },
      // Nothing but a name: the registry ships rows like this and they must
      // not become a button that loads "undefined".
      { name: "Anonymous" },
      { id: "local/bare", name: "Bare One", load_available: true },
      { id: "local/refused", name: "Refused One", load_available: true, load_status: "unsupported" },
    ],
    loaded: ["local/running"],
    current: "local/running",
  };

  it("switches with the load id and engine the registry asked for", async () => {
    const loadModel = vi.fn(() => Promise.resolve(ok({})));
    renderLibrary({ models: ok(SWITCHABLE), loadModel });

    // Named from its coordinate, because the registry gave it no name.
    const button = await screen.findByTestId("library-switch-local/spare");
    expect(button.textContent).toContain("Spare");
    await userEvent.click(button);

    await waitFor(() => expect(loadModel).toHaveBeenCalledWith("local/spare@v2", "local_mlx", false));
  });

  it("switches on the row's own id when the registry named no engine", async () => {
    const loadModel = vi.fn(() => Promise.resolve(ok({})));
    renderLibrary({ models: ok(SWITCHABLE), loadModel });

    await userEvent.click(await screen.findByTestId("library-switch-local/bare"));

    // No engine at all is the honest answer, not a guessed one.
    await waitFor(() => expect(loadModel).toHaveBeenCalledWith("local/bare", undefined, false));
  });

  it("never offers a model the runtime has refused, or one with no id", async () => {
    renderLibrary({ models: ok(SWITCHABLE) });
    await screen.findByTestId("library-switch-local/spare");
    expect(screen.queryByTestId("library-switch-local/refused")).toBeNull();
    expect(screen.getByTestId("library-active-model").textContent).not.toContain("Anonymous");
  });

  it("says the switch is running and then says why it failed", async () => {
    let release!: (value: unknown) => void;
    const gate = new Promise((resolve) => { release = resolve; });
    const loadModel = vi.fn(() => gate);
    renderLibrary({ models: ok(SWITCHABLE), loadModel });

    await userEvent.click(await screen.findByTestId("library-switch-local/spare"));
    expect(screen.getByTestId("library-switch-local/spare").textContent)
      .toContain(t("ko", "library.active.switching"));

    await act(async () => { release(fail("메모리 부족", {})); });
    expect(await screen.findByText("메모리 부족")).toBeTruthy();
  });

  it("falls back to its own words when the failure carried none", async () => {
    renderLibrary({
      models: ok(SWITCHABLE),
      loadModel: vi.fn(() => Promise.resolve({ ok: false, status: 500, source: "live", data: {} })),
    });
    await userEvent.click(await screen.findByTestId("library-switch-local/spare"));
    expect(await screen.findByText(t("ko", "library.active.switchFailed"))).toBeTruthy();
  });

  it("names a running model the catalogue has never heard of", async () => {
    renderLibrary({ models: ok({ catalog: [], loaded: [], current: "mlx-community/qwen-3-8b-4bit" }) });
    await waitFor(() =>
      expect(screen.getByTestId("library-active-model").textContent).toContain("Qwen 3 8b"));
  });
});

describe("the model catalogue", () => {
  beforeEach(() => vi.restoreAllMocks());

  const CATALOG = {
    catalog: [
      { id: "local/loaded", name: "Loaded One", load_available: true },
      {
        id: "local/ready",
        name: "Ready One",
        load_status: "ready",
        recommended_load_id: "local/ready@v2",
        recommended_engine: "local_mlx",
        load_strategy: "direct",
        modality: "multimodal",
        hardware: { recommended_ram_gb: 16, notes: "Tiny but capable vision." },
        verification: { verified: true, notes: "config and tokenizer present" },
        provider: "Maker",
        license: "MIT",
        safety_notes: "주의 사항",
        size: "8GB",
        family: "Gemma",
      },
      { id: "local/pulled", name: "Pulled One", pulled: true, safety_notes: "주의만" },
      {
        id: "local/remote",
        name: "Remote One",
        download_required: true,
        download_size: "4GB",
        license: "Apache-2.0",
        unavailable_reason: "Model files are not present locally.",
      },
    ],
    recommended: [
      {
        id: "local/broken",
        name: "Broken One",
        load_status: "unsupported",
        runtime_compatibility: {
          supported: false,
          action: "Update the runtime",
          user_message: "Gemma 4 12B uses the gemma4_unified MLX format and cannot load here yet.",
          alternatives: [{ id: "alt-1", name: "MLX Studio" }, { name: "Plain Name" }],
        },
      },
      {
        model_id: "local/fallback",
        name: "Fallback One",
        runtime_compatibility: { status: "fallback_available", alternatives: [] },
      },
      { name: "Nameless One" },
      {},
    ],
    loaded: ["local/loaded"],
    compat_profiles: [
      { model_id: "local/loaded", quality_status: "ok" },
      { display_name: "Second Profile" },
    ],
  };

  const RECS = {
    profile: { os: "darwin", arch: "arm64" },
    recommendations: {
      top_pick: { id: "local/ready" },
      models: [{
        id: "local/ready",
        family: "Gemma",
        hardware: { recommended_ram_gb: 24, notes: "registry note" },
        verification: { verified: true },
        provider: "Registry Maker",
        organization: "Org",
        size: "9GB",
        license: "Apache-2.0",
        safety_notes: "레지스트리 주의",
        modality: "multimodal",
        load_strategy: "registry",
        download_size: "5GB",
        runtime_compatibility: {},
      }],
    },
  };

  it("puts what can be used first and shows every row's real state", async () => {
    renderLibrary({ models: ok(CATALOG), modelRecommendations: ok(RECS) });

    await screen.findAllByText("Ready One");
    // Loaded first, then what one click can use, then what is merely on disk —
    // the catalogue used to arrive in recommendation order, which buried the
    // one usable model behind five rows whose only button was disabled.
    const names = Array.from(document.querySelectorAll(".text-base.font-semibold"))
      .map((node) => node.textContent)
      .filter((name) => name?.endsWith("One"));
    expect(names).toEqual([
      "Loaded One", "Ready One", "Pulled One", "Remote One", "Broken One", "Fallback One", "Nameless One",
    ]);

    // The registry's own compatibility prose reaches the reader only through
    // the localized sentence for the situation.
    expect(body()).toContain(t("ko", "library.model.status.unsupported"));
    expect(body()).toContain(t("ko", "library.model.runtimeFallback"));
    expect(body()).toContain(t("ko", "library.model.downloadSize", { size: "4GB" }));
    expect(body()).toContain(t("ko", "library.model.ramRecommended", { ram: "16" }));
    expect(body()).toContain(t("ko", "library.model.recommended"));
    expect(body()).toContain(t("ko", "library.model.multimodal"));
    expect(body()).toContain(t("ko", "library.model.sourceVerified"));
    expect(body()).toContain(t("ko", "library.model.license", { license: "MIT" }));
    // A row with only one of the two still shows the one it has.
    expect(body()).toContain("주의만");
    expect(body()).toContain(t("ko", "library.model.license", { license: "Apache-2.0" }));
    // Alternatives keep their real names for someone who can act on them.
    expect(body()).toContain("MLX Studio");
  });

  it("shows a short, plainly worded list in everyday mode", async () => {
    renderLibrary({ models: ok(CATALOG), modelRecommendations: ok(RECS) }, { mode: "basic" });

    await screen.findAllByText("Loaded One");
    expect(screen.queryByText("Remote One")).toBeNull();
    expect(body()).toContain(t("ko", "library.model.shortListHint"));
    // Registry engineering commentary and licence lines stay in advanced mode.
    expect(body()).not.toContain("Tiny but capable vision.");
    expect(body()).not.toContain("MIT");
  });

  it("says a refused model needs attention without naming a runtime", async () => {
    renderLibrary({
      models: ok({
        catalog: [{
          id: "local/broken",
          name: "Broken One",
          load_status: "unsupported",
          // A size that is really a packaging format, and a hardware block
          // with no RAM figure — neither is copy for an everyday reader.
          size: "4GB gguf",
          hardware: { notes: "Tiny but capable vision." },
          runtime_compatibility: {
            supported: false,
            action: "Update the runtime",
            user_message: "gemma4_unified runtime",
            alternatives: [{ id: "alt-1", name: "MLX Studio" }, { name: "Plain Name" }, { id: "plain-alt" }],
          },
        }, {
          id: "local/fallback",
          name: "Fallback One",
          runtime_compatibility: { status: "fallback_available", alternatives: [] },
        }],
        loaded: [],
      }),
    }, { mode: "basic" });

    await screen.findByText("Broken One");
    expect(body()).toContain(t("ko", "library.model.needsAttention"));
    expect(body()).toContain(t("ko", "library.model.attentionBeforeLoad"));
    expect(body()).toContain(t("ko", "library.model.compatiblePath"));
    // "MLX Studio" is a runtime name; in everyday mode it says what it is for.
    expect(body()).toContain(t("ko", "library.model.compatibleAlternative"));
    expect(body()).toContain("Plain Name");
    expect(body()).toContain("plain-alt");
    expect(body()).not.toContain("Update the runtime");
    expect(body()).not.toContain("gguf");
    expect(body()).not.toContain("Tiny but capable vision.");
  });

  it("rewrites the registry's format jargon instead of printing it", async () => {
    const jargon = {
      catalog: [{ id: "x/y", name: "Jargon One", unavailable_reason: "needs gemma4_unified runtime" }],
      loaded: [],
    };

    const korean = renderLibrary({ models: ok(jargon) }, { mode: "basic" });
    await screen.findByText("Jargon One");
    expect(body()).not.toContain("gemma4_unified");
    expect(body()).toContain(t("ko", "library.model.localFormat"));
    korean.unmount();

    renderLibrary({ models: ok(jargon) }, { mode: "basic", language: "en" });
    await screen.findByText("Jargon One");
    expect(body()).not.toContain("gemma4_unified");
    expect(body()).toContain(t("en", "library.model.localFormat"));
  });

  it("prefers the registry's recommendation when the catalogue row is bare", async () => {
    renderLibrary({
      models: ok({ catalog: [{ id: "local/ready" }], loaded: [] }),
      modelRecommendations: ok(RECS),
    });
    await screen.findByText("local/ready");
    expect(body()).toContain(t("ko", "library.model.ramRecommended", { ram: "24" }));
    expect(body()).toContain("Registry Maker");
    expect(body()).toContain(t("ko", "library.model.license", { license: "Apache-2.0" }));
  });

  it("keeps the download button shut until consent is given", async () => {
    renderLibrary({ models: ok(CATALOG), modelRecommendations: ok(RECS) });
    await screen.findByText("Remote One");

    const install = screen.getByRole("button", { name: t("ko", "library.btn.installLoad") });
    expect(install.hasAttribute("disabled")).toBe(true);
    expect(install.getAttribute("title")).toBe(t("ko", "library.btn.needsConsent"));

    await userEvent.click(screen.getByRole("checkbox"));

    expect(install.hasAttribute("disabled")).toBe(false);
  });

  it("explains a button it will not let you press", async () => {
    renderLibrary({
      models: ok({ catalog: [{ id: "local/nope", name: "Nope One" }, { id: "local/broken", name: "Broken One", load_status: "unsupported" }], loaded: [] }),
    });
    await screen.findByText("Nope One");
    const buttons = screen.getAllByRole("button", { name: t("ko", "library.btn.validateLoad") });
    expect(buttons[0].getAttribute("title")).toBe(t("ko", "library.model.notReady"));
    expect(buttons[1].getAttribute("title")).toBe(t("ko", "library.model.status.unsupported"));
  });

  it("lists the profiles that were checked, and says so when none were", async () => {
    const withProfiles = renderLibrary({ models: ok(CATALOG) });
    await screen.findByText("local/loaded");
    expect(body()).toContain("Second Profile");
    withProfiles.unmount();

    // In everyday mode a profile with no name of its own is still named for a
    // person, and its raw coordinate never reaches the screen.
    const basic = renderLibrary({ models: ok({ compat_profiles: [{ model_id: "hidden/id" }] }) }, { mode: "basic" });
    await screen.findByText(t("ko", "library.model.checked"));
    expect(body()).not.toContain("hidden/id");
    basic.unmount();

    renderLibrary({ models: ok({ catalog: [], loaded: [] }) });
    expect(await screen.findByText(t("ko", "library.model.noneChecked"))).toBeTruthy();
  });

  it("falls back to a neutral status phrase for a status code it doesn't recognise", async () => {
    // The registry's `load_status` enum grows over time; an unmapped value
    // must read as neutral copy, not leak the raw server token onto the badge.
    renderLibrary({
      models: ok({ catalog: [{ id: "local/mystery", name: "Mystery One", load_status: "quarantined" }], loaded: [] }),
    });
    await screen.findByText("Mystery One");
    expect(body()).toContain(t("ko", "library.model.status.unknown"));
    expect(body()).not.toContain("quarantined");
  });

  it("never mistakes a catalogue row with no id for the running model", async () => {
    // A row missing both `id` and `model_id` stringifies to "" — same as an
    // absent `current` field — and the two must not coincidentally match.
    renderLibrary({ models: ok({ catalog: [{ name: "Mystery One" }], loaded: [] }) });
    await screen.findByText("Mystery One");
    expect(body()).toContain(t("ko", "library.runtime.noLoaded"));
  });

  it("leads the embedding panel with the sentence, and keeps the table for advanced", async () => {
    const advanced = renderLibrary({ embeddingsStatus: ok({ state: "production", dim: 384 }) });
    await screen.findByText(t("ko", "library.embedding.state.production"));
    expect(body()).toContain(t("ko", "library.models.embedding.advanced"));
    advanced.unmount();

    renderLibrary({ embeddingsStatus: ok({ state: "fallback" }) }, { mode: "basic" });
    await screen.findByText(t("ko", "library.embedding.state.fallback"));
    expect(body()).toContain(t("ko", "library.models.embedding.basic"));
  });
});

describe("preparing a model", () => {
  beforeEach(() => vi.restoreAllMocks());

  const ONE_READY = {
    catalog: [{ id: "local/ready", name: "Ready One", load_available: true, engine: "local_mlx" }],
    loaded: [],
  };

  it("reports progress while it runs and marks the track done", async () => {
    const streamModelPrepare = vi.fn((_body: unknown, handlers: Record<string, (event: Record<string, unknown>) => void>) => {
      handlers.onProgress?.({ stage: "download", message: "내려받는 중", percent: 42, detail: "3/7" });
      handlers.onDone?.({ stage: "ready" });
      return Promise.resolve(ok({}));
    });
    renderLibrary({ models: ok(ONE_READY), streamModelPrepare });

    await userEvent.click(await screen.findByRole("button", { name: t("ko", "library.btn.validateLoad") }));

    await waitFor(() => expect(streamModelPrepare).toHaveBeenCalledWith(
      { model: "local/ready", engine: "local_mlx", allow_download: false },
      expect.anything(),
    ));
    expect(await screen.findAllByText("내려받는 중")).toBeTruthy();
    expect(body()).toContain("3/7");
    expect(body()).toContain("42%");
  });

  it("still shows a bar and a caption when the stream reports neither", async () => {
    // A frame with only a stage is common early in a run; the panel must not
    // print "undefined%" or an empty line where the caption goes.
    const streamModelPrepare = vi.fn((_body: unknown, handlers: Record<string, (event: Record<string, unknown>) => void>) => {
      handlers.onProgress?.({ stage: "engine" });
      return Promise.resolve(ok({}));
    });
    renderLibrary({
      models: ok({ catalog: [{ id: "local/plain", name: "Plain One", load_available: true }], loaded: [] }),
      streamModelPrepare,
    });

    await userEvent.click(await screen.findByRole("button", { name: t("ko", "library.btn.validateLoad") }));

    // No engine was named anywhere, so the default local one is used.
    await waitFor(() => expect(streamModelPrepare).toHaveBeenCalledWith(
      { model: "local/plain", engine: "local_mlx", allow_download: false },
      expect.anything(),
    ));
    expect(await screen.findAllByText(t("ko", "library.preparing"))).toHaveLength(2);
    expect(body()).toContain("0%");
    expect(document.querySelectorAll('[style*="width: 8%"]')).toHaveLength(2);
  });

  it("explains a failed preparation instead of leaving the row silent", async () => {
    // The client hands `onError` the friendly message and returns the bare
    // server payload; the specific message is the one that must survive.
    const streamModelPrepare = vi.fn((_body: unknown, handlers: Record<string, (event: Record<string, unknown>) => void>) => {
      handlers.onError?.({ user_message: "설치 공간이 부족합니다", recovery_guidance: ["공간을 비우세요", "다시 시도하세요"] });
      return Promise.resolve(fail("실패", { code: 507 }));
    });
    renderLibrary({ models: ok(ONE_READY), streamModelPrepare });

    await userEvent.click(await screen.findByRole("button", { name: t("ko", "library.btn.validateLoad") }));

    expect(await screen.findByText("설치 공간이 부족합니다")).toBeTruthy();
    expect(body()).toContain("공간을 비우세요");
  });

  it("still says something is wrong when the stream failed without a message", async () => {
    renderLibrary({
      models: ok(ONE_READY),
      streamModelPrepare: vi.fn(() => Promise.resolve(fail("실패", {}))),
    });

    await userEvent.click(await screen.findByRole("button", { name: t("ko", "library.btn.validateLoad") }));

    expect(await screen.findByText(t("ko", "library.model.setupAttention"))).toBeTruthy();
  });

  it("unloads the model the row names", async () => {
    const unloadModel = vi.fn(() => Promise.resolve(ok({})));
    renderLibrary({
      models: ok({ catalog: [{ id: "local/loaded", name: "Loaded One" }], loaded: ["local/loaded"] }),
      unloadModel,
    });

    // The runtime summary carries its own unload button above the catalogue;
    // this is the one on the row itself.
    const unloads = await screen.findAllByRole("button", { name: t("ko", "library.runtime.unload") });
    await userEvent.click(unloads[unloads.length - 1]);

    await waitFor(() => expect(unloadModel).toHaveBeenCalledWith("local/loaded"));
  });
});

describe("the runtime summary", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("reloads and unloads the model it is showing", async () => {
    const streamModelPrepare = vi.fn(() => Promise.resolve(ok({})));
    const unloadModel = vi.fn(() => Promise.resolve(ok({})));
    renderLibrary({
      models: ok({
        catalog: [{ id: "local/loaded", name: "Loaded One", recommended_load_id: "local/loaded@v2", recommended_engine: "local_mlx" }],
        loaded: ["local/loaded"],
        current: "local/loaded",
      }),
      modelRecommendations: ok({ profile: { os: "darwin", arch: "arm64" }, recommendations: {} }),
      streamModelPrepare,
      unloadModel,
    });

    const reload = await screen.findByRole("button", { name: t("ko", "library.runtime.reload") });
    await userEvent.click(reload);
    await waitFor(() => expect(streamModelPrepare).toHaveBeenCalledWith(
      { model: "local/loaded@v2", engine: "local_mlx", allow_download: false },
      expect.anything(),
    ));

    const unload = screen.getAllByRole("button", { name: t("ko", "library.runtime.unload") })[0];
    await userEvent.click(unload);
    await waitFor(() => expect(unloadModel).toHaveBeenCalledWith("local/loaded"));
  });

  it("uses the row's plain id and the default engine when the registry gave no recommendation", async () => {
    // No `current` field at all — only `loaded` — so `currentId` is "". The
    // row itself still carries a real id and must still count as the active
    // model, and the summary's own unload button must stay keyed off the
    // top-level `current` field rather than substituting the row's id.
    const streamModelPrepare = vi.fn(() => Promise.resolve(ok({})));
    const unloadModel = vi.fn(() => Promise.resolve(ok({})));
    renderLibrary({
      models: ok({ catalog: [{ id: "local/loaded", name: "Loaded One" }], loaded: ["local/loaded"] }),
      streamModelPrepare,
      unloadModel,
    });

    const reload = await screen.findByRole("button", { name: t("ko", "library.runtime.reload") });
    await userEvent.click(reload);
    await waitFor(() => expect(streamModelPrepare).toHaveBeenCalledWith(
      { model: "local/loaded", engine: "local_mlx", allow_download: false },
      expect.anything(),
    ));

    // Index 0 is the summary's own button, not the catalogue row's.
    const unload = screen.getAllByRole("button", { name: t("ko", "library.runtime.unload") })[0];
    await userEvent.click(unload);
    expect(unloadModel).not.toHaveBeenCalled();
  });

  it("prefers the row's own engine over the default when the registry named no recommendation", async () => {
    const streamModelPrepare = vi.fn(() => Promise.resolve(ok({})));
    renderLibrary({
      models: ok({
        catalog: [{ id: "local/loaded", name: "Loaded One", engine: "custom_engine" }],
        loaded: ["local/loaded"],
        current: "local/loaded",
      }),
      streamModelPrepare,
    });

    await userEvent.click(await screen.findByRole("button", { name: t("ko", "library.runtime.reload") }));
    await waitFor(() => expect(streamModelPrepare).toHaveBeenCalledWith(
      { model: "local/loaded", engine: "custom_engine", allow_download: false },
      expect.anything(),
    ));
  });

  it("does nothing when the loaded model is not in the catalogue", async () => {
    // The name comes from the id alone, so the buttons are live but have no
    // catalogue row to act on — they must not throw or invent one.
    const streamModelPrepare = vi.fn(() => Promise.resolve(ok({})));
    const unloadModel = vi.fn(() => Promise.resolve(ok({})));
    renderLibrary({
      models: ok({ catalog: [], loaded: [], current: "mlx-community/qwen-3-8b-4bit" }),
      streamModelPrepare,
      unloadModel,
    });

    await userEvent.click(await screen.findByRole("button", { name: t("ko", "library.runtime.reload") }));
    expect(streamModelPrepare).not.toHaveBeenCalled();

    renderLibrary({ models: ok({ catalog: [{ id: "local/loaded" }], loaded: ["local/loaded"] }) });
  });

  it("keeps the summary to two plain cells in everyday mode", async () => {
    const basic = renderLibrary({ models: ok({ catalog: [], loaded: [] }) }, { mode: "basic" });
    await screen.findByText(t("ko", "library.runtime.computer"));
    expect(body()).toContain(t("ko", "library.runtime.noneShort"));
    expect(body()).not.toContain(t("ko", "library.runtime.engine"));
    // The cache path is a developer detail; everyday mode never shows it.
    expect(body()).not.toContain("캐시");
    basic.unmount();

    renderLibrary({
      models: ok({ catalog: [], loaded: [] }),
      modelRecommendations: ok({ profile: {}, recommendations: { cache_path: "/tmp/models" } }),
    });
    await screen.findByText(t("ko", "library.runtime.engine"));
    expect(body()).toContain(t("ko", "library.runtime.noLoaded"));
    expect(body()).toContain("/tmp/models");
    expect(body()).toContain(t("ko", "library.value.idle"));
  });
});

describe("SkillsPanel", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("toggles an installed skill and installs one from the market", async () => {
    const skillToggle = vi.fn(() => Promise.resolve(ok({})));
    const skillInstall = vi.fn(() => Promise.resolve(ok({})));
    renderLibrary({
      skills: ok({
        skills: [
          { name: "요약", plugin: "core", enabled: true },
          { id: "정리", description: "설명" },
          // Neither a name, a plugin nor a description: the registry ships
          // rows this bare too, and the row must key off the id alone.
          { id: "정렬" },
        ],
      }),
      skillsMarketplace: ok({
        skills: [
          { name: "번역", description: "설명", plugin: "market" },
          { id: "검색", category: "도구" },
          { id: "요청" },
        ],
      }),
      skillToggle,
      skillInstall,
    }, {}, "skills");

    await screen.findByText("요약");
    expect(body()).toContain("core");
    expect(body()).toContain("설명");

    await userEvent.click(screen.getByRole("button", { name: t("ko", "library.skills.disable") }));
    await waitFor(() => expect(skillToggle).toHaveBeenCalledWith("요약", true));

    // "정렬" has no name, so its own toggle must call back with its id.
    const enableButtons = screen.getAllByRole("button", { name: t("ko", "library.skills.enable") });
    await userEvent.click(enableButtons[enableButtons.length - 1]);
    await waitFor(() => expect(skillToggle).toHaveBeenCalledWith("정렬", false));

    await userEvent.click(screen.getAllByRole("button", { name: t("ko", "library.skills.install") })[0]);
    await waitFor(() => expect(skillInstall).toHaveBeenCalledWith("번역", "market"));

    // "요청" has no name and no plugin: installs by id, with no plugin arg.
    const installButtons = screen.getAllByRole("button", { name: t("ko", "library.skills.install") });
    await userEvent.click(installButtons[installButtons.length - 1]);
    await waitFor(() => expect(skillInstall).toHaveBeenCalledWith("요청", ""));
  });
});

describe("McpPanel", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("names the connector list for the reader's mode and recommends on demand", async () => {
    const mcpRecommend = vi.fn(() => Promise.resolve(ok({ suggestions: [{ name: "GitHub" }] })));
    renderLibrary({ mcpTools: ok({ tools: [{ name: "GitHub", status: "ready" }] }), mcpRecommend }, {}, "mcp");

    await screen.findByText(t("ko", "library.connector.mcpTools"));
    expect(await screen.findByText("GitHub")).toBeTruthy();

    const input = screen.getByDisplayValue("github");
    await userEvent.clear(input);
    expect(screen.getByRole("button", { name: t("ko", "library.connector.recommendAction") }).hasAttribute("disabled")).toBe(true);

    await userEvent.type(input, "슬랙");
    await userEvent.click(screen.getByRole("button", { name: t("ko", "library.connector.recommendAction") }));

    await waitFor(() => expect(mcpRecommend).toHaveBeenCalledWith("슬랙"));
    expect(await screen.findByText(t("ko", "library.connector.recommendDone"))).toBeTruthy();
  });

  it("calls the panel connections in everyday mode and reads the installed list", async () => {
    renderLibrary({ mcpTools: ok({ installed_mcps: [{ name: "Notion", status: "ready" }] }) }, { mode: "basic" }, "mcp");
    await screen.findByText(t("ko", "library.connector.connections"));
    expect(await screen.findByText("Notion")).toBeTruthy();
  });
});

describe("MarketplacePanel", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("installs a template by name", async () => {
    const installTemplate = vi.fn(() => Promise.resolve(ok({})));
    renderLibrary({
      templates: ok({
        templates: [
          { id: "t1", name: "회의록" },
          // No name: the button must still be keyed and labeled from the id.
          { id: "t2" },
          // No id: the reverse — keyed and labeled from the name alone.
          { name: "노트 정리" },
        ],
      }),
      pluginsRegistry: ok({ plugins: [{ name: "설치된 플러그인", status: "ready" }] }),
      pluginsDirectory: ok({ plugins: [{ name: "디렉터리 플러그인", category: "도구" }] }),
      installTemplate,
    }, {}, "marketplace");

    await screen.findByText("설치된 플러그인");
    expect(await screen.findByText("디렉터리 플러그인")).toBeTruthy();
    expect(await screen.findByRole("button", {
      name: t("ko", "library.market.installTemplate", { name: "t2" }),
    })).toBeTruthy();
    expect(await screen.findByRole("button", {
      name: t("ko", "library.market.installTemplate", { name: "노트 정리" }),
    })).toBeTruthy();

    await userEvent.click(await screen.findByRole("button", {
      name: t("ko", "library.market.installTemplate", { name: "회의록" }),
    }));

    await waitFor(() => expect(installTemplate).toHaveBeenCalledWith({ id: "t1", name: "회의록" }));
  });

  it("says the template list is empty rather than showing a bare card", async () => {
    renderLibrary({ templates: ok({ templates: [] }) }, {}, "marketplace");
    await screen.findByText(t("ko", "library.market.templateInstall"));
    expect(body()).toContain(t("ko", "ui.empty.listDetail"));
  });
});
