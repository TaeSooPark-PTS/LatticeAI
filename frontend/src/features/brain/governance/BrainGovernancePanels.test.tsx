import * as React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@/i18n/brain";
import * as baseApi from "@/api/base";
import { brainGovernanceApi } from "@/api/brainGovernance";
import { BrainGovernanceHub } from "./BrainGovernanceHub";

function renderWithQuery(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const EMPTY_HEALTHY_INTEGRITY = {
  ok: true,
  status: 200,
  source: "live" as const,
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
};

describe("brainGovernanceApi client methods", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("calls all Brain governance endpoints with expected paths and payloads", async () => {
    const getSpy = vi.spyOn(baseApi, "get").mockResolvedValue({
      ok: true,
      status: 200,
      source: "live",
      data: {} as never,
    });
    const postSpy = vi.spyOn(baseApi, "post").mockResolvedValue({
      ok: true,
      status: 200,
      source: "live",
      data: {} as never,
    });

    await brainGovernanceApi.integrityCheck();
    await brainGovernanceApi.integrityCheck(true);
    await brainGovernanceApi.integrityRepair({ dry_run: true });
    await brainGovernanceApi.entityCandidates();
    await brainGovernanceApi.entityMerge({ canonical_id: "a", duplicate_id: "b" });
    await brainGovernanceApi.entityRollback({ merge_id: "m1" });
    await brainGovernanceApi.listCorrections();
    await brainGovernanceApi.listCorrections("pending");
    await brainGovernanceApi.createCorrection({ target_id: "n1" });
    await brainGovernanceApi.updateCorrectionStatus("c1", { status: "confirmed" });
    await brainGovernanceApi.revertCorrection("c1");
    await brainGovernanceApi.embeddingRegistry();
    await brainGovernanceApi.embeddingCutover({ generation_id: "g1" });
    await brainGovernanceApi.embeddingRollback();
    await brainGovernanceApi.topicClusters();
    await brainGovernanceApi.knowledgeGaps();

    expect(getSpy).toHaveBeenCalledWith("/api/brain/integrity", expect.any(Object));
    expect(getSpy).toHaveBeenCalledWith("/api/brain/integrity?global=true", expect.any(Object));
    expect(getSpy).toHaveBeenCalledWith("/api/brain/entities/candidates", expect.any(Object));
    expect(getSpy).toHaveBeenCalledWith("/api/brain/corrections", expect.any(Object));
    expect(getSpy).toHaveBeenCalledWith("/api/brain/corrections?status=pending", expect.any(Object));
    expect(getSpy).toHaveBeenCalledWith("/api/brain/embeddings/registry", expect.any(Object));
    expect(getSpy).toHaveBeenCalledWith("/api/brain/clusters", expect.any(Object));
    expect(getSpy).toHaveBeenCalledWith("/api/brain/gaps", expect.any(Object));
    expect(postSpy).toHaveBeenCalledWith("/api/brain/integrity/repair", { dry_run: true }, expect.any(Object));
    expect(postSpy).toHaveBeenCalledWith("/api/brain/entities/merge", { canonical_id: "a", duplicate_id: "b" }, expect.any(Object));
    expect(postSpy).toHaveBeenCalledWith("/api/brain/entities/rollback", { merge_id: "m1" }, expect.any(Object));
    expect(postSpy).toHaveBeenCalledWith("/api/brain/corrections", { target_id: "n1" }, expect.any(Object));
    expect(postSpy).toHaveBeenCalledWith("/api/brain/corrections/c1/status", { status: "confirmed" }, expect.any(Object));
    expect(postSpy).toHaveBeenCalledWith("/api/brain/corrections/c1/revert", {}, expect.any(Object));
    expect(postSpy).toHaveBeenCalledWith("/api/brain/embeddings/cutover", { generation_id: "g1" }, expect.any(Object));
    expect(postSpy).toHaveBeenCalledWith("/api/brain/embeddings/rollback", {}, expect.any(Object));
  });
});

