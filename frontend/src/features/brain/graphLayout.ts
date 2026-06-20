import * as React from "react";
import type { KnowledgeConcept, RelationshipThread } from "./types";

// Return the set of node ids that share a direct (1-hop) edge with `nodeId`.
// The focused node itself is not included.
export function computeGraphNeighbors(nodeId: string, edges: RelationshipThread[]): Set<string> {
  const neighbors = new Set<string>();
  for (const edge of edges) {
    if (edge.source === nodeId) neighbors.add(edge.target);
    else if (edge.target === nodeId) neighbors.add(edge.source);
  }
  return neighbors;
}

export function layoutGraphNodes(nodes: KnowledgeConcept[], radiusX: number, radiusY: number) {
  return nodes.map((node, index) => {
    const point = polarPoint(index, nodes.length, radiusX, radiusY, -88);
    return { node, x: point.x, y: point.y };
  });
}

export function polarPoint(index: number, total: number, radiusX: number, radiusY: number, offsetDegrees = -90) {
  const count = Math.max(total, 1);
  const angle = ((360 / count) * index + offsetDegrees) * Math.PI / 180;
  return {
    x: 50 + Math.cos(angle) * radiusX,
    y: 50 + Math.sin(angle) * radiusY,
  };
}

export function layerStyle(values: Record<string, string>) {
  return values as React.CSSProperties;
}

export function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}
