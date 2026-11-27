import * as THREE from "three";
import type { Brain3DPoint } from "./brainGrowth";

export const LABEL_LIMIT = 120;
const LABEL_HEIGHT = 18;
const LABEL_CHAR_WIDTH = 7;
const LABEL_WIDE_CHAR_WIDTH = 14;
const LABEL_PADDING = 12;
const LABEL_MAX_CHARS = 28;
const LABEL_OFFSET = 10;
const WIDE_CHAR = /[\p{Script=Hangul}\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Emoji_Presentation}\u3000-\u303f\uff01-\uff60\uffe0-\uffe6]/u;

type Box = { left: number; right: number; top: number; bottom: number };

type Entry = {
  point: Brain3DPoint;
  element: HTMLSpanElement;
  width: number;
  height: number;
  priority: number;
  transform: string;
};

export function clipLabel(text: string): string {
  const characters = Array.from(text);
  return characters.length > LABEL_MAX_CHARS ? `${characters.slice(0, LABEL_MAX_CHARS - 1).join("")}…` : text;
}

export function estimateLabelWidth(text: string): number {
  return Array.from(text).reduce(
    (width, character) => width + (WIDE_CHAR.test(character) ? LABEL_WIDE_CHAR_WIDTH : LABEL_CHAR_WIDTH),
    LABEL_PADDING,
  );
}

function overlaps(a: Box, b: Box): boolean {
  return a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
}

export class LabelOverlay {
  private root: HTMLDivElement;
  private tooltip: HTMLDivElement;
  private entries: Entry[] = [];
  private projected = new THREE.Vector3();

  constructor(container: HTMLElement) {
    this.root = document.createElement("div");
    this.root.className = "brain-3d-labels";
    this.root.setAttribute("aria-hidden", "true");
    this.tooltip = document.createElement("div");
    this.tooltip.className = "brain-3d-tooltip";
    this.tooltip.setAttribute("aria-hidden", "true");
    this.tooltip.hidden = true;
    container.appendChild(this.root);
    container.appendChild(this.tooltip);
  }

  setLabels(points: Brain3DPoint[], labels: ReadonlyMap<string, string>, selectedId: string | null): void {
    this.root.replaceChildren();
    this.entries = points
      .flatMap((point): Entry[] => {
        const text = labels.get(point.id);
        if (!text) return [];
        const element = document.createElement("span");
        const selected = point.id === selectedId;
        element.className = selected ? "brain-3d-label is-selected" : "brain-3d-label";
        element.textContent = clipLabel(text);
        return [{
          point,
          element,
          width: 0,
          height: 0,
          priority: (selected ? 10 : 0) + point.importance,
          transform: "",
        }];
      })
      .sort((a, b) => b.priority - a.priority)
      .slice(0, LABEL_LIMIT);
    this.root.append(...this.entries.map((entry) => entry.element));
    for (const entry of this.entries) {
      entry.width = entry.element.offsetWidth || estimateLabelWidth(entry.element.textContent!);
      entry.height = entry.element.offsetHeight || LABEL_HEIGHT;
    }
    for (const entry of this.entries) entry.element.hidden = true;
  }

  update(camera: THREE.Camera, matrixWorld: THREE.Matrix4, width: number, height: number): void {
    const placed: Box[] = [];
    for (const entry of this.entries) {
      const { x, y, z } = entry.point;
      this.projected.set(x, y, z).applyMatrix4(matrixWorld).project(camera);
      const left = ((this.projected.x + 1) / 2) * width - entry.width / 2;
      const top = ((1 - this.projected.y) / 2) * height + LABEL_OFFSET;
      const box = { left, right: left + entry.width, top, bottom: top + entry.height };
      const visible = this.projected.z < 1
        && box.right > 0
        && box.left < width
        && box.bottom > 0
        && box.top < height
        && !placed.some((other) => overlaps(box, other));
      if (entry.element.hidden === visible) entry.element.hidden = !visible;
      if (!visible) continue;
      placed.push(box);
      const transform = `translate(${Math.round(left)}px, ${Math.round(top)}px)`;
      if (transform === entry.transform) continue;
      entry.transform = transform;
      entry.element.style.transform = transform;
    }
  }

  showTooltip(text: string, x: number, y: number): void {
    this.tooltip.textContent = text;
    this.tooltip.style.transform = `translate(${Math.round(x + LABEL_OFFSET)}px, ${Math.round(y + LABEL_OFFSET)}px)`;
    this.tooltip.hidden = false;
  }

  hideTooltip(): void {
    this.tooltip.hidden = true;
  }

  dispose(): void {
    this.entries = [];
    this.root.remove();
    this.tooltip.remove();
  }
}
