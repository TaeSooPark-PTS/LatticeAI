import * as React from "react";
import cytoscape, { type Core } from "cytoscape";
import type { ExplorerModel } from "./graphExplorer";

export function CytoscapeGraph({
  model,
  selectedId,
  onSelect,
  fitSignal,
}: {
  model: ExplorerModel;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  fitSignal: number;
}) {
  const hostRef = React.useRef<HTMLDivElement | null>(null);
  const cyRef = React.useRef<Core | null>(null);
  React.useEffect(() => {
    if (!hostRef.current) return;
    cyRef.current?.destroy();
    cyRef.current = cytoscape({
      container: hostRef.current,
      elements: model.elements,
      style: [
        {
          selector: "node",
          style: {
            "background-color": "data(color)",
            "border-color": "data(borderColor)",
            "border-width": 1.5,
            color: "#f8fafc",
            label: "data(displayLabel)",
            "font-size": 10,
            "font-weight": 600,
            "text-outline-color": "#071012",
            "text-outline-width": 2.5,
            width: "data(size)",
            height: "data(size)",
            "overlay-opacity": 0,
          },
        },
        {
          selector: "node.cluster",
          style: {
            shape: "round-rectangle",
            "background-opacity": 0.46,
            "border-width": 2,
            "font-size": 12,
            "text-valign": "center",
            "text-halign": "center",
          },
        },
        {
          selector: "node.selected",
          style: {
            "border-width": 4,
          },
        },
        {
          selector: "node.match",
          style: {
            "border-width": 4,
            "border-color": "#fef08a",
          },
        },
        {
          selector: "edge",
          style: {
            width: "data(width)",
            "line-color": "#64748b",
            "target-arrow-shape": "triangle",
            "target-arrow-color": "#64748b",
            "curve-style": "bezier",
            "arrow-scale": 0.7,
            opacity: 0.72,
          },
        },
        {
          selector: "edge.connected",
          style: {
            width: 2.6,
            "line-color": "#fef08a",
            "target-arrow-color": "#fef08a",
            opacity: 1,
          },
        },
      ],
      layout: {
        name: "cose",
        animate: false,
        idealEdgeLength: 120,
        nodeRepulsion: 5800,
        gravity: 0.28,
        numIter: 1200,
        fit: true,
        padding: 32,
      },
    });
    cyRef.current.on("tap", "node", (event) => onSelect(String(event.target.id())));
    cyRef.current.on("tap", (event) => {
      if (event.target === cyRef.current) onSelect(null);
    });
    return () => cyRef.current?.destroy();
  }, [model.elements, onSelect]);

  React.useEffect(() => {
    cyRef.current?.fit(undefined, 32);
  }, [fitSignal]);

  React.useEffect(() => {
    if (!cyRef.current || !selectedId) return;
    const node = cyRef.current.getElementById(selectedId);
    if (node.length) {
      cyRef.current.animate({ center: { eles: node }, zoom: Math.max(cyRef.current.zoom(), 1.15) }, { duration: 180 });
    }
  }, [selectedId]);

  return (
    <div
      ref={hostRef}
      data-testid="brain-cytoscape"
      className="brain-grid h-[620px] min-h-[32rem] w-full overflow-hidden rounded-lg border border-border bg-background/80"
    />
  );
}
