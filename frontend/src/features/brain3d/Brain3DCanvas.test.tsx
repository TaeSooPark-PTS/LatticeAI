import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { t } from "@/i18n";
import "@/i18n/brain";
import { useAppStore } from "@/store/appStore";
import { gpu, resetGpu } from "@/test/threeHarness";
import { Brain3DCanvas, CONTEXT_RESTORE_GRACE_MS } from "./Brain3DCanvas";
import { Brain3DSceneController } from "./Brain3DScene";
import { resetWebGLSupportCacheForTest } from "./webglSupport";

vi.mock("three", async (importOriginal) => (await import("@/test/threeHarness")).mockThree(await importOriginal()));
vi.mock("three/examples/jsm/postprocessing/EffectComposer.js", async () => (await import("@/test/threeHarness")).composerModule);
vi.mock("three/examples/jsm/postprocessing/RenderPass.js", async () => (await import("@/test/threeHarness")).renderPassModule);
vi.mock("three/examples/jsm/postprocessing/UnrealBloomPass.js", async () => (await import("@/test/threeHarness")).bloomModule);

const nodes = [
  { id: "n1", label: "Atlas", type: "Document" },
  { id: "n2", label: "Lattice", type: "Concept" },
];
const edges = [{ source: "n1", target: "n2" }];

function enableWebGL() {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({} as never);
}

beforeEach(() => {
  resetGpu();
  resetWebGLSupportCacheForTest();
  useAppStore.setState({ theme: "light" });
});

afterEach(() => {
  resetWebGLSupportCacheForTest();
  vi.restoreAllMocks();
});

describe("Brain3DCanvas without WebGL", () => {
  beforeEach(() => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
  });

  it("renders the custom fallback", () => {
    render(<Brain3DCanvas nodes={nodes} edges={edges} language="en" fallback={<div data-testid="custom-2d-fallback" />} />);
    expect(screen.getByTestId("custom-2d-fallback")).toBeTruthy();
  });

  it("explains itself in the explorer and stays silent as an ambient backdrop", () => {
    const view = render(<Brain3DCanvas nodes={nodes} edges={[]} language="en" className="extra" />);
    const explorer = screen.getByTestId("brain-graph-3d-fallback");
    expect(explorer.textContent).toBe(t("en", "graph.canvas3d.fallback"));
    expect(explorer.className).toBe("brain-3d-fallback extra");
    view.unmount();

    render(<Brain3DCanvas nodes={nodes} edges={edges} language="en" variant="ambient" />);
    const ambient = screen.getByTestId("brain-ambient-3d-fallback");
    expect(ambient.textContent).toBe("");
    expect(ambient.getAttribute("aria-hidden")).toBe("true");
  });

  it("never builds a scene", () => {
    render(<Brain3DCanvas nodes={nodes} edges={edges} language="en" resetSignal={1} />);
    expect(gpu.renderers).toHaveLength(0);
  });
});

