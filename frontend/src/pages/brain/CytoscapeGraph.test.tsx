import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import cytoscape from "cytoscape";
import { useAppStore } from "@/store/appStore";
import { CytoscapeGraph } from "./CytoscapeGraph";
import type { ExplorerModel, GraphNode } from "./graphExplorer";

/**
 * Cytoscape draws into a real 2D canvas, which jsdom cannot provide. The mock
 * below replays the exact API surface the component touches — construction,
 * tap handlers, zoom clamping, fit, per-element focus classes and destroy — so
 * these tests hold the component's wiring, while pixels stay with Playwright.
 */
const h = vi.hoisted(() => {
  type TapHandler = { selector?: string; fn: (event: { target: unknown }) => void };
  type FakeCy = {
    options: Record<string, unknown>;
    zoom: (value?: number) => number | void;
    center: () => void;
    centerCalls: number;
    fit: (eles: unknown, padding: number) => void;
    fitCalls: Array<{ eles: unknown; padding: number }>;
    animate: (target: unknown, opts: unknown) => void;
    animateCalls: Array<{ target: unknown; opts: unknown }>;
    destroy: () => void;
    destroyed: boolean;
    on: (event: string, selectorOrFn: unknown, maybeFn?: unknown) => void;
    nodes: () => { removeClass: (name: string) => void };
    removeClassCalls: string[];
    addClassCalls: string[];
    getElementById: (id: string) => { length: number; addClass: (name: string) => void };
    tap: (selector: string | undefined, target: unknown) => void;
  };
  const state = {
    instances: [] as FakeCy[],
    nextZoom: 1,
    missingElementIds: new Set<string>(),
  };
  function makeCy(options: Record<string, unknown>): FakeCy {
    const handlers: Array<{ event: string } & TapHandler> = [];
    let zoom = state.nextZoom;
    const cy: FakeCy = {
      options,
      centerCalls: 0,
      fitCalls: [],
      animateCalls: [],
      destroyed: false,
      removeClassCalls: [],
      addClassCalls: [],
      zoom: (value?: number) => {
        if (value === undefined) return zoom;
        zoom = value;
      },
      center: () => {
        cy.centerCalls += 1;
      },
      fit: (eles: unknown, padding: number) => {
        cy.fitCalls.push({ eles, padding });
      },
      animate: (target: unknown, opts: unknown) => {
        cy.animateCalls.push({ target, opts });
      },
      destroy: () => {
        cy.destroyed = true;
      },
      on: (event: string, selectorOrFn: unknown, maybeFn?: unknown) => {
        if (typeof selectorOrFn === "string") {
          handlers.push({ event, selector: selectorOrFn, fn: maybeFn as TapHandler["fn"] });
        } else {
          handlers.push({ event, fn: selectorOrFn as TapHandler["fn"] });
        }
      },
      nodes: () => ({
        removeClass: (name: string) => {
          cy.removeClassCalls.push(name);
        },
      }),
      getElementById: (id: string) => ({
        length: state.missingElementIds.has(id) ? 0 : 1,
        addClass: (name: string) => {
          cy.addClassCalls.push(`${id}:${name}`);
        },
      }),
      tap: (selector: string | undefined, target: unknown) => {
        for (const handler of handlers) {
          if (handler.event === "tap" && handler.selector === selector) handler.fn({ target });
        }
      },
    };
    return cy;
  }
  return { state, makeCy };
});

vi.mock("cytoscape", () => ({
  default: vi.fn((options: Record<string, unknown>) => {
    const cy = h.makeCy(options);
    h.state.instances.push(cy);
    return cy;
  }),
}));

// React ships as ESM here, so its namespace cannot be spied on. A passthrough
// mock lets one test swap `useRef` while every other test keeps the real hook.
const refControl = vi.hoisted(() => ({ next: null as null | (() => { current: unknown }) }));
vi.mock("react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react")>();
  return {
    ...actual,
    useRef: ((initial: unknown) =>
      refControl.next ? refControl.next() : actual.useRef(initial)) as typeof actual.useRef,
  };
});

