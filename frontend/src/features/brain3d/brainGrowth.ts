export type BrainStage = "seed" | "cortex" | "neural_web" | "lattice";

export type BrainHemisphere = "left" | "right";

export type BrainGrowthMetrics = {
  nodeCount: number;
  edgeCount: number;
  stage: BrainStage;
  progress: number;
  scale: number;
  hemisphereVolume: number;
  density: number;
  luminosity: number;
  dustCount: number;
};

export type Brain3DNodeInput = {
  id: string;
  label: string;
  type: string;
  group?: string;
  importance?: number;
  degree?: number;
  status?: string;
  updatedAt?: string;
  raw?: Record<string, unknown>;
};

export type Brain3DEdgeInput = {
  id?: string;
  source: string;
  target: string;
  label?: string;
  weight?: number;
};

export type Brain3DPoint = {
  id: string;
  label: string;
  type: string;
  group: string;
  hemisphere: BrainHemisphere;
  x: number;
  y: number;
  z: number;
  radius: number;
  importance: number;
  superseded: boolean;
  recent: boolean;
  colorHex: number;
};

export type FiringTrace = {
  nodeIds: string[];
  edges: Array<{ source: string; target: string }>;
  timestamp: number;
};

const LEFT_HEMISPHERE_TYPES = new Set([
  "decision",
  "document",
  "file",
  "chunk",
  "source",
  "task",
  "workflow",
  "agent",
  "run",
  "approval",
  "hook",
  "policy",
  "code",
  "fact",
  "claim",
  "artifact",
  "url",
  "page",
  "transcript",
  "model",
  "skill",
  "plugin",
  "setting",
  "storage",
]);

const TYPE_COLORS: Record<string, number> = {
  decision: 0x38bdf8,
  document: 0x60a5fa,
  file: 0x3b82f6,
  chunk: 0x22d3ee,
  source: 0x06b6d4,
  task: 0xf59e0b,
  workflow: 0xfb923c,
  agent: 0xf97316,
  concept: 0xa78bfa,
  topic: 0xc084fc,
  note: 0x34d399,
  memory: 0x2dd4bf,
  insight: 0x818cf8,
  entity: 0x6366f1,
  person: 0xe879f9,
  organization: 0xd946ef,
  conversation: 0x14b8a6,
  message: 0x2dd4bf,
  chat: 0x10b981,
};

const TYPE_LOBES: Record<string, [number, number, number]> = {
  decision: [-0.95, 0.42, 0.52],
  document: [-1.15, 0.08, 0.05],
  file: [-1.05, -0.18, -0.25],
  chunk: [-0.88, -0.25, -0.45],
  source: [-1.12, 0.22, -0.35],
  task: [-0.72, 0.45, -0.15],
  workflow: [-0.78, -0.35, 0.35],
  agent: [-0.65, 0.15, 0.62],
  concept: [1.05, 0.36, 0.32],
  topic: [1.12, 0.18, -0.08],
  note: [0.92, -0.12, 0.48],
  memory: [0.85, -0.22, -0.32],
  insight: [0.75, 0.48, 0.12],
  entity: [1.15, -0.05, -0.42],
  person: [0.72, 0.15, 0.62],
  organization: [0.88, -0.38, 0.15],
  conversation: [0.68, -0.32, -0.52],
  message: [0.64, -0.18, -0.58],
  chat: [0.74, -0.28, -0.46],
};

