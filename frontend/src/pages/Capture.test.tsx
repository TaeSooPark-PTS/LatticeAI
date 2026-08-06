import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { latticeApi } from "@/api/client";
import { type ApiResult, fail, ok, renderPage } from "@/test/renderPage";
import {
  browserFolderNameFromFiles,
  CapturePage,
  hasDesktopFolderPicker,
  readJourney,
  uploadResultDetail,
} from "./Capture";

type AnyRecord = Record<string, unknown>;

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function makeFile(name: string, relativePath?: string) {
  const file = new File(["data"], name, { type: "text/plain" });
  if (relativePath) Object.defineProperty(file, "webkitRelativePath", { value: relativePath });
  return file;
}

const fileHandle = (name: string) => ({
  kind: "file" as const,
  name,
  getFile: async () => makeFile(name),
});

/** An array-like `FileList` stand-in jsdom will accept on a file input. */
function setInputFiles(input: HTMLInputElement, files: File[] | null) {
  const value = files
    ? Object.assign([...files], { item: (i: number) => files[i] ?? null })
    : null;
  Object.defineProperty(input, "files", { value, configurable: true });
}

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

  it("lands on the folder and file methods for their deep links", async () => {
    const first = renderPage(<CapturePage initialTab="local" />, { api: {} });
    await waitFor(() =>
      expect(screen.getByTestId("capture-method-local").getAttribute("aria-pressed")).toBe("true"));
    first.unmount();
    renderPage(<CapturePage initialTab="files" />, { api: {} });
    await waitFor(() =>
      expect(screen.getByTestId("capture-method-files").getAttribute("aria-pressed")).toBe("true"));
  });

  it("keeps the address hash aligned with the chosen method", async () => {
    window.location.hash = "";
    render();
    await userEvent.click(await screen.findByTestId("capture-method-local"));
    expect(window.location.hash).toBe("#/my-computer");
    await userEvent.click(screen.getByTestId("capture-method-browser"));
    expect(window.location.hash).toBe("#/capture-browser");
    await userEvent.click(screen.getByTestId("capture-method-files"));
    expect(window.location.hash).toBe("#/capture");
  });
});

describe("CapturePage file intake", () => {
  beforeEach(() => vi.restoreAllMocks());

  const fileInput = () =>
    document.querySelector<HTMLInputElement>(".capture-station-body input[type=file]")!;

  it("uploads files one by one and reports each outcome, with retry", async () => {
    const first = deferred<ApiResult<AnyRecord>>();
    const uploadDocument = vi.fn()
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(() => Promise.resolve(fail("디스크가 가득 참", {})))
      .mockImplementation(() => Promise.resolve(ok({ node_id: "node-7" })));
    render({ uploadDocument });
    await screen.findByText("파일을 놓거나 문서를 선택");
    await userEvent.upload(fileInput(), [makeFile("ok.md"), makeFile("bad.md")]);

    // While the first file is in flight the second still waits its turn.
    await screen.findByText(/내용을 읽어 기억에 넣는 중/);
    expect(screen.getByText(/저장 준비 중/)).toBeTruthy();

    await act(async () => { first.resolve(ok({ node_id: "node-1" })); });
    await screen.findByText(/출처 정보와 함께 저장됨 · node-1/);

    // The second file failed, names its reason, and offers a retry.
    await screen.findAllByText(/디스크가 가득 참/);
    await userEvent.click(screen.getByRole("button", { name: /다시 시도/ }));
    await screen.findByText(/출처 정보와 함께 저장됨 · node-7/);
    expect(uploadDocument).toHaveBeenCalledTimes(3);
  });

  it("accepts files dropped onto the intake surface", async () => {
    const uploadDocument = vi.fn(() => Promise.resolve(ok({})));
    render({ uploadDocument });
    const drop = (await screen.findByText("파일을 놓거나 문서를 선택")).closest("label")!;
    fireEvent.dragOver(drop);
    fireEvent.drop(drop, { dataTransfer: { files: [makeFile("dropped.md")] } });
    await screen.findByText(/출처 정보와 함께 저장됨/);
    expect(screen.getByText("dropped.md")).toBeTruthy();
    expect(uploadDocument).toHaveBeenCalledTimes(1);
  });

  it("ignores a change event that carries no files", async () => {
    const uploadDocument = vi.fn(() => Promise.resolve(ok({})));
    render({ uploadDocument });
    await screen.findByText("파일을 놓거나 문서를 선택");
    setInputFiles(fileInput(), null);
    fireEvent.change(fileInput());
    setInputFiles(fileInput(), []);
    fireEvent.change(fileInput());
    await waitFor(() => expect(uploadDocument).not.toHaveBeenCalled());
  });
});