describe("Brain3DCanvas with WebGL", () => {
  beforeEach(enableWebGL);

  it("falls back when the renderer itself cannot be created", () => {
    gpu.failRenderer = true;
    render(<Brain3DCanvas nodes={nodes} edges={edges} language="en" />);
    expect(screen.getByTestId("brain-graph-3d-fallback")).toBeTruthy();
  });

  it("drives the scene from its props and disposes it on unmount", () => {
    const proto = Brain3DSceneController.prototype;
    const update = vi.spyOn(proto, "updateGraph");
    const select = vi.spyOn(proto, "setSelection");
    const firing = vi.spyOn(proto, "setFiring");
    const reset = vi.spyOn(proto, "resetCamera");
    const theme = vi.spyOn(proto, "setTheme");
    const trace = { nodeIds: ["n1"], edges: [], timestamp: 1 };

    const view = render(<Brain3DCanvas nodes={nodes} edges={edges} language="en" />);
    const host = screen.getByRole("region", { name: t("en", "graph.canvas3d.aria") });
    expect(host.querySelector("canvas")).toBeTruthy();
    expect(host.getAttribute("data-nodes")).toBe("2");
    expect(host.getAttribute("data-edges")).toBe("1");
    expect(host.textContent).toContain(t("en", "graph.hemisphere.left"));
    expect(update.mock.calls.at(-1)![0].map((point) => point.id)).toEqual(["n1", "n2"]);
    expect(select).toHaveBeenLastCalledWith(null);
    expect(firing).toHaveBeenLastCalledWith(null, 0.65);
    expect(reset).not.toHaveBeenCalled();

    view.rerender(
      <Brain3DCanvas nodes={nodes} edges={edges} language="en" selectedId="n2" firingTrace={trace} intensity={1} resetSignal={1} showSuperseded={false} />,
    );
    expect(select).toHaveBeenLastCalledWith("n2");
    expect(firing).toHaveBeenLastCalledWith(trace, 1);
    expect(update.mock.calls.at(-1)![3]).toBe(false);
    expect(reset).toHaveBeenCalledTimes(1);

    act(() => useAppStore.setState({ theme: "dark" }));
    expect(theme).toHaveBeenLastCalledWith(true);
    expect(gpu.renderers).toHaveLength(1);

    view.unmount();
    expect(gpu.renderers[0]!.disposed).toBe(true);
  });

  it("forwards clicks to the latest selection handler", () => {
    const dispose = vi.spyOn(Brain3DSceneController.prototype, "dispose");
    let handler: ((id: string | null) => void) | undefined;
    vi.spyOn(Brain3DSceneController.prototype, "updateGraph").mockImplementation(function (this: unknown) {
      handler = (this as { onSelectNode?: (id: string | null) => void }).onSelectNode;
    });
    const first = vi.fn();
    const second = vi.fn();
    const view = render(<Brain3DCanvas nodes={nodes} edges={edges} language="en" onSelectNode={first} />);
    view.rerender(<Brain3DCanvas nodes={[...nodes]} edges={edges} language="en" onSelectNode={second} />);
    handler!("n1");
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledWith("n1");

    view.rerender(<Brain3DCanvas nodes={[...nodes]} edges={edges} language="en" />);
    expect(() => handler!("n1")).not.toThrow();
    expect(dispose).not.toHaveBeenCalled();
  });

  it("renders an unlabelled, hidden ambient host", () => {
    useAppStore.setState({ theme: "dark" });
    render(<Brain3DCanvas nodes={nodes} edges={edges} language="en" variant="ambient" className="extra" />);
    const host = screen.getByTestId("brain-ambient-3d");
    expect(host.className).toBe("brain-3d-host is-ambient extra");
    expect(host.getAttribute("aria-hidden")).toBe("true");
    expect(host.getAttribute("role")).toBeNull();
    expect(host.querySelector('[role="status"]')).toBeNull();
    expect(host.querySelector(".brain-3d-hemisphere-legend")).toBeNull();
    expect(gpu.renderers[0]!.parameters.antialias).toBe(false);
  });
});

describe("Brain3DCanvas context loss", () => {
  beforeEach(() => {
    enableWebGL();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  const lose = () => act(() => {
    gpu.renderers.at(-1)!.domElement.dispatchEvent(new Event("webglcontextlost", { cancelable: true }));
  });

  it("announces the pause and clears it when the context returns in time", () => {
    render(<Brain3DCanvas nodes={nodes} edges={edges} language="en" fallback={<div data-testid="custom-2d-fallback" />} />);
    const status = screen.getByRole("status");
    expect(status.textContent).toBe("");
    lose();
    expect(screen.getByRole("status")).toBe(status);
    expect(status.textContent).toBe(t("en", "graph.canvas3d.contextLost"));
    act(() => {
      gpu.renderers.at(-1)!.domElement.dispatchEvent(new Event("webglcontextrestored"));
    });
    act(() => {
      vi.advanceTimersByTime(CONTEXT_RESTORE_GRACE_MS * 2);
    });
    expect(screen.getByRole("status")).toBe(status);
    expect(status.textContent).toBe("");
    expect(screen.queryByTestId("custom-2d-fallback")).toBeNull();
  });

  it("switches to the 2D fallback when the context stays lost", () => {
    render(<Brain3DCanvas nodes={nodes} edges={edges} language="en" fallback={<div data-testid="custom-2d-fallback" />} />);
    lose();
    act(() => {
      vi.advanceTimersByTime(CONTEXT_RESTORE_GRACE_MS);
    });
    expect(screen.getByTestId("custom-2d-fallback")).toBeTruthy();
    expect(gpu.renderers.at(-1)!.disposed).toBe(true);
  });
});
