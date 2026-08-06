import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ApiResult } from "@/api/base";
import { t } from "@/i18n";
import { useAppStore } from "@/store/appStore";
import {
  ActionButton,
  DataPanel,
  EmptyState,
  EntityList,
  focusTabButton,
  FriendlySummary,
  KeyValueList,
  LoadingPanel,
  ModeGate,
  OperationResult,
  SourceBadge,
  StatGrid,
  StructuredView,
  Tabs,
  ValuePreview,
} from "./primitives";

function renderActionButton(result: ApiResult<unknown>, onSuccess = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  const invalidate = vi.spyOn(queryClient, "invalidateQueries");
  render(
    <QueryClientProvider client={queryClient}>
      <ActionButton
        label="Run"
        action={async () => result}
        onSuccess={onSuccess}
        invalidate={["memoryManager"]}
      />
    </QueryClientProvider>,
  );
  return { invalidate, onSuccess };
}

describe("ActionButton", () => {
  it("does not call success callbacks or invalidate queries for ok:false results", async () => {
    const spies = renderActionButton({
      ok: false,
      status: 503,
      data: {},
      source: "unavailable",
      error: "service offline",
    });

    await userEvent.click(screen.getByRole("button", { name: "Run" }));
    await screen.findByText("service offline");

    expect(spies.onSuccess).not.toHaveBeenCalled();
    expect(spies.invalidate).not.toHaveBeenCalled();
  });

  it("runs success callbacks and invalidation only for ok:true results", async () => {
    const spies = renderActionButton({ ok: true, status: 200, data: {}, source: "live" });

    await userEvent.click(screen.getByRole("button", { name: "Run" }));

    await waitFor(() => expect(spies.onSuccess).toHaveBeenCalledOnce());
    expect(spies.invalidate).toHaveBeenCalledWith({ queryKey: ["memoryManager"] });
  });
});

/**
 * The shared display primitives.
 *
 * Everything on this app's screens is drawn by these: a payload the server
 * shaped for a machine is turned into something a person reads. The failure
 * mode they exist to prevent is a screen that prints `[object Object]`,
 * `undefined`, a raw registry coordinate, or an internal enum — and, in basic
 * mode, one that leaks a field a non-technical owner should never see. Each
 * one is driven here through both modes and both languages, because the copy
 * they pick is the whole behaviour.
 */

type Mode = "basic" | "advanced" | "admin";