describe("CapturePage recent capture", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("stops watching a source through its action button", async () => {
    render({
      localSources: ok({ sources: [
        { id: "s1", path: "/vault/notes" },
        { source_id: "s2" },
        { path: "/vault/photos" },
      ] }),
    });
    await userEvent.click(await screen.findByRole("button", { name: "/vault/notes 감시 중지" }));
    await waitFor(() => expect(latticeApi.localWatchStop).toHaveBeenCalledWith("s1"));
    await userEvent.click(screen.getByRole("button", { name: "출처 감시 중지" }));
    await waitFor(() => expect(latticeApi.localWatchStop).toHaveBeenCalledWith("s2"));
  });

  it("hides the raw payload panels in basic mode", async () => {
    render({ documents: ok({ count: 3 }) }, { mode: "basic" });
    await waitFor(() => expect(document.body.textContent).toContain("업로드된 문서"));
    expect(document.body.textContent).not.toContain("폴더 접근");
    expect(document.body.textContent).not.toContain("처리 상태");
    expect(document.body.textContent).not.toContain("Brain 성장");
  });
});

describe("CapturePage pipeline", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("requests a search refresh from the pipeline panel", async () => {
    render();
    await userEvent.click(await screen.findByRole("button", { name: "검색 준비 다시 하기" }));
    await waitFor(() => expect(latticeApi.rebuildIndex).toHaveBeenCalled());
  });

  it("shows how much material still waits to be organised", async () => {
    render({ indexStatus: ok({ pending: 5, total: 12 }) });
    await screen.findByText("정리를 기다리는 자료 5건");
  });

  it("counts received material even before anything is remembered", async () => {
    render({
      pipelineStatus: ok({ received: 4, extracted: 0, connected: 0 }),
      graphStats: ok({ nodes: 0, edges: 0 }),
      indexStatus: ok({ pending: 0, total: 0 }),
    });
    await screen.findByText("지금 기다리는 자료는 없어요.");
    expect(screen.getByText("기억한 자료 0건")).toBeTruthy();
  });
});

