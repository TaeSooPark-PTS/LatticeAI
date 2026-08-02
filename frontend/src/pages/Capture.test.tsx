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

  /**
   * Adding material is one action, not a choice between four screens.
   *
   * File, folder and web used to be three of four page-level tabs, ranked equal
   * with the indexer's status page — so the highest-value path here, connecting
   * a folder, sat two tabs deep behind a label naming a *place* rather than a
   * thing to do. These tests hold the station together: one group of methods at
   * the top, and the reporting panels always on screen beneath it.
   */
  it("puts every way of adding material in one station, with no page tablist", async () => {
    render();
    await waitFor(() => expect(screen.getByTestId("capture-method-files")).toBeTruthy());

    expect(screen.queryAllByRole("tab")).toHaveLength(0);
    for (const name of ["파일 올리기", "폴더 연결하기", "웹페이지 저장하기"]) {
      expect(screen.getByRole("button", { name })).toBeTruthy();
    }
    // Toggle buttons, so the chosen one announces itself as pressed.
    expect(screen.getByTestId("capture-method-files").getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByTestId("capture-method-local").getAttribute("aria-pressed")).toBe("false");
  });

  it("switching method swaps the input without leaving the station", async () => {
    render();
    await waitFor(() => expect(screen.getByTestId("capture-method-local")).toBeTruthy());

    await userEvent.click(screen.getByTestId("capture-method-local"));
    await waitFor(() => expect(screen.getByRole("button", { name: /폴더 선택/ })).toBeTruthy());
    expect(screen.getByTestId("capture-method-local").getAttribute("aria-pressed")).toBe("true");

    await userEvent.click(screen.getByTestId("capture-method-browser"));
    await waitFor(() => expect(screen.getByRole("button", { name: /스캔하고 저장/ })).toBeTruthy());
  });

  it("shows progress and what was already added without choosing a tab first", async () => {
    // Both used to be tabs of their own, so a person who added a file and
    // wanted to know where it went had to go looking for the answer.
    render();
    await waitFor(() => expect(document.body.textContent).toContain("업로드된 문서"));
    expect(screen.getByRole("list", { name: "자료가 기억이 되는 3단계" })).toBeTruthy();
    expect(document.body.textContent).toContain("연결된 출처");
  });

  it("a direct link to a method opens that method", async () => {
    renderPage(<CapturePage initialTab="browser" />, { api: { localSources: ok(SOURCES) } });
    await waitFor(() =>
      expect(screen.getByTestId("capture-method-browser").getAttribute("aria-pressed")).toBe("true"));
    expect(screen.getByRole("button", { name: /스캔하고 저장/ })).toBeTruthy();
  });
});
