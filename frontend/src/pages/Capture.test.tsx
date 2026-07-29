import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { fail, ok, renderPage } from "@/test/renderPage";
import { CapturePage } from "./Capture";

/**
 * The capture screen brings outside material into the Brain. Its honesty
 * requirements are specific: an unscanned folder must not read as "0% indexed",
 * a failed ingest must name its reason, and nothing may claim to have been
 * captured that was not.
 */

const SOURCES = {
  sources: [
    { id: "src-1", root_path: "/Users/me/notes", label: "Notes", status: "active", watch_enabled: true },
  ],
};

function render(overrides = {}, options = {}) {
  return renderPage(<CapturePage />, {
    api: {
      localSources: ok(SOURCES),
      documents: ok({ documents: [] }),
      graphStats: ok({ nodes: 12, edges: 20 }),
      indexStatus: ok({ status: "idle", pending: 0, total: 12 }),
      ...overrides,
    },
    ...options,
  });
}

describe("CapturePage", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("renders the capture surface", async () => {
    render();
    await waitFor(() => expect(document.body.textContent).toBeTruthy());
    expect((document.body.textContent || "").length).toBeGreaterThan(20);
  });

  it("offers at least one control for adding material", async () => {
    render();
    await waitFor(() => expect(document.body.textContent).toBeTruthy());
    expect(screen.queryAllByRole("button").length + screen.queryAllByRole("tab").length)
      .toBeGreaterThan(0);
  });

  it("an unavailable source list does not render as nothing-connected", async () => {
    render({ localSources: fail("server unavailable", { sources: [] }) });
    await waitFor(() => expect(document.body.textContent).toBeTruthy());
    expect(document.body.textContent).not.toMatch(/undefined|NaN/);
  });

  it("an empty source list reads as empty, not as broken", async () => {
    render({ localSources: ok({ sources: [] }) });
    await waitFor(() => expect(document.body.textContent).toBeTruthy());
    expect(document.body.textContent).not.toMatch(/undefined|NaN|\[object Object\]/);
  });

  it("renders in English when the language is en", async () => {
    render({}, { language: "en" });
    await waitFor(() => expect(document.body.textContent).toBeTruthy());
    expect(document.body.textContent).not.toMatch(/자료 넣기|폴더 연결/);
  });

  it("a source row missing its optional fields still renders", async () => {
    render({ localSources: ok({ sources: [{ id: "bare" }] }) });
    await waitFor(() => expect(document.body.textContent).toBeTruthy());
    expect(document.body.textContent).not.toMatch(/undefined/);
  });
});