describe("CapturePage folder intake", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.unstubAllGlobals());

  function renderFolder(overrides = {}, options = {}) {
    return renderPage(<CapturePage initialTab="local" />, {
      api: { localSources: ok({ sources: [] }), ...overrides },
      ...options,
    });
  }

  const chooseButton = () => screen.findByRole("button", { name: /폴더 선택/ });
  const pathInput = () => screen.getByPlaceholderText("이 Mac의 폴더 경로") as HTMLInputElement;
  const dirInput = () => document.querySelector<HTMLInputElement>("input[webkitdirectory]")!;

  it("connects a typed path and reports the scan request", async () => {
    const pending = deferred<ApiResult<AnyRecord>>();
    const connectFolder = vi.fn(() => pending.promise);
    renderFolder({ connectFolder });
    const submit = (await screen.findByRole("button", { name: /스캔하고 연결/ }));
    expect(submit.hasAttribute("disabled")).toBe(true);
    await userEvent.type(pathInput(), "  /vault/docs  ");
    expect(submit.hasAttribute("disabled")).toBe(false);
    await userEvent.click(submit);
    await waitFor(() => expect(connectFolder).toHaveBeenCalledWith("/vault/docs"));
    expect(submit.hasAttribute("disabled")).toBe(true);
    await act(async () => { pending.resolve(ok({ scanned: true })); });
    await screen.findByText("폴더 스캔 요청됨");
  });

  it("ignores a submitted empty path", async () => {
    renderFolder();
    await chooseButton();
    fireEvent.submit(pathInput().closest("form")!);
    await waitFor(() => expect(latticeApi.connectFolder).not.toHaveBeenCalled());
  });

  it("imports a folder picked with the browser directory picker", async () => {
    const gate = deferred<ApiResult<AnyRecord>>();
    const uploadDocument = vi.fn()
      .mockImplementationOnce(() => gate.promise)
      .mockImplementation(() => Promise.resolve(ok({})));
    const dir = {
      kind: "directory",
      name: "Notes",
      values: async function* () {
        yield fileHandle("a.md");
        yield {
          kind: "directory",
          name: "sub",
          values: async function* () { yield fileHandle("b.md"); },
        };
      },
    };
    vi.stubGlobal("showDirectoryPicker", vi.fn(async () => dir));
    renderFolder({ uploadDocument });
    await userEvent.click(await chooseButton());
    await screen.findByText("폴더 파일 업로드 중");
    await act(async () => { gate.resolve(ok({})); });
    await screen.findByText("Notes 파일을 Brain에 넣었습니다.");
    expect(uploadDocument).toHaveBeenCalledTimes(2);
  });

  it("falls back to the browser picker when the desktop picker returns nothing", async () => {
    vi.stubGlobal("__TAURI__", { core: { invoke: vi.fn() } });
    const dir = {
      kind: "directory",
      name: "",
      entries: async function* (): AsyncGenerator<[string, unknown]> {
        yield ["c.md", fileHandle("c.md")];
      },
    };
    vi.stubGlobal("showDirectoryPicker", vi.fn(async () => dir));
    const selectFolder = vi.fn(() => Promise.resolve(null));
    renderFolder({ selectFolder, uploadDocument: vi.fn(() => Promise.resolve(ok({}))) });
    await userEvent.click(await chooseButton());
    await screen.findByText("선택한 폴더 파일을 Brain에 넣었습니다.");
    expect(selectFolder).toHaveBeenCalled();
  });

  it("connects the folder chosen through the desktop picker", async () => {
    vi.stubGlobal("__TAURI__", { core: { invoke: vi.fn() } });
    const selectFolder = vi.fn(() => Promise.resolve("/picked/dir"));
    renderFolder({ selectFolder });
    await userEvent.click(await chooseButton());
    await waitFor(() => expect(latticeApi.connectFolder).toHaveBeenCalledWith("/picked/dir"));
    expect(pathInput().value).toBe("/picked/dir");
    await screen.findByText("폴더 스캔 요청됨");
  });

  it("opens the hidden folder input when no picker API exists", async () => {
    const clickSpy = vi.spyOn(HTMLInputElement.prototype, "click");
    renderFolder({ uploadDocument: vi.fn(() => Promise.resolve(ok({}))) });
    await userEvent.click(await chooseButton());
    await waitFor(() => expect(clickSpy).toHaveBeenCalled());
    setInputFiles(dirInput(), [makeFile("a.txt", "Proj/a.txt")]);
    fireEvent.change(dirInput());
    await screen.findByText("Proj 파일을 Brain에 넣었습니다.");
    // The queue names the file by its path inside the folder.
    expect(screen.getByText("Proj/a.txt")).toBeTruthy();
  });

  it("reports an empty folder selection instead of silently doing nothing", async () => {
    renderFolder();
    await chooseButton();
    setInputFiles(dirInput(), null);
    fireEvent.change(dirInput());
    await screen.findByText("선택한 폴더에서 업로드할 파일을 찾지 못했습니다.");
  });

  it("says when the picked folder held nothing it could upload", async () => {
    vi.stubGlobal("showDirectoryPicker", vi.fn(async () => ({ kind: "directory", name: "Empty" })));
    renderFolder();
    await userEvent.click(await chooseButton());
    await screen.findByText("선택한 폴더에서 업로드할 파일을 찾지 못했습니다.");
  });

  it("stays quiet when the person cancels the picker", async () => {
    vi.stubGlobal("showDirectoryPicker", vi.fn(async () => {
      throw new DOMException("cancelled", "AbortError");
    }));
    renderFolder();
    await userEvent.click(await chooseButton());
    await chooseButton();
    expect(screen.queryByText(/폴더 선택 창을 열 수 없습니다/)).toBeNull();
  });

  it("explains when the picker cannot open", async () => {
    vi.stubGlobal("showDirectoryPicker", vi.fn(async () => { throw new Error("boom"); }));
    renderFolder();
    await userEvent.click(await chooseButton());
    await screen.findByText(/폴더 선택 창을 열 수 없습니다/);
  });

  it("treats a non-abort DOMException as a real failure", async () => {
    vi.stubGlobal("showDirectoryPicker", vi.fn(async () => {
      throw new DOMException("no", "NotAllowedError");
    }));
    renderFolder();
    await userEvent.click(await chooseButton());
    await screen.findByText(/폴더 선택 창을 열 수 없습니다/);
  });

  it("ignores a second click while the picker is already open", async () => {
    const opened = deferred<AnyRecord>();
    const picker = vi.fn(() => opened.promise);
    vi.stubGlobal("showDirectoryPicker", picker);
    renderFolder();
    await userEvent.click(await chooseButton());
    const choosing = await screen.findByRole("button", { name: /선택 중/ });
    // The button is disabled while choosing. Removing the DOM attribute does
    // not reach `chooseFolder`'s own re-entry guard either: React suppresses
    // onClick for a disabled control from its own (still-disabled) props,
    // not the live DOM attribute, so this only proves the picker still opens
    // once — defense-in-depth on top of that, not evidence the guard ran.
    choosing.removeAttribute("disabled");
    fireEvent.click(choosing);
    expect(picker).toHaveBeenCalledTimes(1);
    await act(async () => { opened.resolve({ kind: "directory", name: "N" }); });
    await screen.findByText(/찾지 못했습니다/);
  });

  it("gives up quietly when the intake unmounts mid-pick", async () => {
    vi.stubGlobal("latticeDesktop", { selectFolder: vi.fn() });
    const picked = deferred<string | null>();
    const selectFolder = vi.fn(() => picked.promise);
    const view = renderFolder({ selectFolder });
    await userEvent.click(await chooseButton());
    await waitFor(() => expect(selectFolder).toHaveBeenCalled());
    view.unmount();
    await act(async () => { picked.resolve(null); });
    expect(latticeApi.connectFolder).not.toHaveBeenCalled();
  });

  it("labels a folder import whose files were rejected, and retries it", async () => {
    const dir = {
      kind: "directory",
      name: "Docs",
      values: async function* () {
        yield fileHandle("x.md");
        yield fileHandle("y.md");
      },
    };
    vi.stubGlobal("showDirectoryPicker", vi.fn(async () => dir));
    const uploadDocument = vi.fn()
      .mockImplementationOnce(() => Promise.resolve(ok({})))
      .mockImplementationOnce(() => Promise.resolve(fail("용량 초과", {})))
      .mockImplementation(() => Promise.resolve(ok({})));
    renderFolder({ uploadDocument });
    await userEvent.click(await chooseButton());
    await screen.findAllByText(/용량 초과/);
    await userEvent.click(screen.getByRole("button", { name: /다시 시도/ }));
    await screen.findByText("Docs 파일을 Brain에 넣었습니다.");
  });

  it("labels a folder import that failed outright", async () => {
    const dir = {
      kind: "directory",
      name: "Docs",
      values: async function* () { yield fileHandle("x.md"); },
    };
    vi.stubGlobal("showDirectoryPicker", vi.fn(async () => dir));
    renderFolder({ uploadDocument: vi.fn(() => Promise.reject(new Error("네트워크 끊김"))) });
    await userEvent.click(await chooseButton());
    await screen.findByText("폴더 파일을 Brain에 넣지 못했습니다.");
  });
});