function clampValue(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function hashString(input: string): number {
  let hash = 2166136261;
  for (let i = 0; i < input.length; i += 1) {
    hash ^= input.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function seededUnit(seed: number, salt: number): number {
  const mixed = Math.imul(seed ^ (salt * 2654435761), 2246822519) >>> 0;
  return (mixed % 10000) / 10000;
}

export function computeBrainStage(nodeCount: number): BrainStage {
  if (nodeCount >= 300) return "lattice";
  if (nodeCount >= 80) return "neural_web";
  if (nodeCount >= 15) return "cortex";
  return "seed";
}

export function computeBrainGrowth(nodeCount: number, edgeCount: number): BrainGrowthMetrics {
  const safeNodes = Math.max(0, Math.round(nodeCount));
  const safeEdges = Math.max(0, Math.round(edgeCount));
  const stage = computeBrainStage(safeNodes);
  const logNodes = Math.log10(safeNodes + 1);
  const logEdges = Math.log10(safeEdges + 1);
  const progress = clampValue(logNodes / 2.8, 0, 1);
  const scale = Number((0.68 + progress * 0.52).toFixed(3));
  const hemisphereVolume = Number((0.64 + progress * 0.56).toFixed(3));
  const density = Number(clampValue(0.22 + (logNodes * 0.2 + logEdges * 0.12), 0.22, 1).toFixed(3));
  const luminosity = Number(clampValue(0.5 + progress * 0.62, 0.5, 1.15).toFixed(3));
  const dustCount = Math.round(70 + progress * 210);
  return {
    nodeCount: safeNodes,
    edgeCount: safeEdges,
    stage,
    progress: Number(progress.toFixed(3)),
    scale,
    hemisphereVolume,
    density,
    luminosity,
    dustCount,
  };
}

export function classifyHemisphere(nodeType: string): BrainHemisphere {
  return LEFT_HEMISPHERE_TYPES.has(nodeType.trim().toLowerCase()) ? "left" : "right";
}

export function colorForNodeType(nodeType: string): number {
  return TYPE_COLORS[nodeType.trim().toLowerCase()] ?? 0x38bdf8;
}

export function isSupersededNode(node: Brain3DNodeInput): boolean {
  if (typeof node.status === "string" && node.status.toLowerCase() === "superseded") {
    return true;
  }
  const raw = node.raw;
  if (!raw || typeof raw !== "object") return false;
  if (typeof raw.status === "string" && raw.status.toLowerCase() === "superseded") {
    return true;
  }
  const temporal = raw.temporal;
  if (temporal && typeof temporal === "object") {
    const tStatus = (temporal as Record<string, unknown>).status;
    if (typeof tStatus === "string" && tStatus.toLowerCase() === "superseded") return true;
    const supersededBy = (temporal as Record<string, unknown>).superseded_by;
    if (typeof supersededBy === "string" && supersededBy.trim().length > 0) return true;
  }
  const metadata = raw.metadata;
  if (metadata && typeof metadata === "object") {
    const meta = metadata as Record<string, unknown>;
    const lifecycle = meta.lifecycle_status ?? meta.status;
    if (typeof lifecycle === "string" && lifecycle.toLowerCase() === "superseded") return true;
    if (typeof meta.superseded_by === "string" && meta.superseded_by.trim().length > 0) return true;
  }
  return false;
}

export function isRecentNode(node: Brain3DNodeInput, nowMs = Date.now()): boolean {
  const candidates: unknown[] = [node.updatedAt];
  if (node.raw && typeof node.raw === "object") {
    candidates.push(node.raw.updated_at, node.raw.created_at);
    const meta = node.raw.metadata;
    if (meta && typeof meta === "object") {
      const m = meta as Record<string, unknown>;
      candidates.push(m.updated_at, m.created_at, m.captured_at);
    }
  }
  const sevenDaysMs = 7 * 24 * 60 * 60 * 1000;
  for (const candidate of candidates) {
    if (typeof candidate === "number" && Number.isFinite(candidate)) {
      if (nowMs - candidate <= sevenDaysMs) return true;
    } else if (typeof candidate === "string" && candidate.trim()) {
      const parsed = Date.parse(candidate);
      if (Number.isFinite(parsed) && nowMs - parsed <= sevenDaysMs) return true;
    }
  }
  return false;
}

export function layoutBrainHemispheres(
  nodes: Brain3DNodeInput[],
  growth: BrainGrowthMetrics,
  nowMs = Date.now(),
): Brain3DPoint[] {
  const volume = growth.hemisphereVolume * 2.15;
  const fissure = 0.24 * growth.hemisphereVolume;
  return nodes.map((node, index) => {
    const normalizedType = node.type.trim().toLowerCase();
    const hemisphere = classifyHemisphere(normalizedType);
    const sign = hemisphere === "left" ? -1 : 1;
    const seed = hashString(`${node.id}:${index}`);
    const u = seededUnit(seed, 1);
    const v = seededUnit(seed, 2);
    const w = seededUnit(seed, 3);
    const theta = u * Math.PI * 2;
    const phi = Math.acos(2 * v - 1);
    const radial = 0.38 + Math.cbrt(w) * 0.62;

    const lobe = TYPE_LOBES[normalizedType] ?? [sign * 0.95, 0.0, 0.0];
    const shellX = Math.abs(Math.sin(phi) * Math.cos(theta)) * 0.92;
    const shellY = Math.cos(phi) * 0.72;
    const shellZ = Math.sin(phi) * Math.sin(theta) * 1.18;

    const gyri = 1 + 0.09 * Math.sin(theta * 5 + phi * 3);
    const rx = sign * (fissure + (Math.abs(lobe[0]) * 0.46 + shellX * radial * 0.68) * volume * gyri);
    const ry = (lobe[1] * 0.44 + shellY * radial * 0.76) * volume * gyri;
    const rz = (lobe[2] * 0.46 + shellZ * radial * 0.88) * volume * gyri;

    const importance = clampValue(node.importance ?? 0.45, 0.1, 1);
    const degreeBoost = Math.min(0.45, (node.degree ?? 0) * 0.035);
    const superseded = isSupersededNode(node);
    const recent = !superseded && isRecentNode(node, nowMs);
    const radius = Number(((0.055 + importance * 0.065 + degreeBoost * 0.05) * (superseded ? 0.72 : 1)).toFixed(4));

    return {
      id: node.id,
      label: node.label,
      type: node.type,
      group: node.group ?? "knowledge",
      hemisphere,
      x: Number(rx.toFixed(4)),
      y: Number(ry.toFixed(4)),
      z: Number(rz.toFixed(4)),
      radius,
      importance,
      superseded,
      recent,
      colorHex: colorForNodeType(normalizedType),
    };
  });
}

export function computeOneHopHighlight(
  selectedId: string | null,
  edges: Brain3DEdgeInput[],
): Set<string> {
  const result = new Set<string>();
  if (!selectedId) return result;
  result.add(selectedId);
  for (const edge of edges) {
    if (edge.source === selectedId) result.add(edge.target);
    else if (edge.target === selectedId) result.add(edge.source);
  }
  return result;
}

export function extractFiringTrace(payload: unknown): FiringTrace | null {
  if (!payload || typeof payload !== "object") return null;
  const record = payload as Record<string, unknown>;
  const nodeIds = new Set<string>();
  const edges: Array<{ source: string; target: string }> = [];

  const collectNode = (entry: unknown) => {
    if (typeof entry === "string" && entry.trim()) {
      nodeIds.add(entry.trim());
    } else if (entry && typeof entry === "object") {
      const item = entry as Record<string, unknown>;
      const id = item.id ?? item.node_id;
      if (typeof id === "string" && id.trim()) nodeIds.add(id.trim());
    }
  };

  for (const key of ["graph_nodes", "matched_nodes", "node_ids", "nodeIds"]) {
    const list = record[key];
    if (Array.isArray(list)) list.forEach(collectNode);
  }

  const rawEdges = record.graph_edges ?? record.edges;
  if (Array.isArray(rawEdges)) {
    for (const entry of rawEdges) {
      if (!entry || typeof entry !== "object") continue;
      const edge = entry as Record<string, unknown>;
      const source = edge.from ?? edge.source ?? edge.source_id;
      const target = edge.to ?? edge.target ?? edge.target_id;
      if (typeof source === "string" && typeof target === "string" && source && target) {
        edges.push({ source, target });
        nodeIds.add(source);
        nodeIds.add(target);
      }
    }
  }

  if (nodeIds.size === 0 && edges.length === 0) return null;
  return {
    nodeIds: Array.from(nodeIds),
    edges,
    timestamp: Date.now(),
  };
}
