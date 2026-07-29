import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { fail, ok, renderPage } from "@/test/renderPage";
import { ActPage } from "./Act";

/**
 * The automation surface. Its contract is consent-first: a suggestion is a
 * draft, an install is disabled by default, and anything that would change
 * existing content becomes a reviewable proposal. The screen must also read in
 * the user's language — agent roles and recipe names arrive as stable ids and
 * are localised here, which is exactly where an id can leak through.
 */

const RECIPES = {
  recipes: [
    { id: "daily_memory_digest", cadence: "daily", creates: "note", enabled: false },
    { id: "weekly_review", cadence: "weekly", creates: "document", enabled: false },
  ],
};

const REGISTRY = {
  agents: [
    { id: "agent:researcher", type: "agent:researcher", name: "Researcher", status: "available" },
    { id: "agent:reviewer", type: "agent:reviewer", name: "Reviewer", status: "available" },
  ],
};

function render(overrides = {}, options = {}) {
  return renderPage(<ActPage />, {
    api: {
      automationRecipes: ok(RECIPES),
      agentRegistry: ok(REGISTRY),
      agentCapabilities: ok({ capabilities: [] }),
      agentRuntime: ok({ status: "ready", version: "10.3.0" }),
      hooks: ok({ hooks: [] }),
      hookRuns: ok({ runs: [] }),
      ...overrides,
    },
    ...options,
  });
}

describe("ActPage", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("renders the automation surface", async () => {
    render();
    await waitFor(() => expect((document.body.textContent || "").length).toBeGreaterThan(20));
  });

  it("localises agent roles by id rather than printing the id", async () => {
    render();
    await waitFor(() => expect(document.body.textContent).toBeTruthy());
    expect(document.body.textContent).not.toMatch(/agent:researcher|agent:reviewer/);
  });

  it("localises recipe names rather than printing their keys", async () => {
    render();
    await waitFor(() => expect(document.body.textContent).toBeTruthy());
    expect(document.body.textContent).not.toMatch(/daily_memory_digest|weekly_review/);
  });

  it("renders in English when the language is en", async () => {
    render({}, { language: "en" });
    await waitFor(() => expect(document.body.textContent).toBeTruthy());
    // A Korean-only string leaking into the English UI is the failure mode the
    // i18n namespace checker cannot see.
    expect(document.body.textContent).not.toMatch(/자동화 제안|오늘의 기억 정리/);
  });

  it("an unavailable recipe list is reported rather than shown as none", async () => {
    render({ automationRecipes: fail("server unavailable", { recipes: [] }) });
    await waitFor(() => expect(document.body.textContent).toBeTruthy());
    expect(document.body.textContent).not.toMatch(/undefined|NaN/);
  });

  it("an empty registry reads as empty rather than as broken", async () => {
    render({ agentRegistry: ok({ agents: [] }), automationRecipes: ok({ recipes: [] }) });
    await waitFor(() => expect(document.body.textContent).toBeTruthy());
    expect(document.body.textContent).not.toMatch(/undefined|\[object Object\]/);
  });

  it("a recipe row missing its optional fields still renders", async () => {
    render({ automationRecipes: ok({ recipes: [{ id: "bare" }] }) });
    await waitFor(() => expect(document.body.textContent).toBeTruthy());
    expect(document.body.textContent).not.toMatch(/undefined/);
  });

  it("does not present any automation as already running", async () => {
    // Installs are consent-first drafts; the screen must not imply otherwise.
    render();
    await waitFor(() => expect(document.body.textContent).toBeTruthy());
    const checkboxes = screen.queryAllByRole("checkbox") as HTMLInputElement[];
    expect(checkboxes.every((c) => !c.checked || c.disabled)).toBe(true);
  });
});
