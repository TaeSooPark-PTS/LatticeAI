import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { t } from "@/i18n";
import "@/i18n/brain";
import { fail, ok, renderPage, type RenderPageOptions } from "@/test/renderPage";
import ConversationLibrary from "./ConversationLibrary";
import { conversationMarkdown, downloadMarkdown, markdownFileName } from "./conversationExport";
import { buildSearchMatches, LEGACY_CONVERSATION_ID } from "./searchResults";

const LIST = ok([
  { id: "c1", title: "Ranking", message_count: 2, updated_at: "2026-06-02T10:00:00" },
  { id: "c2", title: "Undated", message_count: 1 },
]);
const SEARCH = ok({
  query: "rank",
  results: [{ conversation_id: "c1", title: "x", messages: [{ content: "how to rank", timestamp: "2026-06-02T10:00:00" }] }],
});
const CONVERSATION = ok({ messages: [{ role: "user", content: "hello", timestamp: "2026-06-02T10:00:00" }] });

function render(api: RenderPageOptions["api"] = {}, props: { busyId?: string | null } = {}) {
  const handlers = { onResume: vi.fn(), onDelete: vi.fn(), onBack: vi.fn() };
  const view = renderPage(
    <ConversationLibrary language="en" busyId={props.busyId ?? null} {...handlers} />,
    { language: "en", api: { chatHistory: LIST, searchHistory: SEARCH, conversation: CONVERSATION, ...api } },
  );
  return { ...view, ...handlers };
}

const name = (key: string, args?: Record<string, string | number>) => t("en", key, args);
const box = () => screen.getByRole("textbox", { name: name("brain.history.library.searchLabel") });
const resume = (title: string) => screen.findByRole("button", { name: name("brain.history.resumeAria", { title }) });

afterEach(() => vi.restoreAllMocks());

describe("ConversationLibrary", () => {
  it("lists every conversation, focuses search, opens and goes back", async () => {
    const view = render();
    expect(box()).toHaveFocus();
    await userEvent.click(await resume("Ranking"));
    expect(view.onResume).toHaveBeenCalledWith("c1");
    expect(await resume("Undated")).toHaveTextContent(name("brain.history.messages", { count: 1 }));
    await userEvent.click(screen.getByRole("button", { name: name("brain.history.library.back") }));
    expect(view.onBack).toHaveBeenCalled();
  });

  it("deletes only after a second press and resets on blur", async () => {
    const view = render();
    await resume("Ranking");
    const trash = () => screen.getByRole("button", { name: name("brain.history.deleteAria", { title: "Ranking" }) });
    await userEvent.click(trash());
    expect(view.onDelete).not.toHaveBeenCalled();
    fireEvent.blur(screen.getByRole("button", { name: name("brain.history.deleteConfirm") }));
    await userEvent.click(trash());
    await userEvent.click(screen.getByRole("button", { name: name("brain.history.deleteConfirm") }));
    expect(view.onDelete).toHaveBeenCalledWith("c1");
  });

  it("disables the busy row", async () => {
    render({}, { busyId: "c1" });
    expect(await resume("Ranking")).toBeDisabled();
  });

  it("searches on submit, not mid-composition, and clears", async () => {
    render();
    await resume("Undated");
    fireEvent.change(box(), { target: { value: "rank" } });
    expect(fireEvent.keyDown(box(), { key: "Enter", isComposing: true })).toBe(false);
    expect(fireEvent.keyDown(box(), { key: "Enter" })).toBe(true);
    expect(fireEvent.keyDown(box(), { key: "a", isComposing: true })).toBe(true);
    expect(latticeApi.searchHistory).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: name("brain.history.library.search") }));
    expect(await screen.findByText("how to rank")).toBeInTheDocument();
    expect(latticeApi.searchHistory).toHaveBeenCalledWith("rank");
    await userEvent.click(screen.getByRole("button", { name: name("brain.history.library.clear") }));
    expect(await resume("Undated")).toBeInTheDocument();
    await userEvent.type(box(), "rank{Enter}");
    await screen.findByText("how to rank");
    fireEvent.change(box(), { target: { value: "" } });
    expect(await resume("Undated")).toBeInTheDocument();
  });

  it("warns when search hits the server cap and shows no-match and empty states", async () => {
    const results = Array.from({ length: 30 }, (_, index) => ({ conversation_id: `s${index}`, title: `T${index}`, messages: [] }));
    render({ searchHistory: ok({ results }) });
    await userEvent.type(box(), "a{Enter}");
    expect(await screen.findByText(name("brain.history.library.capped", { count: 30 }))).toBeInTheDocument();
  });

  it("explains an unmatched search", async () => {
    render({ searchHistory: ok({ results: [] }) });
    await userEvent.type(box(), "zzz{Enter}");
    expect(await screen.findByText(name("brain.history.library.noMatchTitle"))).toBeInTheDocument();
  });

  it("explains an empty library", async () => {
    render({ chatHistory: ok([]) });
    expect(await screen.findByText(name("brain.history.library.emptyTitle"))).toBeInTheDocument();
  });

  it("shows loading, then an error with retry", async () => {
    render({ chatHistory: fail("offline", []) });
    expect(screen.getByText(name("ui.loading"))).toBeInTheDocument();
    expect(await screen.findByText("offline")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: name("brain.history.library.retry") }));
    await waitFor(() => expect(latticeApi.chatHistory).toHaveBeenCalledTimes(2));
  });

  it("downloads one conversation as Markdown", async () => {
    URL.createObjectURL = vi.fn(() => "blob:x");
    URL.revokeObjectURL = vi.fn();
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    render();
    await resume("Ranking");
    await userEvent.click(screen.getByRole("button", { name: name("brain.history.library.exportAria", { title: "Ranking" }) }));
    await waitFor(() => expect(click).toHaveBeenCalled());
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:x");
  });

  it("reports why an export failed", async () => {
    const replies = [fail("gone", {}), ok({ messages: [] })];
    render({ conversation: async () => replies.shift() });
    await resume("Ranking");
    const exportButton = screen.getByRole("button", { name: name("brain.history.library.exportAria", { title: "Ranking" }) });
    await userEvent.click(exportButton);
    expect(await screen.findByRole("alert")).toHaveTextContent("gone");
    await userEvent.click(exportButton);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(name("brain.history.emptyConversation")));
  });
});

