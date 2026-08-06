import type {
  BrainBrief,
  BrainProof,
  BrainReadiness,
  ConversationSummary,
  IngestionSourceType,
  IngestionState,
  KnowledgeGraphModel,
  Message,
} from "@/features/brain/types";

/**
 * Hand-built prop fixtures for the Brain surface components.
 *
 * The Brain home splits into presentational components that take the whole
 * world as props (BrainConversation, BrainHomeDock, BrainHomeHero, the panels
 * in HomePanels). Rendering them directly with these fixtures reaches states
 * the full-page harness cannot: a populated graph, a proven model continuity,
 * approval cards, agent failures. Every key referenced here is a real i18n
 * key, so `t()` never leaks a raw `brain.*` key into an assertion.
 */

export function makeReadiness(overrides: Partial<BrainReadiness> = {}): BrainReadiness {
  return {
    score: 62,
    state: "alive",
    depth: 3,
    titleKey: "brain.readiness.alive",
    actionKey: "brain.readiness.map",
    source: "frontend_fallback",
    signals: { memoryCount: 4, conceptCount: 6, relationshipCount: 9, healthySources: 2 },
    ...overrides,
  };
}

export function makeProof(overrides: Partial<BrainProof> = {}): BrainProof {
  return {
    status: "alive",
    modelContinuity: {
      activeModel: "mlx-community/test-model",
      brainOwner: "workspace",
      capability: true,
      survivesModelSwitch: true,
      proven: true,
      contextStore: "sqlite",
    },
    proofs: {
      durableItems: 5,
      hasDurableEvidence: true,
      workspaceMemories: 5,
      conversations: 2,
      graphConcepts: 7,
      vectorItems: 11,
      healthySources: 2,
    },
    recall: {
      query: "회의 정리",
      count: 1,
      items: [
        {
          id: "r1",
          source: "memory",
          title: "지난 회의 메모",
          snippet: "결정 사항 세 가지",
          score: 0.9,
          matchedTerms: ["회의"],
          confidence: "high",
          locator: "메모 > 회의",
        },
      ],
    },
    claims: {
      canRecallUserContext: true,
      keepsContextAcrossModels: true,
      isKnowledgeStore: true,
    },
    ...overrides,
  };
}

export function makeBrief(overrides: Partial<BrainBrief> = {}): BrainBrief {
  return {
    status: "alive",
    score: 62,
    headlineKey: "brain.brief.headline.alive",
    bodyKey: "brain.brief.body.alive",
    focus: {
      kind: "topic",
      title: "프로젝트 계획",
      detail: "가장 최근에 자란 주제",
      source: "graph",
      score: 0.8,
      empty: false,
    },
    nextActions: [
      {
        id: "ask_brain",
        labelKey: "brain.brief.action.ask",
        detailKey: "brain.brief.action.ask.detail",
        route: "",
        priority: 1,
      },
      {
        id: "verify_model",
        labelKey: "brain.brief.action.verify",
        detailKey: "brain.brief.action.verify.detail",
        route: "",
        priority: 2,
      },
      {
        id: "add_source",
        labelKey: "brain.brief.action.add",
        detailKey: "brain.brief.action.add.detail",
        route: "/capture",
        priority: 3,
      },
    ],
    suggestedQuestions: [],
    proactiveActions: [],
    evidence: [
      { id: "durable", labelKey: "brain.brief.evidence.durable", value: 5, detailKey: "brain.brief.evidence.durable.detail" },
      { id: "graph", labelKey: "brain.brief.evidence.graph", value: 7, detailKey: "brain.brief.evidence.graph.detail" },
      { id: "sources", labelKey: "brain.brief.evidence.sources", value: 2, detailKey: "brain.brief.evidence.sources.detail" },
    ],
    generatedAt: "2026-08-05T09:00:00Z",
    ...overrides,
  };
}

export function makeGraph(overrides: Partial<KnowledgeGraphModel> = {}): KnowledgeGraphModel {
  return {
    nodes: [
      { id: "n1", label: "프로젝트 계획", type: "topic", summary: "핵심 주제", importance: 0.9 },
      { id: "n2", label: "회의", type: "topic", summary: "정례 회의", importance: 0.7 },
    ],
    edges: [{ id: "e1", source: "n1", target: "n2", label: "관련", weight: 0.5 }],
    ...overrides,
  };
}

export const emptyIngestionStates: Record<IngestionSourceType, IngestionState | null> = {
  chat: null,
  file: null,
  folder: null,
  note: null,
  web: null,
};

export function makeConversations(count: number): ConversationSummary[] {
  return Array.from({ length: count }, (_, index) => ({
    id: `conv-${index + 1}`,
    title: `대화 ${index + 1}`,
    messageCount: index + 2,
    updatedAt: Date.now() - index * 60_000,
  }));
}

export function assistantMessage(overrides: Partial<Message> = {}): Message {
  return { role: "assistant", content: "정리된 답변입니다.", ...overrides };
}

export function userMessage(content = "이거 기억해줘"): Message {
  return { role: "user", content };
}
