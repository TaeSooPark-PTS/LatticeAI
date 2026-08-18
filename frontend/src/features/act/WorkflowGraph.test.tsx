import * as React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("reactflow", () => ({
  __esModule: true,
  default: ({ children }: { children?: React.ReactNode }) => <div data-testid="reactflow-stub">{children}</div>,
  Background: () => <div>background</div>,
  Controls: () => <div>controls</div>,
}));

import { WorkflowGraph } from "./WorkflowGraph";

describe("WorkflowGraph", () => {
  it("hosts the canvas behind the act-workflow-graph test id", () => {
    render(
      <WorkflowGraph
        nodes={[{ id: "a", position: { x: 0, y: 0 }, data: { label: "A" } }]}
        edges={[]}
      />,
    );
    expect(screen.getByTestId("act-workflow-graph")).toBeTruthy();
    expect(screen.getByTestId("reactflow-stub")).toBeTruthy();
  });
});
