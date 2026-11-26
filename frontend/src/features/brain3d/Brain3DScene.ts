import * as THREE from "three";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";
import {
  computeOneHopHighlight,
  type Brain3DEdgeInput,
  type Brain3DPoint,
  type BrainGrowthMetrics,
  type FiringTrace,
} from "./brainGrowth";
import { createDustMaterial, createSparkMaterial, createSynapseEdgeMaterial } from "./brainShaders";

export type Brain3DSceneOptions = {
  container: HTMLElement;
  isDark: boolean;
  ambientOnly: boolean;
  reducedMotion: boolean;
  onSelectNode?: (nodeId: string | null) => void;
};

type SparkSlot = {
  ax: number;
  ay: number;
  az: number;
  bx: number;
  by: number;
  bz: number;
  phase: number;
  speed: number;
  color: THREE.Color;
};

export class Brain3DSceneController {
  private container: HTMLElement;
  private renderer: THREE.WebGLRenderer;
  private scene: THREE.Scene;
  private camera: THREE.PerspectiveCamera;
  private composer: EffectComposer | null = null;
  private brainGroup: THREE.Group;
  private nodeMesh: THREE.InstancedMesh | null = null;
  private edgeLines: THREE.LineSegments | null = null;
  private edgeMaterial: THREE.ShaderMaterial;
  private sparkPoints: THREE.Points | null = null;
  private sparkMaterial: THREE.ShaderMaterial;
  private dustPoints: THREE.Points | null = null;
  private points: Brain3DPoint[] = [];
  private pointIndex = new Map<string, Brain3DPoint>();
  private edges: Brain3DEdgeInput[] = [];
  private sparkSlots: SparkSlot[] = [];
  private growth: BrainGrowthMetrics | null = null;
  private selectedId: string | null = null;
  private showSuperseded = true;
  private firingTrace: FiringTrace | null = null;
  private activityIntensity = 0.6;
  private isDark: boolean;
  private ambientOnly: boolean;
  private reducedMotion: boolean;
  private onSelectNode?: (nodeId: string | null) => void;
  private rafId = 0;
  private destroyed = false;
  private clock = new THREE.Clock();
  private raycaster = new THREE.Raycaster();
  private pointer = new THREE.Vector2();
  private dragging = false;
  private dragMoved = false;
  private lastX = 0;
  private lastY = 0;
  private yaw = 0.35;
  private pitch = 0.22;
  private targetYaw = 0.35;
  private targetPitch = 0.22;
  private distance = 8.4;
  private targetDistance = 8.4;
  private lookTarget = new THREE.Vector3(0, 0, 0);
  private desiredLookTarget = new THREE.Vector3(0, 0, 0);
  private resizeObserver: ResizeObserver | null = null;

  constructor(options: Brain3DSceneOptions) {
    this.container = options.container;
    this.isDark = options.isDark;
    this.ambientOnly = options.ambientOnly;
    this.reducedMotion = options.reducedMotion;
    this.onSelectNode = options.onSelectNode;

    const width = Math.max(160, this.container.clientWidth || 320);
    const height = Math.max(160, this.container.clientHeight || 240);

    this.scene = new THREE.Scene();
    const fogColor = this.isDark ? 0x060a14 : 0xf2f5fb;
    this.scene.fog = new THREE.FogExp2(fogColor, this.ambientOnly ? 0.045 : 0.055);

    this.camera = new THREE.PerspectiveCamera(44, width / height, 0.1, 80);
    this.camera.position.set(0, 1.1, this.distance);

    this.renderer = new THREE.WebGLRenderer({
      antialias: !this.ambientOnly,
      alpha: true,
      powerPreference: this.ambientOnly ? "low-power" : "high-performance",
    });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.setSize(width, height);
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = this.isDark ? 1.18 : 1.0;

    const canvas = this.renderer.domElement;
    canvas.className = "brain-3d-canvas-element";
    canvas.style.width = "100%";
    canvas.style.height = "100%";
    canvas.style.display = "block";
    this.container.appendChild(canvas);

    this.scene.add(new THREE.AmbientLight(this.isDark ? 0xb4d0ff : 0xffffff, this.isDark ? 0.95 : 1.15));
    const keyLight = new THREE.PointLight(0x38bdf8, 2.2, 32);
    keyLight.position.set(6, 5, 7);
    this.scene.add(keyLight);
    const rimLight = new THREE.PointLight(0xa78bfa, 1.8, 30);
    rimLight.position.set(-6, -3, -5);
    this.scene.add(rimLight);

    this.brainGroup = new THREE.Group();
    this.scene.add(this.brainGroup);

    this.edgeMaterial = createSynapseEdgeMaterial(this.isDark);
    this.sparkMaterial = createSparkMaterial(this.isDark);

    if (!this.ambientOnly) {
      try {
        const composer = new EffectComposer(this.renderer);
        composer.addPass(new RenderPass(this.scene, this.camera));
        composer.addPass(new UnrealBloomPass(new THREE.Vector2(width, height), this.isDark ? 0.58 : 0.24, 0.42, 0.35));
        this.composer = composer;
      } catch {
        this.composer = null;
      }
      this.bindEvents(canvas);
    }

    if (typeof ResizeObserver !== "undefined") {
      this.resizeObserver = new ResizeObserver(() => this.resize());
      this.resizeObserver.observe(this.container);
    }

    this.startLoop();
  }

