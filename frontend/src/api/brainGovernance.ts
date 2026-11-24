import { get, post, type ApiResult } from "./base";
import type { components } from "./openapi";

export type BrainIntegrityRepairRequest = components["schemas"]["BrainIntegrityRepairRequest"];
export type BrainEntityMergeRequest = components["schemas"]["BrainEntityMergeRequest"];
export type BrainEntityRollbackRequest = components["schemas"]["BrainEntityRollbackRequest"];
export type BrainCorrectionCreateRequest = components["schemas"]["BrainCorrectionCreateRequest"];
export type BrainCorrectionStatusRequest = components["schemas"]["BrainCorrectionStatusRequest"];
export type BrainEmbeddingCutoverRequest = components["schemas"]["BrainEmbeddingCutoverRequest"];

export type IntegrityFinding = {
  kind: string;
  severity: "critical" | "warning" | "info" | string;
  safety: "safe_auto" | "requires_approval" | string;
  count: number;
  sample_ids: string[];
  detail: string;
  recommended_action: string;
};

export type IntegrityReport = {
  healthy: boolean;
  sqlite_integrity_ok: boolean;
  foreign_key_violations: number;
  total_issues: number;
  critical_issues: number;
  warning_issues: number;
  findings: IntegrityFinding[];
  counts_by_kind: Record<string, number>;
};

export type PlannedRepairAction = {
  action_id: string;
  kind: string;
  safety: "safe_auto" | "requires_approval" | string;
  estimated_rows: number;
  description: string;
  will_execute: boolean;
};

export type RepairExecutionResult = {
  dry_run: boolean;
  backup_created?: string | null;
  plan: {
    dry_run: boolean;
    blast_radius: {
      rows_deleted: number;
      rows_updated: number;
      tables_touched: string[];
      safe_auto_actions: number;
      approval_gated_actions: number;
    };
    actions: PlannedRepairAction[];
  };
  executed_actions: string[];
  skipped_approval_actions: string[];
  rows_repaired: number;
  post_check_total_issues: number;
};

export type EntityMergeCandidate = {
  canonical_id: string;
  duplicate_id: string;
  canonical_title: string;
  duplicate_title: string;
  node_type: string;
  workspace_id: string;
  decision: "auto_merge" | "review_candidate" | "rejected_homonym" | string;
  signals: {
    exact_key_match: boolean;
    surface_similarity: number;
    token_jaccard: number;
    same_type: boolean;
    provenance_overlap: number;
    relational_overlap: number;
    temporal_proximity: number;
    combined_score: number;
    homonym_risk: boolean;
  };
  rationale: string;
};

export type EntityMergeAuditRecord = {
  merge_id: string;
  workspace_id: string;
  canonical_id: string;
  duplicate_id: string;
  actor: string;
  rationale: string;
  created_at: string;
  rolled_back_at?: string | null;
};

export type EntityCandidatesResponse = {
  workspace_id: string;
  candidates: EntityMergeCandidate[];
  recent_merges: EntityMergeAuditRecord[];
};

export type KnowledgeCorrectionRecord = {
  correction_id: string;
  workspace_id: string;
  target_kind: "entity_link" | "fact" | "relationship" | "citation" | string;
  target_id: string;
  status: "pending" | "confirmed" | "rejected" | string;
  actor: string;
  reason: string;
  before_snapshot: Record<string, unknown>;
  after_snapshot: Record<string, unknown>;
  reindex_triggered: boolean;
  local_only: boolean;
  external_training_upload_allowed: boolean;
  created_at: string;
  updated_at: string;
  reverted_at?: string | null;
};

export type CorrectionsResponse = {
  workspace_id: string;
  external_training_upload_allowed: boolean;
  corrections: KnowledgeCorrectionRecord[];
};

export type EmbeddingModelSpec = {
  model_id: string;
  version: string;
  provider: string;
  dimensions: number;
  normalization: string;
  distance_metric: string;
  languages: string[];
  modalities: string[];
  semantic: boolean;
  status: "active" | "shadow" | "deprecated" | "retired" | string;
};

export type ShadowReindexJob = {
  generation_id: string;
  model_id: string;
  model_version: string;
  dimensions: number;
  normalization: string;
  status: string;
  cursor_item_id?: string | null;
  processed_items: number;
  total_items: number;
  shadow_recall_at_5?: number | null;
  active_recall_at_5?: number | null;
  created_at: string;
  updated_at: string;
};

export type EmbeddingRegistryResponse = {
  models: EmbeddingModelSpec[];
  generations: ShadowReindexJob[];
};

export type TopicCluster = {
  cluster_id: string;
  size: number;
  members: Array<{ id: string; title: string; type: string }>;
};

export type TopicClustersResponse = {
  workspace_id: string;
  clusters: TopicCluster[];
};

