/* ============================================================================
 * Graph canvas — force-directed renderer for the Knowledge Graph explorer.
 * Ports the legacy static/scripts/graph.js physics (pairwise repulsion +
 * edge springs + center gravity + velocity damping) onto a dependency-free
 * <canvas>. Interactions: pointer-event node drag, wheel zoom + drag pan,
 * two-pointer pinch zoom, hover highlight, click-to-select callback,
 * double-click to refit.
 *
 * Honesty + design rules:
 *  - Renders ONLY the nodes/edges it is given — never fabricates data.
 *  - Every color is resolved from design tokens via getComputedStyle at draw
 *    time (light/dark aware); nothing is hardcoded.
 *  - The RAF loop self-suspends when the simulation settles, when the
 *    document is hidden, and when the canvas leaves the DOM; interactions,
 *    resize, theme flips and re-attachment wake it.
 * ========================================================================== */

const WORLD_W = 1000;            // seed-layout world units (camera-independent)
const WORLD_H = 620;
const MIN_SCALE = 0.15;
const MAX_SCALE = 4;
const SETTLE_ENERGY = 0.05;      // total kinetic energy below which RAF pauses
const REPULSION = 2200;
const SPRING = 0.0042;
const DAMPING = 0.85;
const REST_LENGTH = 120;
const CLICK_SLOP_PX = 5;         // pointer travel under this counts as a click

/**
 * createGraphCanvas({ colorFor, onSelect })
 *  - colorFor(type) → a token reference like "var(--lt3-pillar-graph)".
 *  - onSelect(id|null) — fired on canvas click (toggle) and empty-space click.
 * Returns { el, setData({nodes, edges}), setSelected(id|null), destroy() }.
 */
