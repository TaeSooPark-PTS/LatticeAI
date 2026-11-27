import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { t } from "@/i18n";
import { fail, ok, renderPage } from "@/test/renderPage";
import { MemoryListPanel } from "./MemoryListPanel";

const ROWS = [
  { id: "m1", kind: "preferences", content: "녹차를 좋아함", created_at: "2026-11-01T10:00:00" },
  { id: "m2", kind: "custom_kind", content: "그래프에 있는 기억", graph_node_id: "node-2" },
];

const button = (key: string) => screen.getByRole("button", { name: t("ko", key) });
const deleteButton = (content: string) =>
  screen.getByRole("button", { name: t("ko", "brain.memories.delete.aria", { content }) });

describe("MemoryListPanel", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("shows loading, then each memory with its kind and date", async () => {
    renderPage(<MemoryListPanel />, { api: { workspaceMemories: ok({ memories: ROWS }) } });
    expect(screen.getByRole("status").textContent).toBe(t("ko", "ui.loading"));
    expect(await screen.findByText("녹차를 좋아함")).toBeTruthy();
    expect(document.body.textContent).toContain(`${t("ko", "brain.memories.kind.preferences")} · 2026-11-01`);
    expect(screen.getByText("custom_kind")).toBeTruthy();
  });

  it("reports a load failure and retries", async () => {
    const load = vi
      .fn()
      .mockResolvedValueOnce(fail("down", { memories: [] }))
      .mockResolvedValue(ok({ memories: [] }));
    renderPage(<MemoryListPanel />, { api: { workspaceMemories: load } });
    expect((await screen.findByRole("alert")).textContent).toContain("down");
    await userEvent.click(button("common.retry"));
    expect(await screen.findByText(t("ko", "brain.memories.empty"))).toBeTruthy();
  });

  it("searches on submit, ignores a composing Enter, and clears back to the list", async () => {
    const search = vi.fn().mockResolvedValue(ok({ memories: [] }));
    renderPage(<MemoryListPanel />, {
      api: { workspaceMemories: ok({ memories: ROWS }), searchWorkspaceMemories: search },
    });
    await screen.findByText("녹차를 좋아함");
    const box = screen.getByRole("searchbox");
    fireEvent.change(box, { target: { value: " 커피 " } });
    expect(fireEvent.keyDown(box, { key: "Enter", isComposing: true })).toBe(false);
    expect(fireEvent.keyDown(box, { key: "Enter" })).toBe(true);
    expect(fireEvent.keyDown(box, { key: "a" })).toBe(true);
    await userEvent.click(button("brain.memories.search.action"));
    expect(await screen.findByText(t("ko", "brain.memories.noMatch"))).toBeTruthy();
    expect(search).toHaveBeenCalledWith("커피");
    fireEvent.change(box, { target: { value: "커" } });
    fireEvent.change(box, { target: { value: "" } });
    expect(await screen.findByText("녹차를 좋아함")).toBeTruthy();
  });

  it("keeps the draft when adding fails and clears it when it succeeds", async () => {
    const add = vi.fn().mockResolvedValueOnce(fail("거절됨", {})).mockResolvedValue(ok({ memory: ROWS[0] }));
    renderPage(<MemoryListPanel />, {
      api: { workspaceMemories: ok({ memories: [] }), addWorkspaceMemory: add },
    });
    const field = screen.getByLabelText(t("ko", "brain.memories.add.label")) as HTMLTextAreaElement;
    await userEvent.selectOptions(screen.getByRole("combobox"), "decisions");
    await userEvent.type(field, "월요일 배포");
    await userEvent.click(button("brain.memories.add.action"));
    expect((await screen.findByRole("alert")).textContent).toContain("거절됨");
    expect(field.value).toBe("월요일 배포");
    await userEvent.click(button("brain.memories.add.action"));
    await waitFor(() => expect(field.value).toBe(""));
    expect(add).toHaveBeenLastCalledWith("decisions", "월요일 배포");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("deletes only after explicit confirmation, and cancel keeps the memory", async () => {
    const remove = vi.fn().mockResolvedValueOnce(fail("권한 없음", {})).mockResolvedValue(ok({ status: "ok" }));
    renderPage(<MemoryListPanel />, {
      api: { workspaceMemories: ok({ memories: ROWS }), deleteWorkspaceMemory: remove },
    });
    await screen.findByText("녹차를 좋아함");

    await userEvent.click(deleteButton("녹차를 좋아함"));
    expect(screen.getByText(t("ko", "brain.memories.delete.permanent"))).toBeTruthy();
    expect(document.activeElement).toBe(button("ui.cancel"));
    await userEvent.click(button("ui.cancel"));
    expect(remove).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(deleteButton("녹차를 좋아함"));

    await userEvent.click(deleteButton("그래프에 있는 기억"));
    expect(screen.getByText(t("ko", "brain.memories.delete.graphCopy"))).toBeTruthy();
    await userEvent.click(button("brain.memories.delete.confirm"));
    expect((await screen.findByRole("alert")).textContent).toContain("권한 없음");
    await userEvent.click(button("brain.memories.delete.confirm"));
    await waitFor(() => expect(screen.queryByRole("group")).toBeNull());
    expect(remove).toHaveBeenLastCalledWith("m2");
  });

  it("shows pending labels while adding and deleting", async () => {
    const never = () => new Promise(() => {});
    renderPage(<MemoryListPanel />, {
      api: { workspaceMemories: ok({ memories: ROWS }), addWorkspaceMemory: never, deleteWorkspaceMemory: never },
    });
    await screen.findByText("녹차를 좋아함");
    await userEvent.type(screen.getByLabelText(t("ko", "brain.memories.add.label")), "x");
    await userEvent.click(button("brain.memories.add.action"));
    expect(await screen.findByRole("button", { name: t("ko", "brain.memories.add.pending") })).toBeTruthy();
    await userEvent.click(deleteButton("녹차를 좋아함"));
    await userEvent.click(button("brain.memories.delete.confirm"));
    expect(await screen.findByRole("button", { name: t("ko", "brain.memories.delete.pending") })).toBeTruthy();
  });
});
