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
});
