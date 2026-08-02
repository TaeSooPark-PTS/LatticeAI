import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { fail, ok, renderPage } from "@/test/renderPage";
import { LibraryPage } from "./Library";

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