const makeNode = (id: string, label = id): GraphNode => ({
  id,
  label,
  type: "Node",
  group: "other",
  summary: "",
  source: "",
  importance: 0.5,
  degree: 0,
  searchText: id,
  raw: {},
});

function makeModel(nodes: GraphNode[]): ExplorerModel {
  return {
    nodes,
    edges: [],
    groups: [],
    elements: nodes.map((node) => ({ data: { id: node.id } })),
    visibleNodes: nodes,
    visibleEdges: [],
    totalNodes: nodes.length,
    totalEdges: 0,
    hiddenByFilters: 0,
  };
}

const THREE = makeModel([makeNode("a", "노드 A"), makeNode("b", "노드 B"), makeNode("c", "노드 C")]);

function lastCy() {
  return h.state.instances[h.state.instances.length - 1];
}

describe("CytoscapeGraph", () => {
  beforeEach(() => {
    h.state.instances.length = 0;
    h.state.nextZoom = 1;
    h.state.missingElementIds.clear();
    useAppStore.setState({ language: "ko" } as never);
  });
  afterEach(() => vi.restoreAllMocks());

  it("builds the graph from the model and clamps an over-zoomed layout", () => {
    h.state.nextZoom = 2;
    render(
      <CytoscapeGraph model={THREE} selectedId={null} onSelect={vi.fn()} fitSignal={0} ariaLabel="지도" />,
    );
    const cy = lastCy();
    expect(cy.options.elements).toBe(THREE.elements);
    expect(cy.options.maxZoom).toBe(1.4);
    expect(cy.zoom()).toBe(1.4);
    expect(cy.centerCalls).toBeGreaterThan(0);
    // The mount also runs the fit effect once, inside the same clamp.
    expect(cy.fitCalls).toEqual([{ eles: undefined, padding: 32 }]);
    expect(screen.getByTestId("brain-cytoscape").getAttribute("aria-label")).toBe("지도");
  });

  it("skips the zoom clamp when the layout already fits", () => {
    h.state.nextZoom = 1;
    render(<CytoscapeGraph model={THREE} selectedId={null} onSelect={vi.fn()} fitSignal={0} />);
    const cy = lastCy();
    expect(cy.zoom()).toBe(1);
    expect(cy.centerCalls).toBe(0);
  });

  it("reports node taps and background taps through onSelect", () => {
    const onSelect = vi.fn();
    render(<CytoscapeGraph model={THREE} selectedId={null} onSelect={onSelect} fitSignal={0} />);
    const cy = lastCy();
    cy.tap("node", { id: () => "b" });
    expect(onSelect).toHaveBeenLastCalledWith("b");
    // A background tap clears the selection…
    cy.tap(undefined, cy);
    expect(onSelect).toHaveBeenLastCalledWith(null);
    // …but a bubbled tap whose target is an element does not.
    onSelect.mockClear();
    cy.tap(undefined, { not: "the core" });
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("re-fits when the fit signal bumps, clamping only when needed", () => {
    const onSelect = vi.fn();
    const { rerender } = render(
      <CytoscapeGraph model={THREE} selectedId={null} onSelect={onSelect} fitSignal={0} />,
    );
    const cy = lastCy();
    expect(cy.fitCalls).toHaveLength(1);
    cy.zoom(3);
    rerender(<CytoscapeGraph model={THREE} selectedId={null} onSelect={onSelect} fitSignal={1} />);
    expect(cy.fitCalls).toHaveLength(2);
    expect(cy.zoom()).toBe(1.4);
  });

  it("animates toward a newly selected node, and ignores ids the canvas lost", () => {
    const onSelect = vi.fn();
    const { rerender } = render(
      <CytoscapeGraph model={THREE} selectedId={null} onSelect={onSelect} fitSignal={0} />,
    );
    const cy = lastCy();
    rerender(<CytoscapeGraph model={THREE} selectedId="a" onSelect={onSelect} fitSignal={0} />);
    expect(cy.animateCalls.some((call) => (call.opts as { duration: number }).duration === 180)).toBe(true);

    const before = cy.animateCalls.length;
    h.state.missingElementIds.add("phantom");
    rerender(<CytoscapeGraph model={THREE} selectedId="phantom" onSelect={onSelect} fitSignal={0} />);
    expect(cy.animateCalls.length).toBe(before);
  });

  it("rebuilds the graph when the elements change and destroys it on unmount", () => {
    const onSelect = vi.fn();
    const { rerender, unmount } = render(
      <CytoscapeGraph model={THREE} selectedId={null} onSelect={onSelect} fitSignal={0} />,
    );
    const first = lastCy();
    const next = makeModel([makeNode("z", "새 노드")]);
    rerender(<CytoscapeGraph model={next} selectedId={null} onSelect={onSelect} fitSignal={0} />);
    expect(first.destroyed).toBe(true);
    const second = lastCy();
    expect(second).not.toBe(first);
    unmount();
    expect(second.destroyed).toBe(true);
  });

  it("paints node labels with complete comma-hsl colors Cytoscape can parse", () => {
    document.documentElement.style.setProperty("--fg", "40 10% 12%");
    document.documentElement.style.setProperty("--bg", "42 28% 94%");
    document.documentElement.style.setProperty("--border-strong", "41 16% 74%");
    render(<CytoscapeGraph model={THREE} selectedId={null} onSelect={vi.fn()} fitSignal={0} />);
    const styles = (lastCy().options.style as Array<{ selector: string; style: Record<string, string> }>);
    const node = styles.find((entry) => entry.selector === "node")?.style;
    expect(node?.color).toBe("hsl(40, 10%, 12%)");
    expect(node?.["text-outline-color"]).toBe("hsl(42, 28%, 94%)");
    expect(styles.find((entry) => entry.selector === "edge")?.style["line-color"]).toBe("hsl(41, 16%, 74%)");
    document.documentElement.style.removeProperty("--fg");
    document.documentElement.style.removeProperty("--bg");
    document.documentElement.style.removeProperty("--border-strong");
  });

  it("rebuilds canvas paint when the theme flips", () => {
    const onSelect = vi.fn();
    useAppStore.setState({ theme: "dark" } as never);
    const { rerender } = render(
      <CytoscapeGraph model={THREE} selectedId={null} onSelect={onSelect} fitSignal={0} />,
    );
    const first = lastCy();
    useAppStore.setState({ theme: "light" } as never);
    rerender(<CytoscapeGraph model={THREE} selectedId={null} onSelect={onSelect} fitSignal={0} />);
    expect(first.destroyed).toBe(true);
    expect(lastCy()).not.toBe(first);
  });

  it("shows the narrow-search hint only when at most one node is visible", () => {
    const { unmount } = render(
      <CytoscapeGraph model={makeModel([makeNode("only")])} selectedId={null} onSelect={vi.fn()} fitSignal={0} />,
    );
    expect(screen.getByText("검색어를 지우면 전체 지도를 볼 수 있어요")).toBeTruthy();
    unmount();
    render(<CytoscapeGraph model={THREE} selectedId={null} onSelect={vi.fn()} fitSignal={0} />);
    expect(screen.queryByText("검색어를 지우면 전체 지도를 볼 수 있어요")).toBeNull();
  });

  it("walks the node list with the keyboard and reflects the cursor on the canvas", () => {
    const onSelect = vi.fn();
    render(<CytoscapeGraph model={THREE} selectedId={null} onSelect={onSelect} fitSignal={0} />);
    const cy = lastCy();
    const host = screen.getByTestId("brain-cytoscape");
    const status = () => screen.getByRole("status").textContent;

    fireEvent.keyDown(host, { key: "ArrowRight" }); // null → 0
    expect(status()).toBe("노드 A");
    expect(cy.addClassCalls).toContain("a:kb-focus");
    fireEvent.keyDown(host, { key: "ArrowDown" }); // 0 → 1
    expect(status()).toBe("노드 B");
    fireEvent.keyDown(host, { key: "ArrowLeft" }); // 1 → 0
    expect(status()).toBe("노드 A");
    fireEvent.keyDown(host, { key: "ArrowUp" }); // 0 → wraps to 2
    expect(status()).toBe("노드 C");
    fireEvent.keyDown(host, { key: "Home" });
    expect(status()).toBe("노드 A");
    fireEvent.keyDown(host, { key: "End" });
    expect(status()).toBe("노드 C");
    fireEvent.keyDown(host, { key: "Enter" });
    expect(onSelect).toHaveBeenLastCalledWith("c");
    fireEvent.keyDown(host, { key: " " });
    expect(onSelect).toHaveBeenLastCalledWith("c");
    // A key with no binding changes nothing.
    fireEvent.keyDown(host, { key: "x" });
    expect(status()).toBe("노드 C");
    fireEvent.keyDown(host, { key: "Escape" });
    expect(onSelect).toHaveBeenLastCalledWith(null);
    expect(status()).toBe("");
  });

  it("starts from the end when ArrowLeft is the first key pressed", () => {
    render(<CytoscapeGraph model={THREE} selectedId={null} onSelect={vi.fn()} fitSignal={0} />);
    fireEvent.keyDown(screen.getByTestId("brain-cytoscape"), { key: "ArrowLeft" });
    expect(screen.getByRole("status").textContent).toBe("노드 C");
  });

  it("Enter without a keyboard cursor selects nothing", () => {
    const onSelect = vi.fn();
    render(<CytoscapeGraph model={THREE} selectedId={null} onSelect={onSelect} fitSignal={0} />);
    fireEvent.keyDown(screen.getByTestId("brain-cytoscape"), { key: "Enter" });
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("keeps the canvas quiet when the keyboard cursor names a node the canvas lost", () => {
    h.state.missingElementIds.add("a");
    render(<CytoscapeGraph model={THREE} selectedId={null} onSelect={vi.fn()} fitSignal={0} />);
    const cy = lastCy();
    fireEvent.keyDown(screen.getByTestId("brain-cytoscape"), { key: "ArrowRight" });
    expect(screen.getByRole("status").textContent).toBe("노드 A");
    expect(cy.addClassCalls).toEqual([]);
  });

  it("ignores keys entirely when no nodes are visible", () => {
    const onSelect = vi.fn();
    render(<CytoscapeGraph model={makeModel([])} selectedId={null} onSelect={onSelect} fitSignal={0} />);
    fireEvent.keyDown(screen.getByTestId("brain-cytoscape"), { key: "ArrowRight" });
    expect(screen.getByRole("status").textContent).toBe("");
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("stays inert when the host element is not available", () => {
    // The effects all guard on refs that React fills before they run, so the
    // guards can only be exercised by simulating a render where the host ref
    // never attaches — the exact situation the guards defend against.
    const poisonedHost = { get current() { return null; }, set current(_value: unknown) { /* swallow */ } };
    const cyHolder = { current: null };
    let call = 0;
    refControl.next = () => (call++ % 2 === 0 ? poisonedHost : cyHolder);
    try {
      const { unmount } = render(
        <CytoscapeGraph model={THREE} selectedId="a" onSelect={vi.fn()} fitSignal={0} />,
      );
      fireEvent.keyDown(screen.getByTestId("brain-cytoscape"), { key: "ArrowRight" });
      unmount();
    } finally {
      refControl.next = null;
    }
    expect(vi.mocked(cytoscape)).not.toHaveBeenCalled();
    expect(h.state.instances).toHaveLength(0);
  });
});
