import * as THREE from "three";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { computeBrainGrowth, layoutBrainHemispheres, type Brain3DEdgeInput } from "./brainGrowth";
import { Brain3DSceneController, type Brain3DSceneOptions } from "./Brain3DScene";
import { gpu, resetGpu } from "@/test/threeHarness";

vi.mock("three", async (importOriginal) => (await import("@/test/threeHarness")).mockThree(await importOriginal()));
vi.mock("three/examples/jsm/postprocessing/EffectComposer.js", async () => (await import("@/test/threeHarness")).composerModule);
vi.mock("three/examples/jsm/postprocessing/RenderPass.js", async () => (await import("@/test/threeHarness")).renderPassModule);
vi.mock("three/examples/jsm/postprocessing/UnrealBloomPass.js", async () => (await import("@/test/threeHarness")).bloomModule);

const growth = computeBrainGrowth(4, 3);
const nodes = [
  { id: "a", label: "A", type: "Document", updatedAt: new Date().toISOString() },
  { id: "b", label: "B", type: "Concept" },
  { id: "c", label: "C", type: "Concept" },
  { id: "old", label: "Old", type: "Document", status: "superseded" },
];
const points = layoutBrainHemispheres(nodes, growth);
const edges: Brain3DEdgeInput[] = [
  { source: "a", target: "b" },
  { source: "b", target: "c" },
  { source: "a", target: "old" },
  { source: "a", target: "ghost" },
];

let frames: Map<number, FrameRequestCallback>;
let nextFrame: number;
let observers: Array<{ fire: () => void; disconnected: boolean }>;
const controllers: Brain3DSceneController[] = [];

beforeEach(() => {
  resetGpu();
  frames = new Map();
  nextFrame = 1;
  observers = [];
  vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
    const id = nextFrame++;
    frames.set(id, callback);
    return id;
  });
  vi.spyOn(window, "cancelAnimationFrame").mockImplementation((id) => {
    frames.delete(id);
  });
  vi.stubGlobal("ResizeObserver", class {
    private entry: { fire: () => void; disconnected: boolean };
    constructor(callback: () => void) {
      this.entry = { fire: callback, disconnected: false };
      observers.push(this.entry);
    }
    observe() {}
    disconnect() {
      this.entry.disconnected = true;
    }
  });
  vi.spyOn(THREE.Clock.prototype, "getDelta").mockImplementation(function (this: THREE.Clock) {
    this.elapsedTime += 0.05;
    return 0.05;
  });
});

