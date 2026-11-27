import * as THREE from "three";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Brain3DPoint } from "./brainGrowth";
import { LABEL_LIMIT, LabelOverlay, clipLabel, estimateLabelWidth } from "./labelOverlay";

function point(id: string, x: number, y: number, z = 0, importance = 0.5): Brain3DPoint {
  return {
    id,
    label: id.toUpperCase(),
    type: "Concept",
    group: "concept",
    hemisphere: "left",
    x,
    y,
    z,
    radius: 0.1,
    importance,
    superseded: false,
    recent: false,
    colorHex: 0,
  };
}

function camera() {
  const view = new THREE.PerspectiveCamera(44, 800 / 600, 0.1, 80);
  view.position.set(0, 0, 8);
  view.updateMatrixWorld();
  return view;
}

function mount() {
  const container = document.createElement("div");
  document.body.appendChild(container);
  return { container, overlay: new LabelOverlay(container) };
}

const spans = (container: HTMLElement) => [...container.querySelectorAll<HTMLSpanElement>(".brain-3d-label")];
const identity = new THREE.Matrix4();

afterEach(() => {
  document.body.replaceChildren();
  vi.restoreAllMocks();
});

describe("LabelOverlay", () => {
  it("labels only the points it was given text for, most important first", () => {
    const { container, overlay } = mount();
    overlay.setLabels(
      [point("a", -2, 0, 0, 0.2), point("b", 2, 0, 0, 0.9), point("c", 0, 2)],
      new Map([["a", "Alpha"], ["b", "Beta"]]),
      null,
    );
    expect(spans(container).map((span) => span.textContent)).toEqual(["Beta", "Alpha"]);
    expect(spans(container).every((span) => span.hidden)).toBe(true);
    overlay.update(camera(), identity, 800, 600);
    expect(spans(container).every((span) => !span.hidden)).toBe(true);
    expect(spans(container)[0]!.style.transform).toMatch(/^translate\(\d+px, \d+px\)$/);
  });

  it("hides the weaker of two colliding labels and promotes the selected one", () => {
    const { container, overlay } = mount();
    const points = [point("a", 0, 0, 0, 0.9), point("b", 0, 0, 0, 0.1)];
    const labels = new Map([["a", "Alpha"], ["b", "Beta"]]);
    overlay.setLabels(points, labels, null);
    overlay.update(camera(), identity, 800, 600);
    expect(spans(container).map((span) => span.hidden)).toEqual([false, true]);
    overlay.setLabels(points, labels, "b");
    overlay.update(camera(), identity, 800, 600);
    expect(spans(container)[0]!.textContent).toBe("Beta");
    expect(spans(container)[0]!.className).toBe("brain-3d-label is-selected");
    expect(spans(container).map((span) => span.hidden)).toEqual([false, true]);
  });

  it.each([
    ["behind the camera", point("a", 0, 0, 20)],
    ["off to the left", point("a", -40, 0)],
    ["off to the right", point("a", 40, 0)],
    ["off the top", point("a", 0, 40)],
    ["off the bottom", point("a", 0, -40)],
  ])("hides a label that is %s", (_name, hidden) => {
    const { container, overlay } = mount();
    overlay.setLabels([hidden], new Map([["a", "Alpha"]]), null);
    overlay.update(camera(), identity, 800, 600);
    expect(spans(container)[0]!.hidden).toBe(true);
  });

  it("caps the label count and clips long names", () => {
    const { container, overlay } = mount();
    const many = Array.from({ length: LABEL_LIMIT + 5 }, (_, index) => point(`n${index}`, 0, 0));
    overlay.setLabels(many, new Map(many.map((item) => [item.id, item.id])), null);
    expect(spans(container)).toHaveLength(LABEL_LIMIT);
    expect(clipLabel("x".repeat(40))).toBe(`${"x".repeat(27)}…`);
    expect(clipLabel("short")).toBe("short");
  });

  it("clips by code points so an emoji is never cut in half", () => {
    expect(clipLabel(`${"x".repeat(26)}😀tail`)).toBe(`${"x".repeat(26)}😀…`);
    expect(clipLabel("😀".repeat(28))).toBe("😀".repeat(28));
  });

  it("estimates wide scripts and emoji at twice the Latin width", () => {
    expect(estimateLabelWidth("abcd")).toBe(40);
    for (const wide of ["지식그래", "知識図表", "かなカナ", "ＡＢ（）", "😀😀😀😀"]) {
      expect(estimateLabelWidth(wide)).toBe(68);
    }
  });

  it("culls Hangul labels by their wider footprint when layout cannot be measured", () => {
    const { container, overlay } = mount();
    const points = [point("a", -0.4, 0, 0, 0.9), point("b", 0.4, 0, 0, 0.1)];
    overlay.setLabels(points, new Map([["a", "abcdef"], ["b", "ghijkl"]]), null);
    overlay.update(camera(), identity, 800, 600);
    expect(spans(container).map((span) => span.hidden)).toEqual([false, false]);
    overlay.setLabels(points, new Map([["a", "지식그래프다"], ["b", "로컬우선두뇌"]]), null);
    overlay.update(camera(), identity, 800, 600);
    expect(spans(container).map((span) => span.hidden)).toEqual([false, true]);
  });

  it("measures each label once after insertion and trusts the measured box", () => {
    const measured: boolean[] = [];
    const width = vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockImplementation(function (this: HTMLElement) {
      measured.push(this.isConnected && !this.hidden);
      return 400;
    });
    const height = vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(40);
    const { container, overlay } = mount();
    overlay.setLabels(
      [point("a", -1, 0, 0, 0.9), point("b", 1, 0, 0, 0.1)],
      new Map([["a", "A"], ["b", "B"]]),
      null,
    );
    overlay.update(camera(), identity, 800, 600);
    overlay.update(camera(), identity, 800, 600);
    expect(spans(container).map((span) => span.hidden)).toEqual([false, true]);
    expect(measured).toEqual([true, true]);
    expect(width).toHaveBeenCalledTimes(2);
    expect(height).toHaveBeenCalledTimes(2);
  });

  it("leaves the DOM untouched on frames where nothing moved", () => {
    const { container, overlay } = mount();
    overlay.setLabels(
      [point("a", 0, 0, 0, 0.9), point("b", 0, 0, 0, 0.1)],
      new Map([["a", "Alpha"], ["b", "Beta"]]),
      null,
    );
    overlay.update(camera(), identity, 800, 600);
    const observer = new MutationObserver(() => {});
    observer.observe(container, { attributes: true, subtree: true });
    overlay.update(camera(), identity, 800, 600);
    expect(observer.takeRecords()).toEqual([]);
    overlay.update(camera(), identity, 1000, 600);
    expect(observer.takeRecords().map((record) => record.attributeName)).toEqual(["style"]);
    observer.disconnect();
  });

  it("shows and hides a tooltip and removes itself", () => {
    const { container, overlay } = mount();
    const tooltip = container.querySelector<HTMLDivElement>(".brain-3d-tooltip")!;
    expect(tooltip.hidden).toBe(true);
    overlay.showTooltip("Atlas", 10, 20);
    expect(tooltip.hidden).toBe(false);
    expect(tooltip.textContent).toBe("Atlas");
    expect(tooltip.style.transform).toBe("translate(20px, 30px)");
    overlay.hideTooltip();
    expect(tooltip.hidden).toBe(true);
    overlay.dispose();
    expect(container.children).toHaveLength(0);
  });
});