function renderIn(
  ui: React.ReactElement,
  { mode = "advanced", language = "ko" }: { mode?: Mode; language?: "ko" | "en" } = {},
) {
  useAppStore.setState({ mode, language } as never);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const text = () => document.body.textContent || "";

beforeEach(() => {
  useAppStore.setState({ mode: "advanced", language: "ko" } as never);
});

describe("SourceBadge", () => {
  it("says the data was never asked for when there is no result", () => {
    renderIn(<SourceBadge />);
    expect(text()).toContain(t("ko", "ui.status.notLoaded"));
  });

  it("speaks about the product in basic mode and about the wire in advanced", () => {
    const live = { source: "live", ok: true, status: 200 };
    const down = { source: "unavailable", ok: false, status: 503 };

    const basicOk = renderIn(<SourceBadge result={live} />, { mode: "basic" });
    expect(text()).toContain(t("ko", "ui.status.ready"));
    basicOk.unmount();

    const advancedOk = renderIn(<SourceBadge result={live} />);
    expect(text()).toContain(t("ko", "ui.status.connected"));
    advancedOk.unmount();

    const basicDown = renderIn(<SourceBadge result={down} />, { mode: "basic" });
    expect(text()).toContain(t("ko", "ui.status.needsSetup"));
    basicDown.unmount();

    renderIn(<SourceBadge result={down} />);
    expect(text()).toContain(t("ko", "ui.status.unavailable"));
  });
});

describe("EmptyState and LoadingPanel", () => {
  it("falls back to its own title and omits an absent detail", () => {
    const { container } = renderIn(<EmptyState />);
    expect(text()).toContain(t("ko", "ui.empty.title"));
    expect(container.querySelector(".max-w-md")).toBeNull();
  });

  it("shows the title and detail it was given", () => {
    renderIn(<EmptyState title="아무것도 없어요" detail={<em>나중에 다시</em>} />);
    expect(text()).toContain("아무것도 없어요");
    expect(text()).toContain("나중에 다시");
  });

  it("names what is loading rather than showing a bare spinner", () => {
    renderIn(<LoadingPanel title="모델 목록" />);
    expect(text()).toContain("모델 목록");
    expect(text()).toContain(t("ko", "ui.loading"));
  });
});

describe("DataPanel", () => {
  const body = (data: { note: string }) => <p>{data.note}</p>;

  it("renders its children and the source badge for a live result", () => {
    renderIn(
      <DataPanel title="패널" description="설명" result={{ ok: true, status: 200, source: "live", data: { note: "내용" } }}>
        {body}
      </DataPanel>,
    );
    expect(text()).toContain("설명");
    expect(text()).toContain("내용");
    expect(text()).toContain(t("ko", "ui.status.connected"));
  });

  it("hides the source badge and the wire error in basic mode", () => {
    renderIn(
      <DataPanel title="패널" result={{ ok: false, status: 503, source: "unavailable", data: null, error: "ECONNREFUSED" }}>
        {body as never}
      </DataPanel>,
      { mode: "basic" },
    );
    expect(text()).not.toContain("ECONNREFUSED");
    expect(text()).toContain(t("ko", "ui.empty.basicDetail"));
  });

  it("names the failure in advanced mode, and says so plainly when there is none", () => {
    const withError = renderIn(
      <DataPanel title="패널" result={{ ok: false, status: 503, source: "unavailable", data: null, error: "ECONNREFUSED" }}>
        {body as never}
      </DataPanel>,
    );
    expect(text()).toContain("ECONNREFUSED");
    withError.unmount();

    renderIn(<DataPanel title="패널" className="extra">{body as never}</DataPanel>);
    expect(text()).toContain(t("ko", "ui.empty.advancedDetail"));
    expect(document.querySelector(".extra")).toBeTruthy();
  });
});

describe("StatGrid", () => {
  it("formats numbers, dashes absent values, and only shows a hint when there is one", () => {
    const { container } = renderIn(
      <StatGrid stats={[
        { label: "노드", value: 12345, hint: "그래프 전체" },
        { label: "빈 값", value: null },
        { label: "글자", value: "준비됨" },
      ]} />,
    );
    expect(text()).toContain("12,345");
    expect(text()).toContain("그래프 전체");
    expect(text()).toContain("-");
    expect(text()).toContain("준비됨");
    expect(container.querySelectorAll(".text-xs.leading-5")).toHaveLength(1);
  });
});

describe("ValuePreview", () => {
  it("reads a boolean as a state, not as true/false", () => {
    const on = renderIn(<ValuePreview value={true} />);
    expect(text()).toContain(t("ko", "ui.value.enabled"));
    on.unmount();

    renderIn(<ValuePreview value={false} />);
    expect(text()).toContain(t("ko", "ui.value.disabled"));
  });

  it("says an empty list is empty instead of drawing nothing", () => {
    renderIn(<ValuePreview value={[]} />);
    expect(text()).toContain(t("ko", "ui.value.none"));
  });

  it("shows the first five entries of a flat list and counts the rest", () => {
    renderIn(<ValuePreview value={["a", 2, true, false, null, "", "g", "h"]} />);
    expect(text()).toContain("a");
    expect(text()).toContain("Enabled");
    expect(text()).toContain("Disabled");
    // null has no readable value of its own.
    expect(text()).toContain("-");
    expect(text()).toContain("+3");
  });

  it("counts a list of objects rather than printing them", () => {
    renderIn(<ValuePreview value={[{ a: 1 }, { b: 2 }]} />);
    expect(text()).toContain(t("ko", "ui.value.records", { count: "2" }));
    expect(text()).not.toContain("[object Object]");
  });

  it("summarises an object with a value, not with its field names", () => {
    // Listing the keys read like the value; the one field worth showing is a
    // state a person can act on.
    renderIn(<ValuePreview value={{ status: null, state: undefined, name: "", title: "준비 완료" }} />);
    expect(text()).toBe("준비 완료");
  });

  it("skips fields that hold another object or no readable value", () => {
    const nested = renderIn(<ValuePreview value={{ status: ["a"], name: "이름" }} />);
    expect(text()).toBe("이름");
    nested.unmount();

    // Nothing summarisable is left, so say how much is in there.
    renderIn(<ValuePreview value={{ status: Number.NaN, extra: { deep: 1 }, other: 3 }} />);
    expect(text()).toContain(t("ko", "ui.value.fields", { count: "3" }));
  });

  it("says an object with no fields has none", () => {
    renderIn(<ValuePreview value={{}} />);
    expect(text()).toContain(t("ko", "ui.value.noFields"));
  });

  it("truncates a long summary and a long scalar rather than flooding the row", () => {
    const long = "가".repeat(120);
    const summary = renderIn(<ValuePreview value={{ status: long }} />);
    expect(text().endsWith("...")).toBe(true);
    expect(text().length).toBeLessThan(long.length);
    summary.unmount();

    renderIn(<ValuePreview value={long} />);
    expect(text().endsWith("...")).toBe(true);
  });

  it("dashes a number that is not a number", () => {
    renderIn(<ValuePreview value={Number.POSITIVE_INFINITY} />);
    expect(text()).toBe("-");
  });
});

describe("KeyValueList", () => {
  it("says there is nothing to show rather than drawing an empty frame", () => {
    renderIn(<KeyValueList data={undefined as unknown as Record<string, unknown>} />);
    expect(text()).toContain(t("ko", "ui.noValues"));
  });

  it("hides internals in basic mode and keeps them in advanced", () => {
    const data = { status: "ready", api_token: "sk-secret", endpoint: "http://x", note: "메모" };

    const basic = renderIn(<KeyValueList data={data} />, { mode: "basic" });
    expect(text()).not.toContain("sk-secret");
    expect(text()).toContain("메모");
    basic.unmount();

    renderIn(<KeyValueList data={data} />);
    expect(text()).toContain("sk-secret");
  });

  it("translates the keys it knows and titleizes the rest", () => {
    renderIn(<KeyValueList data={{ status: "ready", custom_thing: "값" }} />);
    expect(text()).toContain(t("ko", "ui.field.status"));
    expect(text()).toContain("Custom Thing");
  });

  it("never prints a registry coordinate as a model value", () => {
    renderIn(<KeyValueList data={{
      current_model: "mlx-community/gemma-4-26b-a4b-it-4bit",
      loaded_models: ["mlx-community/qwen-3-8b-4bit", "plain-name"],
      model_count: 2,
    }} />);
    expect(text()).not.toContain("mlx-community/");
    expect(text()).toContain("Gemma 4 26b A4b It");
    expect(text()).toContain("plain-name");
    expect(text()).toContain("2");
  });

  it("stops at the limit it was given", () => {
    const { container } = renderIn(
      <KeyValueList data={{ a: 1, b: 2, c: 3, d: 4 }} limit={2} />,
    );
    expect(container.querySelectorAll(".grid-cols-\\[minmax\\(9rem\\,0\\.5fr\\)_1fr\\]")).toHaveLength(2);
  });
});

describe("StructuredView", () => {
  it("hands basic mode the friendly summary instead of the raw shape", () => {
    renderIn(<StructuredView value={{ documents: [{ name: "노트" }] }} />, { mode: "basic" });
    expect(text()).toContain("노트");
  });

  it("says an empty list is empty", () => {
    renderIn(<StructuredView value={[]} />);
    expect(text()).toContain(t("ko", "ui.empty.listDetail"));
  });

  it("lists records as entities and scalars as badges", () => {
    const records = renderIn(<StructuredView value={[{ title: "첫째" }, { title: "둘째" }]} />);
    expect(text()).toContain("첫째");
    records.unmount();

    const over = renderIn(<StructuredView value={["a", "b", "c"]} limit={2} />);
    expect(text()).toContain("a");
    expect(text()).toContain("+1");
    over.unmount();

    // Nothing was left out, so there is no overflow chip to explain.
    renderIn(<StructuredView value={["a", "b"]} limit={2} />);
    expect(text()).not.toContain("+");
  });

  it("falls through to a key/value list for an object and a preview for a scalar", () => {
    const record = renderIn(<StructuredView value={{ status: "ready" }} />);
    expect(text()).toContain(t("ko", "ui.field.status"));
    record.unmount();

    renderIn(<StructuredView value="그냥 문자열" />);
    expect(text()).toContain("그냥 문자열");
  });
});

describe("FriendlySummary", () => {
  it("says an empty list is empty and lists records as entities", () => {
    const empty = renderIn(<FriendlySummary value={[]} />);
    expect(text()).toContain(t("ko", "ui.empty.listDetail"));
    empty.unmount();

    renderIn(<FriendlySummary value={[{ title: "첫째" }]} />);
    expect(text()).toContain("첫째");
  });

  it("reads scalar list entries as words, not as tokens", () => {
    renderIn(<FriendlySummary value={["agent:researcher", "tool:web_search", "a", "b", "c", "d", "e"]} />);
    expect(text()).toContain("Researcher");
    expect(text()).toContain("Web Search");
    expect(text()).toContain("+1");
  });

  it("leaves paths, addresses and filenames alone", () => {
    renderIn(<FriendlySummary value={["/var/data", "me@example.com", "notes.md", null]} />);
    expect(text()).toContain("/var/data");
    expect(text()).toContain("me@example.com");
    expect(text()).toContain("notes.md");
    expect(text()).toContain("-");
  });

  it("prefers the first list inside an object over the object itself", () => {
    renderIn(<FriendlySummary value={{ meta: 1, documents: [{ name: "노트" }] }} />);
    expect(text()).toContain("노트");
  });

  it("counts nested collections and hides internals when there is no list", () => {
    renderIn(<FriendlySummary value={{
      api_key: "sk-secret",
      tags: ["a", "b", "c"],
      profile: { name: "x" },
      state: "ready",
    }} />);
    expect(text()).not.toContain("sk-secret");
    expect(text()).toContain("3 items");
    expect(text()).toContain("available");
  });

  it("reads a bare scalar as a word", () => {
    renderIn(<FriendlySummary value="agent:researcher" />);
    expect(text()).toContain("Researcher");
  });
});

describe("OperationResult", () => {
  it("draws nothing at all before anything has been asked for", () => {
    const { container } = renderIn(<OperationResult />);
    expect(container.textContent).toBe("");
  });

  it("names a failure, falling back to the payload when there is no message", () => {
    const withError = renderIn(
      <OperationResult result={{ ok: false, status: 503, source: "unavailable", data: null, error: "정지됨" }} />,
    );
    expect(text()).toContain(t("ko", "ui.requestUnavailable"));
    expect(text()).toContain("정지됨");
    withError.unmount();

    renderIn(<OperationResult result={{ ok: false, status: 500, source: "unavailable", data: { status: "터졌어요" } }} />);
    expect(text()).toContain("터졌어요");
  });

  it("uses the caller's success label, or its own, and matches the mode", () => {
    const labelled = renderIn(
      <OperationResult result={{ ok: true, status: 200, source: "live", data: { status: "ready" } }} successLabel="설치 완료" />,
    );
    expect(text()).toContain("설치 완료");
    expect(text()).toContain(t("ko", "ui.field.status"));
    labelled.unmount();

    renderIn(
      <OperationResult result={{ ok: true, status: 200, source: "live", data: { documents: [{ name: "노트" }] } }} />,
      { mode: "basic" },
    );
    expect(text()).toContain(t("ko", "ui.requestCompleted"));
    expect(text()).toContain("노트");
  });
});

describe("EntityList", () => {
  it("says an absent list is empty", () => {
    renderIn(<EntityList items={null} />);
    expect(text()).toContain(t("ko", "ui.empty.listDetail"));
  });

  it("prefers the localized role copy over whatever the server called it", () => {
    // The registry ships English role names; a Korean reader must not be shown
    // "Researcher" beside a Korean sentence.
    renderIn(<EntityList items={[{ type: "agent:researcher", id: "a1", name: "Researcher" }]} labelPrefix="act.agentRole" />);
    expect(text()).toContain(t("ko", "act.agentRole.researcher"));
    expect(text()).toContain(t("ko", "act.agentRole.researcher.detail"));
    // A labelled row is already named by its role, so no type badge repeats it.
    expect(text()).not.toContain("Researcher");
  });

  it("falls back to the server's own copy for a role it has no words for", () => {
    renderIn(<EntityList items={[{ id: "unknown-role", name: "Free agent", summary: "제멋대로" }]} labelPrefix="act.agentRole" />);
    expect(text()).toContain("Free agent");
    expect(text()).toContain("제멋대로");
  });

  it("translates a known entity type and titleizes an unknown one", () => {
    const known = renderIn(<EntityList items={[{ name: "행", type: "Task" }]} metaKey="type" />);
    expect(text()).toContain(t("ko", "ui.entity.Task"));
    known.unmount();

    renderIn(<EntityList items={[{ name: "행", type: "custom_state" }]} metaKey="type" />);
    expect(text()).toContain("Custom State");
  });

  it("drops a badge that only repeats the row's own name", () => {
    const { container } = renderIn(<EntityList items={[{ name: "Custom State", type: "custom_state" }]} metaKey="type" />);
    // Saying "Custom State — Custom State" reads as a rendering fault, not as
    // a category.
    expect(container.querySelector(".entity-list-row")?.textContent).toBe("Custom State");
  });

  it("numbers a row that has no name of any kind, and never shows an id in basic mode", () => {
    renderIn(<EntityList items={[{ id: "row-id-1" }]} />, { mode: "basic" });
    expect(text()).toContain("#1");
    expect(text()).not.toContain("row-id-1");
  });

  it("shows the id under the name in advanced mode", () => {
    renderIn(<EntityList items={[{ id: "row-id-1", name: "이름" }]} />);
    expect(text()).toContain("row-id-1");
  });

  it("does not repeat the title as the detail", () => {
    const { container } = renderIn(<EntityList items={[{ name: "같은 말", summary: "같은 말", description: "다른 말" }]} />);
    expect(container.querySelectorAll("p")).toHaveLength(1);
    expect(container.querySelector("p")?.textContent).toBe("다른 말");
  });

  it("uses the path when there is no summary or description", () => {
    renderIn(<EntityList items={[{ name: "노트", path: "/vault/notes.md" }]} />);
    expect(text()).toContain("/vault/notes.md");
  });

  it("shows no detail paragraph when the row has nothing to add", () => {
    const { container } = renderIn(<EntityList items={[{ name: "이름만" }]} />);
    expect(container.querySelectorAll("p")).toHaveLength(0);
  });
});

describe("ModeGate", () => {
  it("offers advanced mode by default and switches on click", () => {
    renderIn(<ModeGate />, { mode: "basic" });
    expect(text()).toContain(t("ko", "ui.modeGate.title"));
    expect(text()).toContain(t("ko", "ui.modeGate.detail"));

    fireEvent.click(screen.getByRole("button", { name: t("ko", "ui.modeGate.advanced") }));

    expect(useAppStore.getState().mode).toBe("advanced");
  });

  it("takes the caller's copy and can aim at the admin console", () => {
    renderIn(<ModeGate title="관리자 전용" detail="여기는 관리자만" target="admin" />, { mode: "basic" });
    expect(text()).toContain("관리자 전용");
    expect(text()).toContain("여기는 관리자만");

    fireEvent.click(screen.getByRole("button", { name: t("ko", "ui.modeGate.admin") }));

    expect(useAppStore.getState().mode).toBe("admin");
  });
});

describe("ActionButton beyond the happy path", () => {
  it("says the service is unavailable when the failure carried no message", async () => {
    renderIn(
      <ActionButton label="실행" action={async () => ({ ok: false, status: 500, data: {}, source: "unavailable" })} />,
    );
    await userEvent.click(screen.getByRole("button", { name: "실행" }));
    expect(await screen.findByText(t("ko", "ui.status.unavailable"))).toBeTruthy();
  });

  it("shows a spinner while the action runs and reports the default success copy", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const { container } = renderIn(
      <ActionButton
        label="실행"
        action={async () => {
          await gate;
          return { ok: true, status: 200, data: {}, source: "live" };
        }}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "실행" }));
    expect(container.querySelector(".animate-spin")).toBeTruthy();

    release();
    expect(await screen.findByText(t("ko", "ui.done"))).toBeTruthy();
  });
});

