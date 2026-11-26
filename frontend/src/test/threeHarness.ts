import type * as Three from "three";

type FakePass = { disposed: boolean; dispose: () => void };
type FakeComposer = { passes: FakePass[]; renders: number; size: number[]; disposed: boolean };

export const gpu = {
  renderers: [] as FakeRenderer[],
  composers: [] as FakeComposer[],
  blooms: [] as Array<{ strength: number }>,
  lastScene: null as Three.Scene | null,
  lastCamera: null as Three.PerspectiveCamera | null,
  failComposer: false,
  failRenderer: false,
};

export function resetGpu(): void {
  gpu.renderers = [];
  gpu.composers = [];
  gpu.blooms = [];
  gpu.lastScene = null;
  gpu.lastCamera = null;
  gpu.failComposer = false;
  gpu.failRenderer = false;
}

function draw(scene: Three.Scene, camera: Three.PerspectiveCamera): void {
  scene.updateMatrixWorld();
  camera.updateMatrixWorld();
  gpu.lastScene = scene;
  gpu.lastCamera = camera;
}

export class FakeRenderer {
  domElement = document.createElement("canvas");
  size: number[] = [];
  sizeCalls = 0;
  pixelRatio = 0;
  renders = 0;
  disposed = false;
  contextLost = false;
  toneMapping = 0;
  toneMappingExposure = 0;

  constructor(readonly parameters: Record<string, unknown>) {
    if (gpu.failRenderer) throw new Error("no context");
    gpu.renderers.push(this);
  }

  setPixelRatio(ratio: number) {
    this.pixelRatio = ratio;
  }

  setSize(width: number, height: number) {
    this.sizeCalls += 1;
    this.size = [width, height];
  }

  render(scene: Three.Scene, camera: Three.PerspectiveCamera) {
    this.renders += 1;
    draw(scene, camera);
  }

  dispose() {
    this.disposed = true;
  }

  forceContextLoss() {
    this.contextLost = true;
  }
}

export function mockThree(actual: unknown) {
  return { ...(actual as typeof Three), WebGLRenderer: FakeRenderer };
}

class Pass {
  disposed = false;
  dispose() {
    this.disposed = true;
  }
}

class RenderPass extends Pass {
  constructor(readonly scene: Three.Scene, readonly camera: Three.PerspectiveCamera) {
    super();
  }
}

class UnrealBloomPass extends Pass {
  strength: number;
  constructor(_resolution: unknown, strength: number) {
    super();
    this.strength = strength;
    gpu.blooms.push(this);
  }
}

class EffectComposer {
  passes: FakePass[] = [];
  renders = 0;
  size: number[] = [];
  disposed = false;

  constructor() {
    if (gpu.failComposer) throw new Error("no float targets");
    gpu.composers.push(this);
  }

  addPass(pass: FakePass) {
    this.passes.push(pass);
  }

  setSize(width: number, height: number) {
    this.size = [width, height];
  }

  render() {
    this.renders += 1;
    const base = this.passes[0] as unknown as RenderPass;
    draw(base.scene, base.camera);
  }

  dispose() {
    this.disposed = true;
  }
}

export const composerModule = { EffectComposer };
export const renderPassModule = { RenderPass };
export const bloomModule = { UnrealBloomPass };