export function createGraphCanvas({ colorFor, onSelect } = {}) {
  const canvas = document.createElement("canvas");
  canvas.setAttribute("role", "img");
  canvas.setAttribute("aria-label", "Knowledge graph");
  const ctx = canvas.getContext("2d");

  let nodes = [];
  let edges = [];
  let byId = new Map();
  let cam = { scale: 1, tx: 0, ty: 0 };
  let width = 0;
  let height = 0;
  let hovered = null;
  let selectedId = null;
  let dragging = null;
  let panning = null;
  let pinch = null;
  let pressTravel = Infinity;
  const pointers = new Map();
  let raf = 0;
  let needsFit = false;
  let tokens = null;
  const typeColors = new Map();
  let destroyed = false;

  /* ── design tokens (resolved, never hardcoded) ─────────────────────────── */
  function readVar(style, name, fallback) {
    const v = (style.getPropertyValue(name) || "").trim();
    return v || fallback;
  }

  function refreshTokens() {
    const style = getComputedStyle(canvas.isConnected ? canvas : document.documentElement);
    const text = readVar(style, "--text", style.color);
    tokens = {
      text,
      surface: readVar(style, "--surface", readVar(style, "--bg", text)),
      edge: readVar(style, "--border-strong", readVar(style, "--border", text)),
      accent: readVar(style, "--accent", text),
      muted: readVar(style, "--muted", text),
      font: style.fontFamily || "system-ui",
    };
    typeColors.clear();
  }

  function nodeColor(type) {
    if (!typeColors.has(type)) {
      const ref = colorFor ? String(colorFor(type) || "") : "";
      const m = /^var\((--[\w-]+)/.exec(ref.trim());
      const style = getComputedStyle(document.documentElement);
      typeColors.set(type, m ? readVar(style, m[1], tokens.accent) : tokens.accent);
    }
    return typeColors.get(type);
  }

  /* ── data ──────────────────────────────────────────────────────────────── */
  function setData(data) {
    const src = data || {};
    byId = new Map();
    nodes = (src.nodes || []).map((n) => {
      const node = {
        id: n.id,
        label: String(n.label || n.id || ""),
        type: n.type || "Entity",
        weight: clamp(Number(n.weight) || 0, 0, 1),
        seedX: Number.isFinite(n.x) ? n.x : null,
        seedY: Number.isFinite(n.y) ? n.y : null,
        x: 0, y: 0, vx: 0, vy: 0, r: 10, degree: 0,
      };
      byId.set(node.id, node);
      return node;
    });
    edges = (src.edges || [])
      .filter((e) => byId.has(e.from) && byId.has(e.to))
      .map((e) => ({
        from: e.from,
        to: e.to,
        weight: Number(e.weight) || 1,
        source: byId.get(e.from),
        target: byId.get(e.to),
      }));
    edges.forEach((e) => { e.source.degree++; e.target.degree++; });
    nodes.forEach((n) => {
      n.r = clamp(8 + n.weight * 10 + Math.sqrt(n.degree) * 1.6, 8, 28);
    });
    if (selectedId && !byId.has(selectedId)) selectedId = null;
    hovered = null;
    dragging = null;
    seedLayout();
    needsFit = true;
    canvas.dataset.nodeCount = String(nodes.length);
    canvas.setAttribute("aria-label",
      `Knowledge graph: ${nodes.length} entities, ${edges.length} relations. Use the inspector list for keyboard access.`);
    wake();
  }

  function seedLayout() {
    if (!nodes.length) return;
    const hasCoords = nodes.every((n) => n.seedX !== null && n.seedY !== null);
    if (hasCoords) {
      // Backend-provided normalized [0..1] coordinates seed the simulation.
      nodes.forEach((n) => {
        n.x = 60 + n.seedX * (WORLD_W - 120);
        n.y = 50 + n.seedY * (WORLD_H - 100);
      });
      return;
    }
    // Golden-angle spiral by weight rank: heavy nodes near the center.
    const golden = Math.PI * (3 - Math.sqrt(5));
    const maxR = Math.min(WORLD_W, WORLD_H) * 0.42;
    const order = [...nodes].sort((a, b) => b.weight - a.weight);
    order.forEach((n, rank) => {
      const radius = rank === 0 ? 0 : maxR * Math.sqrt(rank / Math.max(1, nodes.length - 1));
      const angle = rank * golden;
      n.x = WORLD_W / 2 + Math.cos(angle) * radius;
      n.y = WORLD_H / 2 + Math.sin(angle) * radius * 0.72;
    });
  }

  /* ── physics (ported from legacy graph.js step()) ──────────────────────── */
  function step() {
    const centerPull = selectedId ? 0.00035 : 0.00055;
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i];
        const b = nodes[j];
        const dx = a.x - b.x;
        const dy = a.y - b.y;
        const d2 = Math.max(120, dx * dx + dy * dy);
        const force = REPULSION / d2;
        a.vx += dx * force; a.vy += dy * force;
        b.vx -= dx * force; b.vy -= dy * force;
      }
    }
    edges.forEach((e) => {
      const dx = e.target.x - e.source.x;
      const dy = e.target.y - e.source.y;
      const dist = Math.max(1, Math.hypot(dx, dy));
      const force = (dist - REST_LENGTH) * (SPRING + Math.min(0.003, e.weight * 0.0012));
      e.source.vx += (dx / dist) * force;
      e.source.vy += (dy / dist) * force;
      e.target.vx -= (dx / dist) * force;
      e.target.vy -= (dy / dist) * force;
    });
    let energy = 0;
    nodes.forEach((n) => {
      if (n === dragging) return;
      n.vx += (WORLD_W / 2 - n.x) * centerPull;
      n.vy += (WORLD_H / 2 - n.y) * centerPull;
      n.vx *= DAMPING;
      n.vy *= DAMPING;
      n.x += n.vx;
      n.y += n.vy;
      energy += n.vx * n.vx + n.vy * n.vy;
    });
    return energy;
  }

  /* ── camera ────────────────────────────────────────────────────────────── */
  function toWorld(px, py) {
    return { x: (px - cam.tx) / cam.scale, y: (py - cam.ty) / cam.scale };
  }

  function applyZoom(px, py, factor) {
    const next = clamp(cam.scale * factor, MIN_SCALE, MAX_SCALE);
    cam.tx = px - (px - cam.tx) * (next / cam.scale);
    cam.ty = py - (py - cam.ty) * (next / cam.scale);
    cam.scale = next;
  }

  function fitToScreen() {
    if (!nodes.length || !width || !height) return;
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    nodes.forEach((n) => {
      x0 = Math.min(x0, n.x - n.r); x1 = Math.max(x1, n.x + n.r);
      y0 = Math.min(y0, n.y - n.r); y1 = Math.max(y1, n.y + n.r);
    });
    const margin = 48;
    cam.scale = clamp(Math.min(
      (width - margin * 2) / Math.max(1, x1 - x0),
      (height - margin * 2) / Math.max(1, y1 - y0),
    ), MIN_SCALE, 2.5);
    cam.tx = (width - (x0 + x1) * cam.scale) / 2;
    cam.ty = (height - (y0 + y1) * cam.scale) / 2;
  }

  function centerOnNode(node) {
    cam.tx = width / 2 - node.x * cam.scale;
    cam.ty = height / 2 - node.y * cam.scale;
  }

  function nodeOnScreen(node) {
    const sx = node.x * cam.scale + cam.tx;
    const sy = node.y * cam.scale + cam.ty;
    return sx >= 0 && sx <= width && sy >= 0 && sy <= height;
  }

  function nodeAt(px, py) {
    const w = toWorld(px, py);
    let best = null;
    let bestDist = Infinity;
    nodes.forEach((n) => {
      const dist = Math.hypot(n.x - w.x, n.y - w.y);
      if (dist < n.r + 8 / cam.scale && dist < bestDist) { best = n; bestDist = dist; }
    });
    return best;
  }

  /* ── render loop (self-suspending) ─────────────────────────────────────── */
  function wake() {
    if (destroyed || raf || document.hidden || !canvas.isConnected) return;
    raf = requestAnimationFrame(draw);
  }

  function neighborSetOf(node) {
    const ids = new Set([node.id]);
    edges.forEach((e) => {
      if (e.from === node.id) ids.add(e.to);
      if (e.to === node.id) ids.add(e.from);
    });
    return ids;
  }

  function draw() {
    raf = 0;
    if (destroyed || document.hidden || !canvas.isConnected) return;
    if (!tokens) refreshTokens();
    const energy = step();
    if (needsFit) { fitToScreen(); needsFit = false; }

    ctx.clearRect(0, 0, width, height);
    ctx.save();
    ctx.translate(cam.tx, cam.ty);
    ctx.scale(cam.scale, cam.scale);

    const selected = selectedId ? byId.get(selectedId) : null;
    const active = hovered || selected;
    const neighbors = active ? neighborSetOf(active) : null;
    // LOD: drop labels when zoomed far out or the mesh is dense.
    const showLabels = cam.scale >= 0.55 && nodes.length <= 200;

    edges.forEach((e) => {
      const lit = neighbors && neighbors.has(e.from) && neighbors.has(e.to);
      ctx.globalAlpha = neighbors ? (lit ? 0.85 : 0.07) : 0.35;
      ctx.strokeStyle = tokens.edge;
      ctx.lineWidth = (1 + Math.min(2.4, e.weight * 0.6) + (lit ? 0.5 : 0)) / cam.scale;
      ctx.beginPath();
      ctx.moveTo(e.source.x, e.source.y);
      ctx.lineTo(e.target.x, e.target.y);
      ctx.stroke();
    });

    nodes.forEach((n) => {
      const isSelected = n === selected;
      const isHovered = n === hovered;
      const alpha = neighbors ? (neighbors.has(n.id) ? 1 : 0.14) : 1;
      const radius = n.r + (isSelected ? 3 : isHovered ? 2 : 0);

      ctx.globalAlpha = alpha;
      ctx.fillStyle = nodeColor(n.type);
      ctx.beginPath();
      ctx.arc(n.x, n.y, radius, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = tokens.surface;
      ctx.lineWidth = 2 / cam.scale;
      ctx.stroke();

      if (isSelected || isHovered) {
        ctx.strokeStyle = isSelected ? tokens.accent : nodeColor(n.type);
        ctx.lineWidth = (isSelected ? 2.6 : 1.8) / cam.scale;
        ctx.globalAlpha = alpha * 0.6;
        ctx.beginPath();
        ctx.arc(n.x, n.y, radius + 5 / cam.scale, 0, Math.PI * 2);
        ctx.stroke();
        ctx.globalAlpha = alpha;
      }

      if (showLabels || isSelected || isHovered) {
        const label = n.label.length > 22 ? n.label.slice(0, 21) + "…" : n.label;
        const fs = Math.max(9.5, 12 / cam.scale);
        ctx.font = `600 ${fs}px ${tokens.font}`;
        const lw = ctx.measureText(label).width;
        const lx = n.x - lw / 2;
        const ly = n.y + radius + (8 / cam.scale) + fs;
        const pad = 4 / cam.scale;
        ctx.globalAlpha = alpha > 0.5 ? alpha * 0.85 : alpha * 0.25;
        ctx.fillStyle = tokens.surface;
        ctx.beginPath();
        if (ctx.roundRect) ctx.roundRect(lx - pad, ly - fs, lw + pad * 2, fs + pad * 1.6, 5 / cam.scale);
        else ctx.rect(lx - pad, ly - fs, lw + pad * 2, fs + pad * 1.6);
        ctx.fill();
        ctx.globalAlpha = alpha;
        ctx.fillStyle = tokens.text;
        ctx.fillText(label, lx, ly);
      }
    });

    ctx.globalAlpha = 1;
    ctx.restore();
    if (energy > SETTLE_ENERGY || dragging) raf = requestAnimationFrame(draw);
  }

  /* ── interactions (pointer events: mouse + touch + pen) ────────────────── */
  function localPoint(e) {
    const rect = canvas.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }

  function pinchState() {
    const [a, b] = [...pointers.values()];
    return { d: Math.hypot(a.x - b.x, a.y - b.y), cx: (a.x + b.x) / 2, cy: (a.y + b.y) / 2 };
  }

  function onPointerDown(e) {
    canvas.setPointerCapture(e.pointerId);
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.size === 2) {
      dragging = null;
      panning = null;
      pinch = pinchState();
      return;
    }
    pressTravel = 0;
    const p = localPoint(e);
    const node = nodeAt(p.x, p.y);
    if (node) {
      dragging = node;
    } else {
      panning = { sx: e.clientX, sy: e.clientY, tx0: cam.tx, ty0: cam.ty };
      canvas.style.cursor = "grabbing";
    }
    wake();
  }

  function onPointerMove(e) {
    const prev = pointers.get(e.pointerId);
    if (prev) {
      pressTravel += Math.hypot(e.clientX - prev.x, e.clientY - prev.y);
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    }
    if (pinch && pointers.size === 2) {
      const next = pinchState();
      const rect = canvas.getBoundingClientRect();
      applyZoom(next.cx - rect.left, next.cy - rect.top, next.d / Math.max(1, pinch.d));
      pinch = next;
      wake();
      return;
    }
    if (dragging) {
      const p = localPoint(e);
      const w = toWorld(p.x, p.y);
      dragging.x = w.x; dragging.y = w.y;
      dragging.vx = 0; dragging.vy = 0;
      wake();
    } else if (panning) {
      cam.tx = panning.tx0 + (e.clientX - panning.sx);
      cam.ty = panning.ty0 + (e.clientY - panning.sy);
      wake();
    } else {
      const p = localPoint(e);
      const node = nodeAt(p.x, p.y);
      if (node !== hovered) { hovered = node; wake(); }
      canvas.style.cursor = node ? "pointer" : "grab";
    }
  }

  function onPointerUp(e) {
    pointers.delete(e.pointerId);
    if (pointers.size < 2) pinch = null;
    dragging = null;
    panning = null;
    canvas.style.cursor = "grab";
    if (pressTravel < CLICK_SLOP_PX) {
      const p = localPoint(e);
      const node = nodeAt(p.x, p.y);
      const next = node && node.id !== selectedId ? node.id : null;
      selectedId = next;
      if (onSelect) onSelect(next);
    }
    pressTravel = Infinity;
    wake();
  }

  function onPointerCancel(e) {
    pointers.delete(e.pointerId);
    pinch = null;
    dragging = null;
    panning = null;
    pressTravel = Infinity;
  }

  function onPointerLeave() {
    if (hovered) { hovered = null; wake(); }
  }

  function onWheel(e) {
    e.preventDefault();
    const p = localPoint(e);
    applyZoom(p.x, p.y, e.deltaY < 0 ? 1.12 : 1 / 1.12);
    wake();
  }

  function onDblClick() {
    fitToScreen();
    wake();
  }

  function onVisibility() {
    if (!document.hidden) wake();
  }

  /* ── environment: resize (DPR-aware) + theme flips ─────────────────────── */
  function resize() {
    const rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    width = rect.width;
    height = rect.height;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.floor(width * dpr);
    canvas.height = Math.floor(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    wake();
  }

  const resizeObserver = new ResizeObserver(resize);
  resizeObserver.observe(canvas);

  const themeObserver = new MutationObserver(() => { refreshTokens(); wake(); });
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-lt-theme"] });
  const colorScheme = window.matchMedia("(prefers-color-scheme: dark)");
  const onScheme = () => { refreshTokens(); wake(); };
  colorScheme.addEventListener("change", onScheme);

  canvas.addEventListener("pointerdown", onPointerDown);
  canvas.addEventListener("pointermove", onPointerMove);
  canvas.addEventListener("pointerup", onPointerUp);
  canvas.addEventListener("pointercancel", onPointerCancel);
  canvas.addEventListener("pointerleave", onPointerLeave);
  canvas.addEventListener("wheel", onWheel, { passive: false });
  canvas.addEventListener("dblclick", onDblClick);
  document.addEventListener("visibilitychange", onVisibility);

  function setSelected(id) {
    selectedId = id || null;
    const node = selectedId ? byId.get(selectedId) : null;
    if (node && width && !nodeOnScreen(node)) centerOnNode(node);
    wake();
  }

  function destroy() {
    destroyed = true;
    if (raf) { cancelAnimationFrame(raf); raf = 0; }
    resizeObserver.disconnect();
    themeObserver.disconnect();
    colorScheme.removeEventListener("change", onScheme);
    document.removeEventListener("visibilitychange", onVisibility);
    canvas.removeEventListener("pointerdown", onPointerDown);
    canvas.removeEventListener("pointermove", onPointerMove);
    canvas.removeEventListener("pointerup", onPointerUp);
    canvas.removeEventListener("pointercancel", onPointerCancel);
    canvas.removeEventListener("pointerleave", onPointerLeave);
    canvas.removeEventListener("wheel", onWheel);
    canvas.removeEventListener("dblclick", onDblClick);
    pointers.clear();
    nodes = [];
    edges = [];
    byId = new Map();
  }

  return { el: canvas, setData, setSelected, destroy };
}

function clamp(v, min, max) {
  return Math.max(min, Math.min(max, v));
}