  private bindEvents(canvas: HTMLCanvasElement): void {
    canvas.addEventListener("pointerdown", this.handlePointerDown);
    window.addEventListener("pointermove", this.handlePointerMove);
    window.addEventListener("pointerup", this.handlePointerUp);
    canvas.addEventListener("wheel", this.handleWheel, { passive: false });
  }

  private handlePointerDown = (event: PointerEvent): void => {
    this.dragging = true;
    this.dragMoved = false;
    this.lastX = event.clientX;
    this.lastY = event.clientY;
  };

  private handlePointerMove = (event: PointerEvent): void => {
    if (!this.dragging) return;
    const dx = event.clientX - this.lastX;
    const dy = event.clientY - this.lastY;
    if (Math.abs(dx) + Math.abs(dy) > 3) this.dragMoved = true;
    this.lastX = event.clientX;
    this.lastY = event.clientY;
    this.targetYaw -= dx * 0.007;
    this.targetPitch = Math.max(-1.15, Math.min(1.15, this.targetPitch + dy * 0.006));
  };

  private handlePointerUp = (event: PointerEvent): void => {
    if (!this.dragging) return;
    this.dragging = false;
    if (!this.dragMoved && this.nodeMesh && this.onSelectNode) {
      const rect = this.renderer.domElement.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        this.pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
        this.pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
        this.raycaster.setFromCamera(this.pointer, this.camera);
        const hits = this.raycaster.intersectObject(this.nodeMesh, false);
        const instanceId = hits[0]?.instanceId;
        if (typeof instanceId === "number" && this.points[instanceId]) {
          this.onSelectNode(this.points[instanceId]!.id);
        }
      }
    }
  };

  private handleWheel = (event: WheelEvent): void => {
    event.preventDefault();
    this.targetDistance = Math.max(3.2, Math.min(18, this.targetDistance + event.deltaY * 0.005));
  };

  public resize(): void {
    if (this.destroyed) return;
    const width = Math.max(160, this.container.clientWidth || 320);
    const height = Math.max(160, this.container.clientHeight || 240);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height);
    this.composer?.setSize(width, height);
  }

  public setTheme(isDark: boolean): void {
    if (this.isDark === isDark) return;
    this.isDark = isDark;
    this.scene.fog = new THREE.FogExp2(isDark ? 0x060a14 : 0xf2f5fb, this.ambientOnly ? 0.045 : 0.055);
    this.edgeMaterial.uniforms.uBaseOpacity!.value = isDark ? 0.34 : 0.28;
    this.edgeMaterial.uniforms.uGlowStrength!.value = isDark ? 1.35 : 0.95;
    this.edgeMaterial.blending = isDark ? THREE.AdditiveBlending : THREE.NormalBlending;
    this.edgeMaterial.needsUpdate = true;
    this.rebuildDust();
  }

  public updateGraph(
    points: Brain3DPoint[],
    edges: Brain3DEdgeInput[],
    growth: BrainGrowthMetrics,
    showSuperseded: boolean,
  ): void {
    this.points = showSuperseded ? points : points.filter((p) => !p.superseded);
    this.pointIndex = new Map(this.points.map((p) => [p.id, p]));
    this.edges = edges.filter((e) => this.pointIndex.has(e.source) && this.pointIndex.has(e.target));
    this.growth = growth;
    this.showSuperseded = showSuperseded;
    this.rebuildNodes();
    this.rebuildEdges();
    this.rebuildSparks();
    this.rebuildDust();
  }

  public setSelection(selectedId: string | null): void {
    this.selectedId = selectedId;
    if (selectedId) {
      const target = this.pointIndex.get(selectedId);
      if (target) {
        this.desiredLookTarget.set(target.x, target.y, target.z);
        this.targetDistance = Math.min(this.targetDistance, 5.2);
      }
    } else {
      this.desiredLookTarget.set(0, 0, 0);
      this.targetDistance = this.ambientOnly ? 7.2 : 8.4;
    }
    this.rebuildNodes();
    this.rebuildEdges();
  }

  public setFiring(trace: FiringTrace | null, intensity: number): void {
    this.firingTrace = trace;
    this.activityIntensity = Math.max(0.35, Math.min(1.4, intensity));
    this.rebuildNodes();
    this.rebuildEdges();
    this.rebuildSparks();
  }

  public resetCamera(): void {
    this.desiredLookTarget.set(0, 0, 0);
    this.targetYaw = 0.35;
    this.targetPitch = 0.22;
    this.targetDistance = this.ambientOnly ? 7.2 : 8.4;
  }

  private rebuildNodes(): void {
    if (this.nodeMesh) {
      this.brainGroup.remove(this.nodeMesh);
      this.nodeMesh.geometry.dispose();
      (this.nodeMesh.material as THREE.Material).dispose();
      this.nodeMesh = null;
    }
    const count = this.points.length;
    if (count === 0) return;

    const geometry = new THREE.SphereGeometry(1, 14, 14);
    const material = new THREE.MeshStandardMaterial({
      roughness: 0.25,
      metalness: 0.15,
      emissive: new THREE.Color(this.isDark ? 0x0ea5e9 : 0x2563eb),
      emissiveIntensity: (this.growth?.luminosity ?? 0.8) * (this.isDark ? 0.55 : 0.2),
    });
    const mesh = new THREE.InstancedMesh(geometry, material, count);
    const highlight = computeOneHopHighlight(this.selectedId, this.edges);
    const firingNodes = new Set(this.firingTrace?.nodeIds ?? []);
    const matrix = new THREE.Matrix4();
    const color = new THREE.Color();
    const dimColor = new THREE.Color(this.isDark ? 0x1e293b : 0x94a3b8);

    for (let i = 0; i < count; i += 1) {
      const point = this.points[i]!;
      const isSelected = point.id === this.selectedId;
      const isNeighbor = highlight.has(point.id);
      const isFiring = firingNodes.has(point.id);
      const dimmed = Boolean(this.selectedId && !isNeighbor);
      const scaleFactor = point.radius
        * (isSelected ? 1.85 : isFiring ? 1.55 : point.recent ? 1.22 : 1)
        * (dimmed ? 0.62 : 1);

      matrix.makeScale(scaleFactor, scaleFactor, scaleFactor);
      matrix.setPosition(point.x, point.y, point.z);
      mesh.setMatrixAt(i, matrix);

      color.setHex(point.colorHex);
      if (isFiring) {
        color.lerp(new THREE.Color(0x67e8f9), 0.55);
      } else if (point.superseded && this.showSuperseded) {
        color.lerp(dimColor, 0.72);
      } else if (dimmed) {
        color.lerp(dimColor, 0.78);
      } else if (point.recent) {
        color.offsetHSL(0, 0.08, 0.12);
      }
      mesh.setColorAt(i, color);
    }

    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    this.brainGroup.add(mesh);
    this.nodeMesh = mesh;
  }

  private rebuildEdges(): void {
    if (this.edgeLines) {
      this.brainGroup.remove(this.edgeLines);
      this.edgeLines.geometry.dispose();
      this.edgeLines = null;
    }
    if (this.edges.length === 0) return;

    const vertexCount = this.edges.length * 2;
    const positions = new Float32Array(vertexCount * 3);
    const colors = new Float32Array(vertexCount * 3);
    const pulses = new Float32Array(vertexCount);
    const alphas = new Float32Array(vertexCount);
    const highlight = computeOneHopHighlight(this.selectedId, this.edges);
    const firingEdgeKeys = new Set(
      (this.firingTrace?.edges ?? []).flatMap((e) => [`${e.source}->${e.target}`, `${e.target}->${e.source}`]),
    );
    const firingNodes = new Set(this.firingTrace?.nodeIds ?? []);
    const cA = new THREE.Color();
    const cB = new THREE.Color();

    let offset = 0;
    for (let i = 0; i < this.edges.length; i += 1) {
      const edge = this.edges[i]!;
      const src = this.pointIndex.get(edge.source);
      const dst = this.pointIndex.get(edge.target);
      if (!src || !dst) continue;

      const isFiring = firingEdgeKeys.has(`${edge.source}->${edge.target}`)
        || (firingNodes.has(edge.source) && firingNodes.has(edge.target));
      const isFocused = Boolean(
        this.selectedId && (edge.source === this.selectedId || edge.target === this.selectedId),
      );
      const isDimmed = Boolean(
        this.selectedId && !(highlight.has(edge.source) && highlight.has(edge.target)),
      );

      cA.setHex(isFiring ? 0x38bdf8 : src.colorHex);
      cB.setHex(isFiring ? 0xc084fc : dst.colorHex);
      const pulse = isFiring ? 1.0 : isFocused ? 0.72 : 0.0;
      const alpha = isFiring ? 1.0 : isFocused ? 0.95 : isDimmed ? 0.14 : (src.superseded || dst.superseded ? 0.25 : 0.75);

      positions[offset * 3] = src.x;
      positions[offset * 3 + 1] = src.y;
      positions[offset * 3 + 2] = src.z;
      colors[offset * 3] = cA.r;
      colors[offset * 3 + 1] = cA.g;
      colors[offset * 3 + 2] = cA.b;
      pulses[offset] = pulse;
      alphas[offset] = alpha;
      offset += 1;

      positions[offset * 3] = dst.x;
      positions[offset * 3 + 1] = dst.y;
      positions[offset * 3 + 2] = dst.z;
      colors[offset * 3] = cB.r;
      colors[offset * 3 + 1] = cB.g;
      colors[offset * 3 + 2] = cB.b;
      pulses[offset] = pulse;
      alphas[offset] = alpha;
      offset += 1;
    }

    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(positions.subarray(0, offset * 3), 3));
    geometry.setAttribute("color", new THREE.BufferAttribute(colors.subarray(0, offset * 3), 3));
    geometry.setAttribute("aPulse", new THREE.BufferAttribute(pulses.subarray(0, offset), 1));
    geometry.setAttribute("aAlpha", new THREE.BufferAttribute(alphas.subarray(0, offset), 1));

    const lines = new THREE.LineSegments(geometry, this.edgeMaterial);
    this.brainGroup.add(lines);
    this.edgeLines = lines;
  }

  private rebuildSparks(): void {
    if (this.sparkPoints) {
      this.brainGroup.remove(this.sparkPoints);
      this.sparkPoints.geometry.dispose();
      this.sparkPoints = null;
    }
    this.sparkSlots = [];

    const activeEdges = this.firingTrace?.edges.length
      ? this.firingTrace.edges
      : this.edges.slice(0, Math.min(28, this.edges.length));

    for (let i = 0; i < activeEdges.length; i += 1) {
      const edge = activeEdges[i]!;
      const src = this.pointIndex.get(edge.source);
      const dst = this.pointIndex.get(edge.target);
      if (!src || !dst) continue;
      this.sparkSlots.push({
        ax: src.x,
        ay: src.y,
        az: src.z,
        bx: dst.x,
        by: dst.y,
        bz: dst.z,
        phase: (i * 0.27) % 1,
        speed: this.firingTrace?.edges.length ? 0.65 : 0.22,
        color: new THREE.Color(i % 2 === 0 ? 0x38bdf8 : 0xc084fc),
      });
    }

    if (this.sparkSlots.length === 0) return;

    const count = this.sparkSlots.length;
    const positions = new Float32Array(count * 3);
    const colors = new Float32Array(count * 3);
    const intensities = new Float32Array(count);

    for (let i = 0; i < count; i += 1) {
      const slot = this.sparkSlots[i]!;
      positions[i * 3] = slot.ax;
      positions[i * 3 + 1] = slot.ay;
      positions[i * 3 + 2] = slot.az;
      colors[i * 3] = slot.color.r;
      colors[i * 3 + 1] = slot.color.g;
      colors[i * 3 + 2] = slot.color.b;
      intensities[i] = this.firingTrace ? 1.0 : 0.55;
    }

    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    geometry.setAttribute("aIntensity", new THREE.BufferAttribute(intensities, 1));
    this.sparkPoints = new THREE.Points(geometry, this.sparkMaterial);
    this.brainGroup.add(this.sparkPoints);
  }

  private rebuildDust(): void {
    if (this.dustPoints) {
      this.scene.remove(this.dustPoints);
      this.dustPoints.geometry.dispose();
      (this.dustPoints.material as THREE.Material).dispose();
      this.dustPoints = null;
    }
    const count = this.growth?.dustCount ?? 90;
    const positions = new Float32Array(count * 3);
    for (let i = 0; i < count; i += 1) {
      const angle = i * 2.39996;
      const radius = 3.2 + (i % 17) * 0.34;
      positions[i * 3] = Math.cos(angle) * radius;
      positions[i * 3 + 1] = ((i % 23) - 11) * 0.28;
      positions[i * 3 + 2] = Math.sin(angle) * radius;
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    this.dustPoints = new THREE.Points(geometry, createDustMaterial(this.isDark));
    this.scene.add(this.dustPoints);
  }

  private startLoop(): void {
    const tick = () => {
      if (this.destroyed) return;
      this.rafId = window.requestAnimationFrame(tick);
      const dt = Math.min(0.05, this.clock.getDelta());
      const elapsed = this.clock.elapsedTime;

      if (!this.dragging && !this.reducedMotion) {
        this.targetYaw += dt * (this.ambientOnly ? 0.14 : 0.065) * this.activityIntensity;
      }

      const damp = 1 - Math.exp(-6.5 * dt);
      this.yaw += (this.targetYaw - this.yaw) * damp;
      this.pitch += (this.targetPitch - this.pitch) * damp;
      this.distance += (this.targetDistance - this.distance) * damp;
      this.lookTarget.lerp(this.desiredLookTarget, damp);

      const cx = this.lookTarget.x + Math.sin(this.yaw) * Math.cos(this.pitch) * this.distance;
      const cy = this.lookTarget.y + Math.sin(this.pitch) * this.distance;
      const cz = this.lookTarget.z + Math.cos(this.yaw) * Math.cos(this.pitch) * this.distance;
      this.camera.position.set(cx, cy, cz);
      this.camera.lookAt(this.lookTarget);

      if (!this.reducedMotion) {
        const baseScale = this.growth?.scale ?? 1;
        const breathe = 1 + Math.sin(elapsed * (1.2 + this.activityIntensity * 0.6)) * 0.018;
        this.brainGroup.scale.setScalar(baseScale * breathe);
        if (this.dustPoints) {
          this.dustPoints.rotation.y = elapsed * 0.025;
        }
      }

      this.edgeMaterial.uniforms.uTime!.value = elapsed;

      if (this.sparkPoints && this.sparkSlots.length > 0 && !this.reducedMotion) {
        const posAttr = this.sparkPoints.geometry.getAttribute("position") as THREE.BufferAttribute;
        for (let i = 0; i < this.sparkSlots.length; i += 1) {
          const slot = this.sparkSlots[i]!;
          const t = (slot.phase + elapsed * slot.speed * this.activityIntensity) % 1;
          posAttr.setXYZ(
            i,
            slot.ax + (slot.bx - slot.ax) * t,
            slot.ay + (slot.by - slot.ay) * t,
            slot.az + (slot.bz - slot.az) * t,
          );
        }
        posAttr.needsUpdate = true;
      }

      if (this.composer) {
        this.composer.render();
      } else {
        this.renderer.render(this.scene, this.camera);
      }
    };
    this.rafId = window.requestAnimationFrame(tick);
  }

  public dispose(): void {
    this.destroyed = true;
    if (this.rafId) window.cancelAnimationFrame(this.rafId);
    this.resizeObserver?.disconnect();
    const canvas = this.renderer.domElement;
    canvas.removeEventListener("pointerdown", this.handlePointerDown);
    window.removeEventListener("pointermove", this.handlePointerMove);
    window.removeEventListener("pointerup", this.handlePointerUp);
    canvas.removeEventListener("wheel", this.handleWheel);
    if (this.nodeMesh) {
      this.nodeMesh.geometry.dispose();
      (this.nodeMesh.material as THREE.Material).dispose();
    }
    if (this.edgeLines) this.edgeLines.geometry.dispose();
    if (this.sparkPoints) this.sparkPoints.geometry.dispose();
    if (this.dustPoints) {
      this.dustPoints.geometry.dispose();
      (this.dustPoints.material as THREE.Material).dispose();
    }
    this.edgeMaterial.dispose();
    this.sparkMaterial.dispose();
    this.renderer.dispose();
    canvas.remove();
  }
}
