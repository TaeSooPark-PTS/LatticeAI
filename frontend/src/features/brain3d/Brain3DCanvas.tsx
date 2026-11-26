import * as React from "react";
import { t, type Language } from "@/i18n";
import { useAppStore } from "@/store/appStore";
import {
  computeBrainGrowth,
  layoutBrainHemispheres,
  type Brain3DEdgeInput,
  type Brain3DNodeInput,
  type FiringTrace,
} from "./brainGrowth";
import { Brain3DSceneController } from "./Brain3DScene";
import { detectWebGLSupport, prefersReducedMotion } from "./webglSupport";

export type Brain3DCanvasProps = {
  nodes: Brain3DNodeInput[];
  edges: Brain3DEdgeInput[];
  selectedId?: string | null;
  onSelectNode?: (nodeId: string | null) => void;
  showSuperseded?: boolean;
  firingTrace?: FiringTrace | null;
  intensity?: number;
  resetSignal?: number;
  variant?: "explorer" | "ambient";
  language: Language;
  className?: string;
  fallback?: React.ReactNode;
};

export function Brain3DCanvas({
  nodes,
  edges,
  selectedId = null,
  onSelectNode,
  showSuperseded = true,
  firingTrace = null,
  intensity = 0.65,
  resetSignal = 0,
  variant = "explorer",
  language,
  className,
  fallback,
}: Brain3DCanvasProps) {
  const theme = useAppStore((state) => state.theme);
  const hostRef = React.useRef<HTMLDivElement | null>(null);
  const controllerRef = React.useRef<Brain3DSceneController | null>(null);
  const [webglReady, setWebglReady] = React.useState<boolean>(() => detectWebGLSupport());
  const growth = React.useMemo(() => computeBrainGrowth(nodes.length, edges.length), [nodes.length, edges.length]);
  const points = React.useMemo(() => layoutBrainHemispheres(nodes, growth), [nodes, growth]);
  const onSelectRef = React.useRef(onSelectNode);
  onSelectRef.current = onSelectNode;

  React.useEffect(() => {
    if (!webglReady || !hostRef.current) return;
    let controller: Brain3DSceneController | null = null;
    try {
      controller = new Brain3DSceneController({
        container: hostRef.current,
        isDark: theme === "dark",
        ambientOnly: variant === "ambient",
        reducedMotion: prefersReducedMotion(),
        onSelectNode: (id) => onSelectRef.current?.(id),
      });
      controllerRef.current = controller;
    } catch {
      setWebglReady(false);
    }
    return () => {
      controller?.dispose();
      controllerRef.current = null;
    };
  }, [webglReady, variant]);

  React.useEffect(() => {
    controllerRef.current?.setTheme(theme === "dark");
  }, [theme]);

  React.useEffect(() => {
    controllerRef.current?.updateGraph(points, edges, growth, showSuperseded);
  }, [points, edges, growth, showSuperseded]);

  React.useEffect(() => {
    controllerRef.current?.setSelection(selectedId);
  }, [selectedId]);

  React.useEffect(() => {
    controllerRef.current?.setFiring(firingTrace, intensity);
  }, [firingTrace, intensity]);

  React.useEffect(() => {
    if (resetSignal > 0) controllerRef.current?.resetCamera();
  }, [resetSignal]);

  if (!webglReady) {
    if (fallback) return <>{fallback}</>;
    return (
      <div
        className={`brain-3d-fallback ${className || ""}`.trim()}
        data-testid={variant === "ambient" ? "brain-ambient-3d-fallback" : "brain-graph-3d-fallback"}
        data-stage={growth.stage}
        aria-hidden={variant === "ambient" ? true : undefined}
      >
        {variant === "explorer" ? (
          <span>{t(language, "graph.canvas3d.fallback")}</span>
        ) : null}
      </div>
    );
  }

  return (
    <div
      ref={hostRef}
      className={`brain-3d-host ${variant === "ambient" ? "is-ambient" : "is-explorer"} ${className || ""}`.trim()}
      data-testid={variant === "ambient" ? "brain-ambient-3d" : "brain-graph-3d"}
      data-stage={growth.stage}
      data-nodes={growth.nodeCount}
      data-edges={growth.edgeCount}
      role={variant === "explorer" ? "region" : undefined}
      aria-label={variant === "explorer" ? t(language, "graph.canvas3d.aria") : undefined}
      aria-hidden={variant === "ambient" ? true : undefined}
    >
      {variant === "explorer" ? (
        <div className="brain-3d-hemisphere-legend" aria-hidden="true">
          <span>{t(language, "graph.hemisphere.left")}</span>
          <span>{t(language, "graph.hemisphere.right")}</span>
        </div>
      ) : null}
    </div>
  );
}
