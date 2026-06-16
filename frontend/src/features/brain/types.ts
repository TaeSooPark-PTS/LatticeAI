import type { BrainState } from "@/components/LivingBrain";

export type ApiRecord = Record<string, unknown>;
export type BrainDepth = 1 | 2 | 3 | 4 | 5;

export type Message = {
  role: "user" | "assistant";
  content: string;
};

export type MemoryFragment = {
  id: string;
  title: string;
  kind: string;
};

export type KnowledgeConcept = {
  id: string;
  label: string;
  type: string;
  summary: string;
  importance: number;
};

export type RelationshipThread = {
  id: string;
  source: string;
  target: string;
  label: string;
  weight: number;
};

export type KnowledgeGraphModel = {
  nodes: KnowledgeConcept[];
  edges: RelationshipThread[];
};

export const DEPTHS: Array<{ level: BrainDepth; label: string; state: BrainState }> = [
  { level: 1, label: "Living Brain", state: "idle" },
  { level: 2, label: "Memory Layer", state: "recalling" },
  { level: 3, label: "Knowledge Layer", state: "synthesizing" },
  { level: 4, label: "Relationship Layer", state: "planning" },
  { level: 5, label: "Knowledge Graph", state: "synthesizing" },
];
