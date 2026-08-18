import * as React from "react";
import ReactFlow, { Background, Controls, type Edge, type Node } from "reactflow";

/**
 * The node-and-edge canvas. Isolated so reactflow stays off the Act review
 * inbox's first paint — it only loads when someone opens recipes in advanced
 * mode.
 */
export function WorkflowGraph({ nodes, edges }: { nodes: Node[]; edges: Edge[] }) {
  return (
    <div className="h-[440px] rounded-lg border border-border" data-testid="act-workflow-graph">
      <ReactFlow nodes={nodes} edges={edges} fitView>
        <Background />
        <Controls />
      </ReactFlow>
    </div>
  );
}
