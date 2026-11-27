import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { fail, ok, renderPage } from "@/test/renderPage";
import { DocumentLibrary } from "./DocumentLibrary";
import { RecentCapturePanel } from "./CaptureStatusPanels";

const docs = (count: number) =>
  Array.from({ length: count }, (_, index) => ({ id: `d-${index}`, filename: `note-${index}.md`, ingest_state: "indexed" }));

const render = (result: Parameters<typeof DocumentLibrary>[0]["result"], onRetry = vi.fn(), api = {}) =>
  renderPage(<DocumentLibrary result={result} onRetry={onRetry} />, { language: "en", api });

describe("DocumentLibrary", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("says it is loading before the first answer", () => {
    render(undefined);
    expect(screen.getByRole("status").textContent).toBeTruthy();
    expect(screen.queryByRole("searchbox")).toBeNull();
  });

  it("shows a load failure with its reason and retries on request", async () => {
    const onRetry = vi.fn();
    render(fail("disk offline", { documents: [] }) as never, onRetry);
    expect(screen.getByRole("alert").textContent).toContain("disk offline");
    await userEvent.click(screen.getByRole("button", { name: "Reload" }));
    expect(onRetry).toHaveBeenCalled();
  });

  it("copes with a failure that carries no reason", () => {
    render(fail("", {}) as never);
    expect(screen.getByRole("alert").textContent).toContain("Could not load your documents.");
  });

  it("reads an empty library as empty", () => {
    render(ok({ documents: [] }) as never);
    expect(screen.getByText("No documents saved yet.")).toBeTruthy();
  });

  it("pages through the list and filters by name, resetting the page", async () => {
    render(ok({ documents: docs(30) }) as never);
    expect(screen.getAllByRole("listitem")).toHaveLength(12);
    await userEvent.click(screen.getByRole("button", { name: "Show more" }));
    expect(screen.getAllByRole("listitem")).toHaveLength(24);
    await userEvent.click(screen.getByRole("button", { name: "Show more" }));
    expect(screen.getAllByRole("listitem")).toHaveLength(30);
    expect(screen.queryByRole("button", { name: "Show more" })).toBeNull();

    fireEvent.change(screen.getByRole("searchbox"), { target: { value: " NOTE-2" } });
    expect(screen.getAllByRole("listitem")).toHaveLength(11);
    expect(screen.getByRole("status").textContent).toBe("Showing 11 of 11");
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "" } });
    expect(screen.getAllByRole("listitem")).toHaveLength(12);
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "zzz" } });
    expect(screen.getByText("No document matches that name.")).toBeTruthy();
  });

  it("matches decomposed Hangul names and falls back when a name is missing", () => {
    render(ok({ documents: [
      { id: "a", filename: "회의록.md".normalize("NFD") },
      { title: "from-title", ingest_state: "ingested" },
      {},
    ] }) as never);
    expect(screen.getByText("Untitled document")).toBeTruthy();
    expect(screen.getByText("from-title")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: /Preview/ })).toHaveLength(1);
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "회의록" } });
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
  });

  it("says when the fetch limit was reached", () => {
    render(ok({ documents: docs(200) }) as never);
    expect(screen.getByText("Only the 200 most recently updated documents are listed.")).toBeTruthy();
  });

  it("opens a preview from the graph node and closes it", async () => {
    render(ok({ documents: docs(1) }) as never, vi.fn(), {
      graphNode: ok({ node: { title: "note-0.md", type: "Document", summary: "preview body" } }),
    });
    await userEvent.click(screen.getByRole("button", { name: "Preview note-0.md" }));
    expect(await screen.findByText("preview body")).toBeTruthy();
    expect(latticeApi.graphNode).toHaveBeenCalledWith("d-0");
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});

describe("library reload from the capture panel", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("fetches the documents again", async () => {
    renderPage(<RecentCapturePanel />, { language: "en", api: { documents: fail("offline", { documents: [] }) } });
    await userEvent.click(await screen.findByRole("button", { name: "Reload" }));
    await waitFor(() => expect(latticeApi.documents).toHaveBeenCalledTimes(2));
    expect(latticeApi.documents).toHaveBeenLastCalledWith(200);
  });
});