export type KnowledgeGapsResponse = {
  workspace_id: string;
  isolated_entities_count: number;
  missing_provenance_count: number;
  isolated_entities: Array<{ id: string; title: string; type: string }>;
  missing_provenance: Array<{ id: string; title: string; type: string }>;
};

const EMPTY_INTEGRITY: IntegrityReport = {
  healthy: true,
  sqlite_integrity_ok: true,
  foreign_key_violations: 0,
  total_issues: 0,
  critical_issues: 0,
  warning_issues: 0,
  findings: [],
  counts_by_kind: {},
};

const EMPTY_CANDIDATES: EntityCandidatesResponse = {
  workspace_id: "personal",
  candidates: [],
  recent_merges: [],
};

const EMPTY_CORRECTIONS: CorrectionsResponse = {
  workspace_id: "personal",
  external_training_upload_allowed: false,
  corrections: [],
};

const EMPTY_EMBEDDINGS: EmbeddingRegistryResponse = {
  models: [],
  generations: [],
};

const EMPTY_CLUSTERS: TopicClustersResponse = {
  workspace_id: "personal",
  clusters: [],
};

const EMPTY_GAPS: KnowledgeGapsResponse = {
  workspace_id: "personal",
  isolated_entities_count: 0,
  missing_provenance_count: 0,
  isolated_entities: [],
  missing_provenance: [],
};

export const brainGovernanceApi = {
  integrityCheck: (global = false): Promise<ApiResult<IntegrityReport>> =>
    get<IntegrityReport>(
      global ? "/api/brain/integrity?global=true" : "/api/brain/integrity",
      EMPTY_INTEGRITY,
    ),
  integrityRepair: (
    body: BrainIntegrityRepairRequest,
  ): Promise<ApiResult<RepairExecutionResult>> =>
    post<RepairExecutionResult>("/api/brain/integrity/repair", body, {} as RepairExecutionResult),
  entityCandidates: (): Promise<ApiResult<EntityCandidatesResponse>> =>
    get<EntityCandidatesResponse>("/api/brain/entities/candidates", EMPTY_CANDIDATES),
  entityMerge: (
    body: BrainEntityMergeRequest,
  ): Promise<ApiResult<EntityMergeAuditRecord>> =>
    post<EntityMergeAuditRecord>("/api/brain/entities/merge", body, {} as EntityMergeAuditRecord),
  entityRollback: (
    body: BrainEntityRollbackRequest,
  ): Promise<ApiResult<EntityMergeAuditRecord>> =>
    post<EntityMergeAuditRecord>(
      "/api/brain/entities/rollback",
      body,
      {} as EntityMergeAuditRecord,
    ),
  listCorrections: (status?: string): Promise<ApiResult<CorrectionsResponse>> =>
    get<CorrectionsResponse>(
      status ? `/api/brain/corrections?status=${encodeURIComponent(status)}` : "/api/brain/corrections",
      EMPTY_CORRECTIONS,
    ),
  createCorrection: (
    body: BrainCorrectionCreateRequest,
  ): Promise<ApiResult<KnowledgeCorrectionRecord>> =>
    post<KnowledgeCorrectionRecord>(
      "/api/brain/corrections",
      body,
      {} as KnowledgeCorrectionRecord,
    ),
  updateCorrectionStatus: (
    correctionId: string,
    body: BrainCorrectionStatusRequest,
  ): Promise<ApiResult<KnowledgeCorrectionRecord>> =>
    post<KnowledgeCorrectionRecord>(
      `/api/brain/corrections/${encodeURIComponent(correctionId)}/status`,
      body,
      {} as KnowledgeCorrectionRecord,
    ),
  revertCorrection: (correctionId: string): Promise<ApiResult<KnowledgeCorrectionRecord>> =>
    post<KnowledgeCorrectionRecord>(
      `/api/brain/corrections/${encodeURIComponent(correctionId)}/revert`,
      {},
      {} as KnowledgeCorrectionRecord,
    ),
  embeddingRegistry: (): Promise<ApiResult<EmbeddingRegistryResponse>> =>
    get<EmbeddingRegistryResponse>("/api/brain/embeddings/registry", EMPTY_EMBEDDINGS),
  embeddingCutover: (
    body: BrainEmbeddingCutoverRequest,
  ): Promise<ApiResult<Record<string, unknown>>> =>
    post<Record<string, unknown>>("/api/brain/embeddings/cutover", body, {}),
  embeddingRollback: (): Promise<ApiResult<Record<string, unknown>>> =>
    post<Record<string, unknown>>("/api/brain/embeddings/rollback", {}, {}),
  topicClusters: (): Promise<ApiResult<TopicClustersResponse>> =>
    get<TopicClustersResponse>("/api/brain/clusters", EMPTY_CLUSTERS),
  knowledgeGaps: (): Promise<ApiResult<KnowledgeGapsResponse>> =>
    get<KnowledgeGapsResponse>("/api/brain/gaps", EMPTY_GAPS),
};