describe("Tabs", () => {
  const TABS = [
    { id: "a", label: "첫째" },
    { id: "b", label: "둘째" },
    { id: "c", label: "셋째" },
  ];

  function renderTabs(value = "b") {
    const onChange = vi.fn();
    const view = renderIn(<Tabs tabs={TABS} value={value} onChange={onChange} ariaLabel="구역" />);
    return { onChange, view, buttons: screen.getAllByRole("tab") };
  }

  it("selects on click", async () => {
    const { onChange, buttons } = renderTabs();
    await userEvent.click(buttons[2]);
    expect(onChange).toHaveBeenCalledWith("c");
  });

  it("walks forward and wraps with the arrow keys", () => {
    const { onChange, buttons } = renderTabs();
    fireEvent.keyDown(buttons[0], { key: "ArrowRight" });
    expect(onChange).toHaveBeenCalledWith("b");
    expect(document.activeElement).toBe(buttons[1]);

    fireEvent.keyDown(buttons[2], { key: "ArrowDown" });
    expect(onChange).toHaveBeenCalledWith("a");
    expect(document.activeElement).toBe(buttons[0]);
  });

  it("walks backward and wraps with the arrow keys", () => {
    const { onChange, buttons } = renderTabs();
    fireEvent.keyDown(buttons[0], { key: "ArrowLeft" });
    expect(onChange).toHaveBeenCalledWith("c");
    expect(document.activeElement).toBe(buttons[2]);

    fireEvent.keyDown(buttons[1], { key: "ArrowUp" });
    expect(onChange).toHaveBeenCalledWith("a");
  });

  it("jumps to the ends with Home and End", () => {
    const { onChange, buttons } = renderTabs();
    fireEvent.keyDown(buttons[1], { key: "Home" });
    expect(onChange).toHaveBeenCalledWith("a");
    expect(document.activeElement).toBe(buttons[0]);

    fireEvent.keyDown(buttons[1], { key: "End" });
    expect(onChange).toHaveBeenCalledWith("c");
    expect(document.activeElement).toBe(buttons[2]);
  });

  it("leaves every other key to the browser", () => {
    const { onChange, buttons } = renderTabs();
    fireEvent.keyDown(buttons[1], { key: "x" });
    expect(onChange).not.toHaveBeenCalled();
  });

  it("stays reachable from the keyboard when none of its tabs is selected", () => {
    // One group of a grouped switcher holds no selection; a roving tabindex
    // would otherwise put every button at -1 and drop the whole group out of
    // the Tab order.
    const { buttons } = renderTabs("not-in-this-group");
    expect(buttons.map((button) => button.getAttribute("tabindex"))).toEqual(["0", "-1", "-1"]);
  });

  it("survives a tab button that has already unmounted", () => {
    expect(() => focusTabButton(null)).not.toThrow();
  });
});
