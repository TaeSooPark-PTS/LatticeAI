import ReactFlow, { Background, Controls, type Edge, type Node } from "reactflow";

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
