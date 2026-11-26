let cachedWebGL: boolean | null = null;

export function detectWebGLSupport(): boolean {
  if (cachedWebGL !== null) return cachedWebGL;
  try {
    const canvas = document.createElement("canvas");
    const gl = canvas.getContext("webgl2", { failIfMajorPerformanceCaveat: false })
      || canvas.getContext("webgl", { failIfMajorPerformanceCaveat: false });
    cachedWebGL = Boolean(gl);
    if (gl && "getExtension" in gl) {
      const lose = (gl as WebGLRenderingContext).getExtension("WEBGL_lose_context");
      lose?.loseContext();
    }
  } catch {
    cachedWebGL = false;
  }
  return cachedWebGL;
}

export function resetWebGLSupportCacheForTest(): void {
  cachedWebGL = null;
}

export function prefersReducedMotion(): boolean {
  if (typeof window.matchMedia !== "function") return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}