describe("conversation export helpers", () => {
  it("keeps user and assistant turns only", () => {
    const markdown = conversationMarkdown("en", "Title", {
      messages: [
        { role: "user", content: " hi ", timestamp: "2026-01-01" },
        { role: "assistant", content: "yo" },
        { role: "system", content: "hidden" },
        { role: "user", content: "  " },
        { role: "user", content: 4 },
      ],
    });
    expect(markdown).toBe("# Title\n\n## You · 2026-01-01\n\nhi\n\n## Brain\n\nyo\n");
    expect(conversationMarkdown("en", "Title", null)).toBeNull();
  });

  it("builds a safe file name", () => {
    expect(markdownFileName("..a/b: c?.. ")).toBe("a b c.md");
    expect(markdownFileName("///")).toBe("conversation.md");
  });

  it("removes the temporary anchor", () => {
    URL.createObjectURL = vi.fn(() => "blob:y");
    URL.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    downloadMarkdown("a.md", "# a");
    expect(document.querySelector("a[download]")).toBeNull();
  });
});

describe("buildSearchMatches", () => {
  it("sorts newest first, maps the legacy bucket and trims long snippets", () => {
    const long = `${"x".repeat(100)} needle ${"y".repeat(200)}`;
    const matches = buildSearchMatches({
      results: [
        { conversation_id: "legacy", messages: [{ content: long, timestamp: "2026-01-01T00:00:00" }] },
        { conversation_id: "new", title: "New", messages: [{ timestamp: "2026-02-01T00:00:00" }, { timestamp: "bad" }] },
        { messages: [] },
      ],
    }, "needle", []);
    expect(matches.map((item) => item.id)).toEqual(["new", LEGACY_CONVERSATION_ID]);
    expect(matches[1]!.snippet.startsWith("…")).toBe(true);
    expect(matches[1]!.snippet.endsWith("…")).toBe(true);
    expect(matches[1]!.title).toBe(LEGACY_CONVERSATION_ID);
    expect(buildSearchMatches(null, "a", [])).toEqual([]);
  });

  it("prefers the known title and timestamp", () => {
    const [match] = buildSearchMatches(
      { results: [{ conversation_id: "c1", title: "x" }] },
      "q",
      [{ id: "c1", title: "Known", messageCount: 1, updatedAt: 5 }],
    );
    expect(match).toMatchObject({ title: "Known", updatedAt: 5, matches: 0, snippet: "" });
  });
});