describe("BrainGovernanceHub & 5 dedicated Brain UI panels", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders Integrity Check & Repair across findings, dry-run, approval toggle, execute, failure, and healthy empty state", async () => {
    const checkSpy = vi.spyOn(brainGovernanceApi, "integrityCheck").mockResolvedValue({
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
          {
            kind: "missing_file_path",
            severity: "warning",
            safety: "requires_approval",
            count: 1,
            sample_ids: ["f-1"],
            detail: "Indexed local file missing.",
            recommended_action: "Prune missing file record.",
          },
        ],
        counts_by_kind: { dangling_edge: 1, missing_file_path: 1 },
      },
    });
    const repairSpy = vi
      .spyOn(brainGovernanceApi, "integrityRepair")
      .mockResolvedValueOnce({
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
              approval_gated_actions: 1,
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
              {
                action_id: "prune_missing_local_file_index",
                kind: "missing_file_path",
                safety: "requires_approval",
                estimated_rows: 1,
                description: "Prune missing file record.",
                will_execute: false,
              },
            ],
          },
          executed_actions: [],
          skipped_approval_actions: ["prune_missing_local_file_index"],
          rows_repaired: 0,
          post_check_total_issues: 2,
        },
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        source: "live",
        data: {
          dry_run: false,
          plan: {
            dry_run: false,
            blast_radius: {
              rows_deleted: 1,
              rows_updated: 0,
              tables_touched: [],
              safe_auto_actions: 1,
              approval_gated_actions: 0,
            },
            actions: [],
          },
          executed_actions: ["delete_dangling_edges"],
          skipped_approval_actions: [],
          rows_repaired: 1,
          post_check_total_issues: 0,
        },
      })
      .mockResolvedValueOnce({
        ok: false,
        status: 403,
        source: "unavailable",
        data: {} as never,
        error: "forbidden",
      });

    renderWithQuery(<BrainGovernanceHub language="en" />);
    await waitFor(() => expect(screen.getByText("dangling_edge")).toBeInTheDocument());
    expect(screen.getByText("missing_file_path")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(checkSpy).toHaveBeenCalledTimes(2));

    fireEvent.click(screen.getByTestId("integrity-dry-run-btn"));
    await waitFor(() => expect(screen.getByTestId("integrity-blast-radius")).toBeInTheDocument());

    const checkbox = screen.getByRole("checkbox");
    fireEvent.click(checkbox);
    expect(checkbox).toBeChecked();
    fireEvent.click(checkbox);
    expect(checkbox).not.toBeChecked();
    fireEvent.click(checkbox);

    fireEvent.click(screen.getByTestId("integrity-execute-btn"));
    await waitFor(() =>
      expect(repairSpy).toHaveBeenCalledWith({
        dry_run: false,
        approved_actions: ["prune_missing_local_file_index"],
        global: false,
      }),
    );

    fireEvent.click(screen.getByTestId("integrity-dry-run-btn"));
    await waitFor(() => expect(repairSpy).toHaveBeenCalledTimes(3));

    checkSpy.mockResolvedValueOnce(EMPTY_HEALTHY_INTEGRITY);
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() =>
      expect(
        screen.getByText("No integrity issues detected in the current workspace."),
      ).toBeInTheDocument(),
    );
  });

  it("renders Entity Merge Review & Rollback across populated, rolled-back, refresh, and empty states", async () => {
    vi.spyOn(brainGovernanceApi, "integrityCheck").mockResolvedValue(EMPTY_HEALTHY_INTEGRITY);
    const candSpy = vi.spyOn(brainGovernanceApi, "entityCandidates").mockResolvedValue({
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
            {
              merge_id: "emerge:old",
              workspace_id: "personal",
              canonical_id: "n-12",
              duplicate_id: "n-13",
              actor: "user@example.com",
              rationale: "Old merge",
              created_at: "2026-11-23T00:00:00Z",
              rolled_back_at: "2026-11-23T02:00:00Z",
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
    await waitFor(() => expect(screen.getByTestId("merge-candidate-n-2")).toBeInTheDocument());
    expect(screen.getByText("Rolled back")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("merge-candidate-n-2"));
    await waitFor(() => expect(mergeSpy).toHaveBeenCalled());

    fireEvent.click(screen.getByTestId("rollback-merge-emerge:abc"));
    await waitFor(() =>
      expect(rollbackSpy).toHaveBeenCalledWith({
        merge_id: "emerge:abc",
        add_do_not_merge: true,
      }),
    );

    candSpy.mockResolvedValue({
      ok: true,
      status: 200,
      source: "live",
      data: {
        workspace_id: "personal",
        candidates: [],
        recent_merges: [],
      },
    });
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() =>
      expect(screen.getByText("No duplicate entity candidates pending review.")).toBeInTheDocument(),
    );
    expect(screen.getByText("No audited entity merges recorded yet.")).toBeInTheDocument();
  });

  it("renders Knowledge Corrections panel across create, targetKind change, confirm, reject, revert, failure, and empty states", async () => {
    vi.spyOn(brainGovernanceApi, "integrityCheck").mockResolvedValue(EMPTY_HEALTHY_INTEGRITY);
    const listSpy = vi.spyOn(brainGovernanceApi, "listCorrections").mockResolvedValue({
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
              reason: "",
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
    const createSpy = vi
      .spyOn(brainGovernanceApi, "createCorrection")
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        source: "live",
        data: {} as never,
      })
      .mockResolvedValueOnce({
        ok: false,
        status: 403,
        source: "unavailable",
        data: {} as never,
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
      expect(screen.getByTestId("confirm-correction-corr:1")).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByTestId("confirm-correction-corr:1"));
    await waitFor(() => expect(statusSpy).toHaveBeenCalledWith("corr:1", { status: "confirmed" }));
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    await waitFor(() => expect(statusSpy).toHaveBeenCalledWith("corr:1", { status: "rejected" }));
    fireEvent.click(screen.getByTestId("revert-correction-corr:2"));
    await waitFor(() => expect(revertSpy).toHaveBeenCalledWith("corr:2"));

    listSpy.mockResolvedValue({
      ok: true,
      status: 200,
      source: "live",
      data: {
        workspace_id: "personal",
        external_training_upload_allowed: false,
        corrections: [],
      },
    });
    fireEvent.change(screen.getByLabelText("Target Kind"), { target: { value: "relationship" } });
    fireEvent.change(screen.getByTestId("correction-target-id"), { target: { value: "edge-1" } });
    fireEvent.change(screen.getByTestId("correction-reason"), { target: { value: "Clarify" } });
    fireEvent.change(screen.getByTestId("correction-after-summary"), {
      target: { value: "Updated summary" },
    });
    fireEvent.click(screen.getByTestId("correction-submit-btn"));
    await waitFor(() => expect(createSpy).toHaveBeenCalledTimes(1));

    const form = screen.getByTestId("correction-submit-btn").closest("form")!;
    fireEvent.submit(form);
    expect(createSpy).toHaveBeenCalledTimes(1);

    fireEvent.change(screen.getByTestId("correction-target-id"), { target: { value: "edge-2" } });
    fireEvent.submit(form);
    await waitFor(() => expect(createSpy).toHaveBeenCalledTimes(2));

    await waitFor(() => expect(listSpy).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.getByText("No knowledge corrections recorded yet.")).toBeInTheDocument(),
    );
  });

  it("renders Embedding Registry & Re-Index Progress across active/shadow models, cutover, rollback, refresh, and empty generations", async () => {
    vi.spyOn(brainGovernanceApi, "integrityCheck").mockResolvedValue(EMPTY_HEALTHY_INTEGRITY);
    const regSpy = vi
      .spyOn(brainGovernanceApi, "embeddingRegistry")
      .mockResolvedValueOnce({
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
            {
              model_id: "text-embedding-3-small",
              version: "1.0.0",
              provider: "openai",
              dimensions: 1536,
              normalization: "l2",
              distance_metric: "cosine",
              languages: ["en"],
              modalities: ["text"],
              semantic: true,
              status: "shadow",
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
            {
              generation_id: "gen-1",
              model_id: "BAAI/bge-m3",
              model_version: "1.0.0",
              dimensions: 1024,
              normalization: "l2",
              status: "active",
              processed_items: 0,
              total_items: 0,
              shadow_recall_at_5: null,
              active_recall_at_5: null,
              created_at: "2026-11-23T00:00:00Z",
              updated_at: "2026-11-23T00:00:00Z",
            },
          ],
        },
      })
      .mockResolvedValue({
        ok: true,
        status: 200,
        source: "live",
        data: {
          models: [],
          generations: [],
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
    await waitFor(() => expect(screen.getByText("BAAI/bge-m3")).toBeInTheDocument());
    expect(screen.getByText("text-embedding-3-small")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("cutover-gen-gen-2"));
    await waitFor(() => expect(cutoverSpy).toHaveBeenCalledWith({ generation_id: "gen-2" }));

    fireEvent.click(screen.getByTestId("embedding-rollback-btn"));
    await waitFor(() => expect(rollbackSpy).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(regSpy).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.getByText("No shadow re-index generations recorded yet.")).toBeInTheDocument(),
    );
  });

  it("renders Knowledge Clusters & Gaps Explorer across populated, refresh, and empty states", async () => {
    vi.spyOn(brainGovernanceApi, "integrityCheck").mockResolvedValue(EMPTY_HEALTHY_INTEGRITY);
    const clustersSpy = vi
      .spyOn(brainGovernanceApi, "topicClusters")
      .mockResolvedValueOnce({
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
      })
      .mockResolvedValue({
        ok: true,
        status: 200,
        source: "live",
        data: {
          workspace_id: "personal",
          clusters: [],
        },
      });
    const gapsSpy = vi
      .spyOn(brainGovernanceApi, "knowledgeGaps")
      .mockResolvedValueOnce({
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
      })
      .mockResolvedValue({
        ok: true,
        status: 200,
        source: "live",
        data: {
          workspace_id: "personal",
          isolated_entities_count: 0,
          missing_provenance_count: 0,
          isolated_entities: [],
          missing_provenance: [],
        },
      });

    renderWithQuery(<BrainGovernanceHub language="en" />);
    fireEvent.click(screen.getByRole("tab", { name: "Clusters & Gaps" }));
    await waitFor(() => expect(screen.getByText("cluster-atlas")).toBeInTheDocument());
    expect(screen.getByText("Orphan Note (Note)")).toBeInTheDocument();
    expect(screen.getByText("Ungrounded Fact (Fact)")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(clustersSpy).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(gapsSpy).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(screen.getByText("No multi-node topic clusters detected yet.")).toBeInTheDocument(),
    );
    expect(
      screen.getByText("No isolated entities or missing provenance detected."),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Brain Network" }));
    expect(screen.getByTestId("brain-network-panel")).toBeInTheDocument();
  });
});
