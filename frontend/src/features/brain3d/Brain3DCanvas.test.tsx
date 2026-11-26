import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { Brain3DCanvas } from "./Brain3DCanvas";
import { resetWebGLSupportCacheForTest } from "./webglSupport";

afterEach(() => {
  resetWebGLSupportCacheForTest();
});

describe("Brain3DCanvas", () => {
  it("renders graceful fallback when WebGL is unavailable in headless jsdom", () => {
    render(
      <Brain3DCanvas
        nodes={[
          { id: "n1", label: "Atlas", type: "Document" },
          { id: "n2", label: "Lattice", type: "Concept" },
        ]}
        edges={[{ source: "n1", target: "n2" }]}
        language="en"
        fallback={<div data-testid="custom-2d-fallback">2D Canvas</div>}
      />,
    );
    expect(screen.getByTestId("custom-2d-fallback")).toBeTruthy();
  });

  it("renders default fallback notice when no custom fallback is supplied", () => {
    render(
      <Brain3DCanvas
        nodes={[{ id: "n1", label: "Atlas", type: "Document" }]}
        edges={[]}
        language="en"
      />,
    );
    expect(screen.getByTestId("brain-graph-3d-fallback")).toBeTruthy();
  });
});