afterEach(() => {
  controllers.splice(0).forEach((controller) => controller.dispose());
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function step(count = 1) {
  for (let i = 0; i < count; i += 1) {
    const pending = [...frames.values()];
    frames.clear();
    pending.forEach((callback) => callback(0));
  }
}

function mount(overrides: Partial<Brain3DSceneOptions> = {}, size = { width: 800, height: 600 }) {
  const container = document.createElement("div");
  Object.defineProperty(container, "clientWidth", { configurable: true, get: () => size.width });
  Object.defineProperty(container, "clientHeight", { configurable: true, get: () => size.height });
  document.body.appendChild(container);
  const onSelectNode = vi.fn();
  const controller = new Brain3DSceneController({
    container,
    isDark: true,
    ambientOnly: false,
    reducedMotion: false,
    onSelectNode,
    ...overrides,
  });
  controllers.push(controller);
  const renderer = gpu.renderers.at(-1)!;
  return { controller, container, renderer, onSelectNode, size };
}

const scene = () => gpu.lastScene!;
const camera = () => gpu.lastCamera!;
const group = () => scene().children.find((child) => child instanceof THREE.Group) as THREE.Group;
const nodeMesh = () => group().children.find((child) => child instanceof THREE.InstancedMesh) as THREE.InstancedMesh | undefined;
const lines = () => group().children.find((child) => child instanceof THREE.LineSegments) as THREE.LineSegments | undefined;
const sparks = () => group().children.find((child) => child instanceof THREE.Points) as THREE.Points | undefined;
const dust = () => scene().children.find((child) => child instanceof THREE.Points) as THREE.Points;
const attr = (name: string) => Array.from((lines()!.geometry.getAttribute(name) as THREE.BufferAttribute).array, (value) => Number(value.toFixed(4)));
const scaleOf = (index: number) => {
  const matrix = new THREE.Matrix4();
  nodeMesh()!.getMatrixAt(index, matrix);
  return matrix.elements[0]!;
};
const colorOf = (index: number) => {
  const color = new THREE.Color();
  nodeMesh()!.getColorAt(index, color);
  return color.getHex();
};

function screenPosition(index: number) {
  const matrix = new THREE.Matrix4();
  nodeMesh()!.getMatrixAt(index, matrix);
  const world = new THREE.Vector3().setFromMatrixPosition(matrix).applyMatrix4(group().matrixWorld);
  const projected = world.project(camera());
  return { clientX: ((projected.x + 1) / 2) * 800, clientY: ((1 - projected.y) / 2) * 600 };
}

function pointer(target: EventTarget, type: string, init: { clientX?: number; clientY?: number } = {}) {
  target.dispatchEvent(new MouseEvent(type, { bubbles: true, ...init }));
}

describe("Brain3DSceneController lifecycle", () => {
  it("mounts a canvas, renders through the bloom composer and follows container size", () => {
    const { container, renderer, size } = mount();
    expect(container.querySelector("canvas.brain-3d-canvas-element")).toBe(renderer.domElement);
    expect(renderer.size).toEqual([800, 600]);
    step();
    expect(gpu.composers[0]!.renders).toBe(1);

    size.width = 50;
    size.height = 0;
    observers[0]!.fire();
    expect(renderer.size).toEqual([160, 240]);
    expect(gpu.composers[0]!.size).toEqual([160, 240]);

    size.width = 0;
    size.height = 400;
    observers[0]!.fire();
    expect(renderer.size).toEqual([320, 400]);
  });

  it("renders the light theme directly so the canvas stays see-through, and blooms again in the dark", () => {
    const { renderer, controller } = mount();
    controller.setTheme(false);
    step();
    expect(renderer.renders).toBe(1);
    expect(gpu.composers[0]!.renders).toBe(0);
    controller.setTheme(true);
    step();
    expect(gpu.composers[0]!.renders).toBe(1);
  });

  it("falls back to a direct render when post-processing cannot start", () => {
    gpu.failComposer = true;
    const { renderer, controller } = mount();
    step();
    controller.resize();
    controller.setTheme(false);
    expect(renderer.renders).toBe(1);
  });

  it("keeps an ambient backdrop cheap and non-interactive, even without a ResizeObserver", () => {
    vi.stubGlobal("ResizeObserver", undefined);
    vi.stubGlobal("devicePixelRatio", 0);
    const { renderer, onSelectNode, controller } = mount({ ambientOnly: true, isDark: false }, { width: 0, height: 0 });
    expect(renderer.parameters).toMatchObject({ antialias: false, powerPreference: "low-power" });
    expect(renderer.pixelRatio).toBe(1);
    expect(renderer.size).toEqual([320, 240]);
    controller.updateGraph(points, edges, growth, true);
    controller.setSelection("a");
    controller.setSelection(null);
    controller.resetCamera();
    step(3);
    pointer(renderer.domElement, "pointerdown");
    pointer(window, "pointerup");
    expect(onSelectNode).not.toHaveBeenCalled();
    expect(gpu.composers).toHaveLength(0);
    expect(renderer.renders).toBe(3);
  });

  it("releases every GPU resource and listener on dispose and ignores later frames and resizes", () => {
    const meshDispose = vi.spyOn(THREE.InstancedMesh.prototype, "dispose");
    const { controller, container, renderer } = mount();
    controller.updateGraph(points, edges, growth, true);
    step();
    const removed = vi.spyOn(window, "removeEventListener");
    const orphan = [...frames.values()][0]!;

    controllers.splice(0);
    controller.dispose();

    expect(meshDispose).toHaveBeenCalledTimes(1);
    expect(gpu.composers[0]!.disposed).toBe(true);
    expect(gpu.composers[0]!.passes.every((pass) => pass.disposed)).toBe(true);
    expect(renderer.disposed).toBe(true);
    expect(renderer.contextLost).toBe(true);
    expect(observers[0]!.disconnected).toBe(true);
    expect(container.querySelector("canvas")).toBeNull();
    expect(removed.mock.calls.map((call) => call[0])).toEqual(["pointermove", "pointerup", "pointercancel"]);
    expect(frames.size).toBe(0);

    orphan(0);
    expect(frames.size).toBe(0);
    controller.resize();
    expect(renderer.sizeCalls).toBe(1);
  });

  it("disposes cleanly before any graph arrived", () => {
    const { controller, renderer } = mount({ ambientOnly: true });
    controllers.splice(0);
    controller.dispose();
    expect(renderer.disposed).toBe(true);
  });
});

describe("Brain3DSceneController graph rendering", () => {
  it("draws one instance per node and only edges whose ends are drawn", () => {
    const { controller } = mount();
    controller.updateGraph(points, edges, growth, true);
    step();
    expect(nodeMesh()!.count).toBe(4);
    expect(attr("position")).toHaveLength(3 * 2 * 3);
    expect(attr("aPulse")).toEqual([0, 0, 0, 0, 0, 0]);
    expect(attr("aAlpha")).toEqual([0.75, 0.75, 0.75, 0.75, 0.25, 0.25]);
    expect(scaleOf(0)).toBeCloseTo(points[0]!.radius * 1.22);
    expect(sparks()!.geometry.getAttribute("position").count).toBe(3);
    expect(dust().geometry.getAttribute("position").count).toBe(growth.dustCount);

    controller.updateGraph(points, edges, growth, false);
    step();
    expect(nodeMesh()!.count).toBe(3);
    expect(attr("aAlpha")).toHaveLength(4);
  });

  it("empties the scene for an empty graph and releases replaced instance buffers", () => {
    const meshDispose = vi.spyOn(THREE.InstancedMesh.prototype, "dispose");
    const { controller } = mount();
    controller.updateGraph(points, edges, growth, true);
    controller.updateGraph([], [], computeBrainGrowth(0, 0), true);
    step();
    expect(meshDispose).toHaveBeenCalledTimes(1);
    expect(nodeMesh()).toBeUndefined();
    expect(lines()).toBeUndefined();
    expect(sparks()).toBeUndefined();
  });

  it("focuses a selection with its neighbours, then restores the overview", () => {
    const { controller } = mount();
    controller.updateGraph(points, edges, growth, true);
    step(60);
    const overview = camera().position.length();

    controller.setSelection("b");
    step(80);
    expect(scaleOf(1)).toBeCloseTo(points[1]!.radius * 1.85);
    expect(scaleOf(3)).toBeCloseTo(points[3]!.radius * 0.62);
    expect(attr("aPulse")).toEqual([0.72, 0.72, 0.72, 0.72, 0, 0]);
    expect(attr("aAlpha")).toEqual([0.95, 0.95, 0.95, 0.95, 0.14, 0.14]);
    const focused = camera().position.distanceTo(new THREE.Vector3(points[1]!.x, points[1]!.y, points[1]!.z));
    expect(focused).toBeCloseTo(5.2, 1);
    expect(focused).toBeLessThan(overview);

    controller.setSelection("missing");
    controller.setSelection(null);
    step(80);
    expect(camera().position.length()).toBeCloseTo(8.4, 1);
  });

  it("lights up the fired path and moves sparks along it", () => {
    const { controller } = mount();
    controller.updateGraph(points, edges, growth, true);
    controller.setFiring(
      { nodeIds: ["b", "c"], edges: [{ source: "b", target: "a" }, { source: "a", target: "ghost" }], timestamp: 1 },
      9,
    );
    step();
    expect(attr("aPulse")).toEqual([1, 1, 1, 1, 0, 0]);
    expect(scaleOf(1)).toBeCloseTo(points[1]!.radius * 1.55);
    expect(sparks()!.geometry.getAttribute("position").count).toBe(1);
    expect(Array.from(sparks()!.geometry.getAttribute("aIntensity").array)).toEqual([1]);

    const before = sparks()!.geometry.getAttribute("position").getX(0);
    step(4);
    expect(sparks()!.geometry.getAttribute("position").getX(0)).not.toBe(before);

    controller.setFiring({ nodeIds: ["a"], edges: [], timestamp: 2 }, 0);
    expect(sparks()!.geometry.getAttribute("position").count).toBe(3);
    controller.setFiring(null, 0.6);
    expect(Array.from(sparks()!.geometry.getAttribute("aIntensity").array)[0]).toBeCloseTo(0.55);
  });

  it("restyles the whole scene, nodes included, when the theme flips", () => {
    const { controller, renderer } = mount();
    controller.updateGraph(points, edges, growth, true);
    controller.setSelection("a");
    step();
    const material = () => nodeMesh()!.material as THREE.MeshStandardMaterial;
    const ambient = scene().children.find((child) => child instanceof THREE.AmbientLight) as THREE.AmbientLight;
    const edgeMaterial = lines()!.material as THREE.ShaderMaterial;
    const sparkMaterial = sparks()!.material as THREE.ShaderMaterial;
    const darkDim = colorOf(3);

    controller.setTheme(true);
    expect(colorOf(3)).toBe(darkDim);

    controller.setTheme(false);
    expect(renderer.toneMappingExposure).toBe(1);
    expect(ambient.intensity).toBe(1.15);
    expect(material().emissive.getHex()).toBe(0x2563eb);
    expect(colorOf(3)).not.toBe(darkDim);
    expect(edgeMaterial.blending).toBe(THREE.NormalBlending);
    expect(sparkMaterial.uniforms.uSize!.value).toBe(14);
    expect(gpu.blooms[0]!.strength).toBe(0.24);
    expect((dust().material as THREE.PointsMaterial).opacity).toBe(0.22);

    controller.setTheme(true);
    expect(renderer.toneMappingExposure).toBe(1.18);
    expect(material().emissive.getHex()).toBe(0x0ea5e9);
    expect(gpu.blooms[0]!.strength).toBe(0.58);
    expect(edgeMaterial.blending).toBe(THREE.AdditiveBlending);
  });

  it("flips the theme of a light scene that has no graph yet", () => {
    const { controller } = mount({ isDark: false, ambientOnly: true });
    controller.setTheme(true);
    step();
    expect((scene().fog as THREE.FogExp2).color.getHex()).toBe(0x060a14);
    expect(dust()).toBeDefined();
  });

  it("holds still when the viewer asks for reduced motion", () => {
    const { controller } = mount({ reducedMotion: true });
    controller.updateGraph(points, edges, growth, true);
    step();
    const first = camera().position.clone();
    const spark = sparks()!.geometry.getAttribute("position").getX(0);
    step(20);
    expect(camera().position.distanceTo(first)).toBeLessThan(0.001);
    expect(group().scale.x).toBe(1);
    expect(sparks()!.geometry.getAttribute("position").getX(0)).toBe(spark);
  });
});

describe("Brain3DSceneController interaction", () => {
  function ready() {
    const mounted = mount({ reducedMotion: true });
    mounted.renderer.domElement.getBoundingClientRect = () => ({ left: 0, top: 0, width: 800, height: 600 }) as DOMRect;
    mounted.controller.updateGraph(points, edges, growth, true);
    step();
    return mounted;
  }

  function click(canvas: HTMLElement, at: { clientX: number; clientY: number }) {
    pointer(canvas, "pointerdown", at);
    pointer(window, "pointerup", at);
  }

  it("selects the node under a click and nothing for empty space", () => {
    const { renderer, onSelectNode } = ready();
    click(renderer.domElement, screenPosition(1));
    expect(onSelectNode).toHaveBeenLastCalledWith("b");
    click(renderer.domElement, { clientX: 1, clientY: 1 });
    expect(onSelectNode).toHaveBeenCalledTimes(1);
  });

  it("ignores a hit it cannot map to a node, a collapsed canvas and a stray release", () => {
    const { renderer, onSelectNode } = ready();
    const at = screenPosition(1);
    pointer(window, "pointerup", at);
    const intersect = vi.spyOn(THREE.Raycaster.prototype, "intersectObject").mockReturnValue([{ instanceId: 99 } as never]);
    click(renderer.domElement, at);
    intersect.mockRestore();
    renderer.domElement.getBoundingClientRect = () => ({ left: 0, top: 0, width: 0, height: 600 }) as DOMRect;
    click(renderer.domElement, at);
    renderer.domElement.getBoundingClientRect = () => ({ left: 0, top: 0, width: 800, height: 0 }) as DOMRect;
    click(renderer.domElement, at);
    expect(onSelectNode).not.toHaveBeenCalled();
  });

  it("does not try to select when no handler or no nodes exist", () => {
    const intersect = vi.spyOn(THREE.Raycaster.prototype, "intersectObject");
    const silent = mount({ onSelectNode: undefined });
    silent.controller.updateGraph(points, edges, growth, true);
    click(silent.renderer.domElement, { clientX: 1, clientY: 1 });
    const empty = mount();
    click(empty.renderer.domElement, { clientX: 1, clientY: 1 });
    expect(intersect).not.toHaveBeenCalled();
  });

  it("orbits on drag without selecting, and resets the camera", () => {
    const { controller, renderer, onSelectNode } = ready();
    step(40);
    const start = camera().position.clone();
    pointer(window, "pointermove", { clientX: 500, clientY: 500 });
    step(5);
    expect(camera().position.distanceTo(start)).toBeLessThan(0.001);

    pointer(renderer.domElement, "pointerdown", { clientX: 100, clientY: 100 });
    pointer(window, "pointermove", { clientX: 101, clientY: 100 });
    pointer(window, "pointermove", { clientX: 300, clientY: 900 });
    pointer(window, "pointerup", { clientX: 300, clientY: 900 });
    step(80);
    expect(camera().position.distanceTo(start)).toBeGreaterThan(1);
    expect(camera().position.y / camera().position.length()).toBeCloseTo(Math.sin(1.15), 2);
    expect(onSelectNode).not.toHaveBeenCalled();

    controller.resetCamera();
    step(80);
    expect(camera().position.distanceTo(start)).toBeLessThan(0.01);
  });

  it("ends the drag when the browser cancels the pointer", () => {
    const { renderer, onSelectNode } = ready();
    step(40);
    pointer(renderer.domElement, "pointerdown", { clientX: 100, clientY: 100 });
    pointer(window, "pointercancel");
    const before = camera().position.clone();
    pointer(window, "pointermove", { clientX: 600, clientY: 400 });
    pointer(window, "pointerup", { clientX: 600, clientY: 400 });
    step(40);
    expect(camera().position.distanceTo(before)).toBeLessThan(0.001);
    expect(onSelectNode).not.toHaveBeenCalled();
  });

  it("zooms with the wheel inside fixed bounds without scrolling the page", () => {
    const { renderer } = ready();
    const wheel = (deltaY: number) => {
      const event = new WheelEvent("wheel", { deltaY, cancelable: true });
      renderer.domElement.dispatchEvent(event);
      step(100);
      return event;
    };
    expect(wheel(100000).defaultPrevented).toBe(true);
    expect(camera().position.length()).toBeCloseTo(18, 1);
    wheel(-100000);
    expect(camera().position.length()).toBeCloseTo(3.2, 1);
  });
});