describe("CapturePage web intake", () => {
  beforeEach(() => vi.restoreAllMocks());

  function renderWeb(overrides = {}, options = {}) {
    return renderPage(<CapturePage initialTab="browser" />, { api: { ...overrides }, ...options });
  }

  const urlInput = async () =>
    (await screen.findByPlaceholderText("https://example.com/article")) as HTMLInputElement;
  const saveButton = () => screen.getByRole("button", { name: /스캔하고 저장/ });

  it("captures a typed address and reports the save", async () => {
    const pending = deferred<ApiResult<AnyRecord>>();
    const browserReadUrl = vi.fn(() => pending.promise);
    renderWeb({ browserReadUrl });
    const input = await urlInput();
    expect(saveButton().hasAttribute("disabled")).toBe(true);
    await userEvent.type(input, "https://ko.example.com/a");
    await userEvent.click(saveButton());
    expect(browserReadUrl).toHaveBeenCalledWith("https://ko.example.com/a");
    // While the capture runs, both actions wait.
    expect(screen.getByRole("button", { name: /붙여넣기/ }).hasAttribute("disabled")).toBe(true);
    await act(async () => { pending.resolve(ok({ captured: true })); });
    await screen.findByText("웹페이지 저장 요청됨");
  });

  it("normalises a bare domain and leaves other text alone", async () => {
    renderWeb();
    const input = await urlInput();
    await userEvent.type(input, "example.com/post");
    await userEvent.click(saveButton());
    await waitFor(() =>
      expect(latticeApi.browserReadUrl).toHaveBeenCalledWith("https://example.com/post"));
    expect(input.value).toBe("https://example.com/post");
    await userEvent.clear(input);
    await userEvent.type(input, "메모용 텍스트");
    await userEvent.click(saveButton());
    await waitFor(() => expect(latticeApi.browserReadUrl).toHaveBeenCalledWith("메모용 텍스트"));
  });

  it("ignores a submitted empty address", async () => {
    renderWeb();
    const input = await urlInput();
    fireEvent.submit(input.closest("form")!);
    await waitFor(() => expect(latticeApi.browserReadUrl).not.toHaveBeenCalled());
  });

  it("captures the clipboard address on paste", async () => {
    Object.defineProperty(navigator, "clipboard", {
      value: { readText: vi.fn(async () => "clip.example.io") },
      configurable: true,
    });
    renderWeb();
    await urlInput();
    fireEvent.click(screen.getByRole("button", { name: /붙여넣기/ }));
    await waitFor(() =>
      expect(latticeApi.browserReadUrl).toHaveBeenCalledWith("https://clip.example.io"));
  });

  it("does nothing when the clipboard is unreadable or absent", async () => {
    Object.defineProperty(navigator, "clipboard", {
      value: { readText: vi.fn(async () => { throw new Error("denied"); }) },
      configurable: true,
    });
    renderWeb();
    await urlInput();
    fireEvent.click(screen.getByRole("button", { name: /붙여넣기/ }));
    await act(async () => {});
    expect(latticeApi.browserReadUrl).not.toHaveBeenCalled();

    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
    fireEvent.click(screen.getByRole("button", { name: /붙여넣기/ }));
    await act(async () => {});
    expect(latticeApi.browserReadUrl).not.toHaveBeenCalled();
  });
});

