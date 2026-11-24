import * as React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@/i18n/brain";
import { brainGovernanceApi } from "@/api/brainGovernance";
import { BrainGovernanceHub } from "./BrainGovernanceHub";

function renderWithQuery(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("BrainGovernanceHub & 5 dedicated Brain UI panels", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders Integrity Check & Repair with dry-run blast radius preview and safe repair execution", async () => {
    vi.spyOn(brainGovernanceApi, "integrityCheck").mockResolvedValue({
      ok: true,
      status: 200,
      source: "live",
      data: {
        healthy: false,
        sqlite_integrity_ok: true,
        foreign_key_violations: 0,
        total_issues: 2,
        critical_issues: 1,
        warning_issues: 1,
        findings: [
          {
            kind: "dangling_edge",
            severity: "critical",
            safety: "safe_auto",
            count: 1,
            sample_ids: ["e-1"],
            detail: "Edges referencing non-existent nodes.",
            recommended_action: "Delete dangling edges.",
          },
        ],
        counts_by_kind: { dangling_edge: 1 },
      },
    });
    const repairSpy = vi.spyOn(brainGovernanceApi, "integrityRepair").mockResolvedValue({
      ok: true,
      status: 200,
      source: "live",
      data: {
        dry_run: true,
        plan: {
          dry_run: true,
          blast_radius: {
            rows_deleted: 1,
            rows_updated: 0,
            tables_touched: ["edges"],
            safe_auto_actions: 1,
            approval_gated_actions: 0,
          },
          actions: [
            {
              action_id: "delete_dangling_edges",
              kind: "dangling_edge",
              safety: "safe_auto",
              estimated_rows: 1,
              description: "Delete dangling edges.",
              will_execute: true,
            },
          ],
        },
        executed_actions: [],
        skipped_approval_actions: [],
        rows_repaired: 0,
        post_check_total_issues: 1,
      },
    });

    renderWithQuery(<BrainGovernanceHub language="en" />);
    await waitFor(() => expect(screen.getByText("dangling_edge")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("integrity-dry-run-btn"));
    await waitFor(() => expect(screen.getByTestId("integrity-blast-radius")).toBeInTheDocument());
    expect(repairSpy).toHaveBeenCalledWith({
      dry_run: true,
      approved_actions: [],
      global: false,
    });

    fireEvent.click(screen.getByTestId("integrity-execute-btn"));
    await waitFor(() =>
      expect(repairSpy).toHaveBeenCalledWith({
        dry_run: false,
        approved_actions: [],
        global: false,
      }),
    );
  });

  it("renders Entity Merge Review & Rollback and triggers merge + rollback actions", async () => {
    vi.spyOn(brainGovernanceApi, "integrityCheck").mockResolvedValue({
      ok: true,
      status: 200,
      source: "live",
      data: {
        healthy: true,
        sqlite_integrity_ok: true,
        foreign_key_violations: 0,
        total_issues: 0,
        critical_issues: 0,
        warning_issues: 0,
        findings: [],
        counts_by_kind: {},
      },
    });
    vi.spyOn(brainGovernanceApi, "entityCandidates").mockResolvedValue({
      ok: true,
      status: 200,
      source: "live",
      data: {
        workspace_id: "personal",
        candidates: [
          {
            canonical_id: "n-1",
            duplicate_id: "n-2",
            canonical_title: "Atlas Corp",
            duplicate_title: "Atlas Corporation",
            node_type: "Organization",
            workspace_id: "personal",
            decision: "review_candidate",
            signals: {
              exact_key_match: false,
              surface_similarity: 0.85,
              token_jaccard: 0.5,
              same_type: true,
              provenance_overlap: 0.5,
              relational_overlap: 0.4,
              temporal_proximity: 0.9,
              combined_score: 0.82,
              homonym_risk: false,
            },
            rationale: "High surface similarity and shared provenance",
          },
        ],
        recent_merges: [
          {
            merge_id: "emerge:abc",
            workspace_id: "personal",
            canonical_id: "n-10",
            duplicate_id: "n-11",
            actor: "user@example.com",
            rationale: "Confirmed duplicate",
            created_at: "2026-11-24T00:00:00Z",
            rolled_back_at: null,
          },
        ],
      },
    });
    const mergeSpy = vi.spyOn(brainGovernanceApi, "entityMerge").mockResolvedValue({
      ok: true,
      status: 200,
      source: "live",
      data: {
        merge_id: "emerge:new",
        workspace_id: "personal",
        canonical_id: "n-1",
        duplicate_id: "n-2",
        actor: "user@example.com",
        rationale: "High surface similarity and shared provenance",
        created_at: "2026-11-24T00:00:00Z",
      },
    });
    const rollbackSpy = vi.spyOn(brainGovernanceApi, "entityRollback").mockResolvedValue({
      ok: true,
      status: 200,
      source: "live",
      data: {
        merge_id: "emerge:abc",
        workspace_id: "personal",
        canonical_id: "n-10",
        duplicate_id: "n-11",
        actor: "user@example.com",
        rationale: "Confirmed duplicate",
        created_at: "2026-11-24T00:00:00Z",
        rolled_back_at: "2026-11-24T01:00:00Z",
      },
    });

    renderWithQuery(<BrainGovernanceHub language="en" />);
    fireEvent.click(screen.getByRole("tab", { name: "Entity Merges" }));
    await waitFor(() => expect(screen.getByTestId("entity-merge-panel")).toBeInTheDocument());
    await waitFor(() =>
      expect(screen.getByTestId("merge-candidate-n-2")).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByTestId("merge-candidate-n-2"));
    await waitFor(() => expect(mergeSpy).toHaveBeenCalled());

    fireEvent.click(screen.getByTestId("rollback-merge-emerge:abc"));
    await waitFor(() =>
      expect(rollbackSpy).toHaveBeenCalledWith({
        merge_id: "emerge:abc",
        add_do_not_merge: true,
      }),
    );
  });

  it("renders Knowledge Corrections panel and supports create, confirm, and revert", async () => {
    vi.spyOn(brainGovernanceApi, "integrityCheck").mockResolvedValue({
      ok: true,
      status: 200,
      source: "live",
      data: {
        healthy: true,
        sqlite_integrity_ok: true,
        foreign_key_violations: 0,
        total_issues: 0,
        critical_issues: 0,
        warning_issues: 0,
        findings: [],
        counts_by_kind: {},
      },
    });
    vi.spyOn(brainGovernanceApi, "listCorrections").mockResolvedValue({
      ok: true,
      status: 200,
      source: "live",
      data: {
        workspace_id: "personal",
        external_training_upload_allowed: false,
        corrections: [
          {
            correction_id: "corr:1",
            workspace_id: "personal",
            target_kind: "fact",
            target_id: "fact-1",
            status: "pending",
            actor: "user@example.com",
            reason: "Fix port number",
            before_snapshot: { summary: "Port 8000" },
            after_snapshot: { summary: "Port 4825" },
            reindex_triggered: false,
            local_only: true,
            external_training_upload_allowed: false,
            created_at: "2026-11-24T00:00:00Z",
            updated_at: "2026-11-24T00:00:00Z",
          },
          {
            correction_id: "corr:2",
            workspace_id: "personal",
            target_kind: "fact",
            target_id: "fact-2",
            status: "confirmed",
            actor: "user@example.com",
            reason: "Updated owner",
            before_snapshot: { summary: "Kim" },
            after_snapshot: { summary: "Lee" },
            reindex_triggered: true,
            local_only: true,
            external_training_upload_allowed: false,
            created_at: "2026-11-24T00:00:00Z",
            updated_at: "2026-11-24T00:00:00Z",
          },
        ],
      },
    });
    const createSpy = vi.spyOn(brainGovernanceApi, "createCorrection").mockResolvedValue({
      ok: true,
      status: 200,
      source: "live",
      data: {
        correction_id: "corr:3",
        workspace_id: "personal",
        target_kind: "fact",
        target_id: "fact-3",
        status: "pending",
        actor: "user@example.com",
        reason: "Clarify spec",
        before_snapshot: {},
        after_snapshot: { summary: "Updated summary" },
        reindex_triggered: false,
        local_only: true,
        external_training_upload_allowed: false,
        created_at: "2026-11-24T00:00:00Z",
        updated_at: "2026-11-24T00:00:00Z",
      },
    });
    const statusSpy = vi.spyOn(brainGovernanceApi, "updateCorrectionStatus").mockResolvedValue({
      ok: true,
      status: 200,
      source: "live",
      data: {} as never,
    });
    const revertSpy = vi.spyOn(brainGovernanceApi, "revertCorrection").mockResolvedValue({
      ok: true,
      status: 200,
      source: "live",
      data: {} as never,
    });

    renderWithQuery(<BrainGovernanceHub language="en" />);
    fireEvent.click(screen.getByRole("tab", { name: "Corrections" }));
    await waitFor(() =>
      expect(screen.getByTestId("knowledge-corrections-panel")).toBeInTheDocument(),
    );

    fireEvent.change(screen.getByTestId("correction-target-id"), { target: { value: "fact-3" } });
    fireEvent.change(screen.getByTestId("correction-reason"), { target: { value: "Clarify spec" } });
    fireEvent.change(screen.getByTestId("correction-after-summary"), {
      target: { value: "Updated summary" },
    });
    fireEvent.click(screen.getByTestId("correction-submit-btn"));
    await waitFor(() => expect(createSpy).toHaveBeenCalled());

    fireEvent.click(screen.getByTestId("confirm-correction-corr:1"));
    await waitFor(() => expect(statusSpy).toHaveBeenCalledWith("corr:1", { status: "confirmed" }));

    fireEvent.click(screen.getByTestId("revert-correction-corr:2"));
    await waitFor(() => expect(revertSpy).toHaveBeenCalledWith("corr:2"));
  });

  it("renders Embedding Registry & Re-Index Progress and triggers cutover/rollback", async () => {
    vi.spyOn(brainGovernanceApi, "integrityCheck").mockResolvedValue({
      ok: true,
      status: 200,
      source: "live",
      data: {
        healthy: true,
        sqlite_integrity_ok: true,
        foreign_key_violations: 0,
        total_issues: 0,
        critical_issues: 0,
        warning_issues: 0,
        findings: [],
        counts_by_kind: {},
      },
    });
    vi.spyOn(brainGovernanceApi, "embeddingRegistry").mockResolvedValue({
      ok: true,
      status: 200,
      source: "live",
      data: {
        models: [
          {
            model_id: "BAAI/bge-m3",
            version: "1.0.0",
            provider: "sentence_transformers",
            dimensions: 1024,
            normalization: "l2",
            distance_metric: "cosine",
            languages: ["en", "ko"],
            modalities: ["text"],
            semantic: true,
            status: "active",
          },
        ],
        generations: [
          {
            generation_id: "gen-2",
            model_id: "BAAI/bge-m3",
            model_version: "1.0.0",
            dimensions: 1024,
            normalization: "l2",
            status: "evaluated_pass",
            processed_items: 100,
            total_items: 100,
            shadow_recall_at_5: 0.94,
            active_recall_at_5: 0.89,
            created_at: "2026-11-24T00:00:00Z",
            updated_at: "2026-11-24T00:00:00Z",
          },
        ],
      },
    });
    const cutoverSpy = vi.spyOn(brainGovernanceApi, "embeddingCutover").mockResolvedValue({
      ok: true,
      status: 200,
      source: "live",
      data: { ok: true },
    });
    const rollbackSpy = vi.spyOn(brainGovernanceApi, "embeddingRollback").mockResolvedValue({
      ok: true,
      status: 200,
      source: "live",
      data: { ok: true },
    });

    renderWithQuery(<BrainGovernanceHub language="en" />);
    fireEvent.click(screen.getByRole("tab", { name: "Embeddings" }));
    await waitFor(() => expect(screen.getByTestId("embedding-registry-panel")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText("BAAI/bge-m3")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("cutover-gen-gen-2"));
    await waitFor(() => expect(cutoverSpy).toHaveBeenCalledWith({ generation_id: "gen-2" }));

    fireEvent.click(screen.getByTestId("embedding-rollback-btn"));
    await waitFor(() => expect(rollbackSpy).toHaveBeenCalled());
  });

  it("renders Knowledge Clusters & Gaps Explorer", async () => {
    vi.spyOn(brainGovernanceApi, "integrityCheck").mockResolvedValue({
      ok: true,
      status: 200,
      source: "live",
      data: {
        healthy: true,
        sqlite_integrity_ok: true,
        foreign_key_violations: 0,
        total_issues: 0,
        critical_issues: 0,
        warning_issues: 0,
        findings: [],
        counts_by_kind: {},
      },
    });
    vi.spyOn(brainGovernanceApi, "topicClusters").mockResolvedValue({
      ok: true,
      status: 200,
      source: "live",
      data: {
        workspace_id: "personal",
        clusters: [
          {
            cluster_id: "cluster-atlas",
            size: 2,
            members: [
              { id: "n1", title: "Atlas Core", type: "Project" },
              { id: "n2", title: "SQLite WAL", type: "Concept" },
            ],
          },
        ],
      },
    });
    vi.spyOn(brainGovernanceApi, "knowledgeGaps").mockResolvedValue({
      ok: true,
      status: 200,
      source: "live",
      data: {
        workspace_id: "personal",
        isolated_entities_count: 1,
        missing_provenance_count: 1,
        isolated_entities: [{ id: "n3", title: "Orphan Note", type: "Note" }],
        missing_provenance: [{ id: "n4", title: "Ungrounded Fact", type: "Fact" }],
      },
    });

    renderWithQuery(<BrainGovernanceHub language="en" />);
    fireEvent.click(screen.getByRole("tab", { name: "Clusters & Gaps" }));
    await waitFor(() => expect(screen.getByTestId("clusters-gaps-panel")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText("cluster-atlas")).toBeInTheDocument());
    expect(screen.getByText("Orphan Note (Note)")).toBeInTheDocument();
    expect(screen.getByText("Ungrounded Fact (Fact)")).toBeInTheDocument();
  });
});