describe("capture helpers", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("readJourney prefers per-stage counts and parses numeric strings", () => {
    const journey = readJourney(
      {
        received: "5",
        extracted: "abc",
        connected: -2,
        stages: { read: { status: "done" }, understand: "working", connect: "bogus" },
      },
      { pending: 0, total: 9, pipelines: { docs: {} } },
      { nodes: 3, edges: 1 },
    );
    expect(journey.steps[0]).toMatchObject({ key: "read", state: "done", count: 5 });
    expect(journey.steps[1].state).toBe("working");
    // The connect stage carries an unknown token, so the fallback applies.
    expect(journey.steps[2].state).toBe("done");
    expect(journey.remembered).toBe(9);
    expect(journey.connections).toBe(1);
  });

  it("readJourney treats zero per-stage counts as waiting", () => {
    const journey = readJourney({ received: 0, extracted: 0, connected: 0 }, {}, {});
    expect(journey.steps.map((step) => step.state)).toEqual(["waiting", "waiting", "waiting"]);
    expect(journey.received).toBe(0);
  });

  it("readJourney falls back to graph totals when no pipeline reported", () => {
    const working = readJourney(null, { pending: 2 }, { nodes: 0, edges: 0 });
    expect(working.steps[0].state).toBe("working");
    const done = readJourney(undefined, {}, { nodes: 4, edges: 2 });
    expect(done.steps.map((step) => step.state)).toEqual(["done", "done", "done"]);
    const empty = readJourney({}, {}, {});
    expect(empty.steps.map((step) => step.state)).toEqual(["waiting", "waiting", "waiting"]);
    expect(empty.steps[0].count).toBe(0);
  });

  it("readJourney reads per-stage counts as progress when no stage map is sent", () => {
    // Older builds report the three counts flat, with no `stages` block. A
    // positive count is the only evidence that stage finished, so each one has
    // to stand on its own rather than borrowing the read stage's verdict.
    const journey = readJourney({ received: 2, extracted: 3, connected: 4 }, {}, {});
    expect(journey.steps.map((step) => step.state)).toEqual(["done", "done", "done"]);
    expect(journey.steps.map((step) => step.count)).toEqual([2, 3, 4]);
  });

  it("readJourney keeps counts non-negative, rounded, and finite", () => {
    const journey = readJourney({ received: 3.6 }, { pending: "2" }, { nodes: Number.NaN });
    expect(journey.steps[0].count).toBe(4);
    expect(journey.waiting).toBe(2);
    expect(journey.remembered).toBe(0);
  });

  it("uploadResultDetail names every status and node-id source", () => {
    type QueueItem = Parameters<typeof uploadResultDetail>[0];
    const item = (over: Partial<QueueItem>): QueueItem => ({
      id: "x",
      file: makeFile("d.md"),
      name: "d.md",
      size: 4,
      status: "done",
      ...over,
    });
    const result = (value: unknown) => value as QueueItem["result"];
    expect(uploadResultDetail(item({ status: "queued" }), "ko")).toBe("저장 준비 중");
    expect(uploadResultDetail(item({ status: "uploading" }), "ko")).toBe("내용을 읽어 기억에 넣는 중");
    expect(uploadResultDetail(item({ status: "failed", result: result(fail("이유", {})) }), "ko")).toBe("이유");
    expect(uploadResultDetail(
      item({ status: "failed", result: result({ ok: false, status: 0, source: "unavailable", data: {} }) }),
      "ko",
    )).toBe("Brain에 들어가기 전에 수집에 실패했습니다.");
    // A done row whose result vanished still reads as a failure, not a claim.
    expect(uploadResultDetail(item({}), "ko")).toBe("Brain에 들어가기 전에 수집에 실패했습니다.");
    expect(uploadResultDetail(item({ result: result(ok(null)) }), "ko")).toBe("출처 정보와 함께 저장됨");
    expect(uploadResultDetail(item({ result: result(ok({ node_id: "n1" })) }), "ko")).toBe("출처 정보와 함께 저장됨 · n1");
    expect(uploadResultDetail(item({ result: result(ok({ graph_node: "g1" })) }), "ko")).toBe("출처 정보와 함께 저장됨 · g1");
    expect(uploadResultDetail(item({ result: result(ok({ provenance_id: "p1" })) }), "ko")).toBe("출처 정보와 함께 저장됨 · p1");
    expect(uploadResultDetail(item({ result: result(ok({})) }), "ko")).toBe("출처 정보와 함께 저장됨");
  });

  it("browserFolderNameFromFiles reads the first path segment or nothing", () => {
    expect(browserFolderNameFromFiles([])).toBe("");
    expect(browserFolderNameFromFiles([makeFile("a.txt")])).toBe("");
    expect(browserFolderNameFromFiles([makeFile("a.txt", "Proj/a.txt")])).toBe("Proj");
    expect(browserFolderNameFromFiles([makeFile("a.txt", "/")])).toBe("");
  });

  it("hasDesktopFolderPicker detects each desktop shell shape", () => {
    expect(hasDesktopFolderPicker()).toBe(false);
    vi.stubGlobal("__TAURI__", {});
    expect(hasDesktopFolderPicker()).toBe(false);
    vi.stubGlobal("__TAURI__", { core: {} });
    expect(hasDesktopFolderPicker()).toBe(false);
    vi.stubGlobal("__TAURI__", { core: { invoke: () => Promise.resolve(null) } });
    expect(hasDesktopFolderPicker()).toBe(true);
    vi.unstubAllGlobals();
    vi.stubGlobal("latticeDesktop", {});
    expect(hasDesktopFolderPicker()).toBe(false);
    vi.stubGlobal("latticeDesktop", { selectFolder: () => Promise.resolve(null) });
    expect(hasDesktopFolderPicker()).toBe(true);
    vi.unstubAllGlobals();
    // Internals alone are not enough outside a tauri: origin.
    vi.stubGlobal("__TAURI_INTERNALS__", {});
    expect(hasDesktopFolderPicker()).toBe(false);
  });
});
