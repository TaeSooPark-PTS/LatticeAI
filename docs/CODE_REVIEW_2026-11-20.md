[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./CODE_REVIEW_2026-11-20.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](./ko/CODE_REVIEW_2026-11-20.md)

# Lattice AI Comprehensive Code Review & Improvement Roadmap

> Review Date: 2026-11-20  
> Repository: https://github.com/TaeSooPark-PTS/LatticeAI  
> Reviewed Branch: `main`  
> Reviewed Commit: `360892d320f9ac018229ca453e4cd86cad68cbb3`  
> Repository File Count: 2,041 (based on Git tree; count may vary depending on directory/file grouping criteria)

---

## 1. Purpose and Scope

Rather than summarizing READMEs or Markdown documentation alone, this document provides a static code review grounded in the core implementation files and project components across the Lattice AI Git tree.

Primary areas reviewed:

- Rust Workspace and Brain Core
- Knowledge Graph / GraphWriter / Schema
- Retrieval / BM25F / Vector / GraphRAG
- Embedding Providers and Index Contract
- Agent Kernel / Agent Loop / Adaptive Harness
- Rust Host / Gateway / Python Worker
- React / TypeScript Frontend
- Tauri Desktop / CLI / VS Code & Browser Extensions
- Security / Workspace Isolation / Approval
- GitHub Actions CI/CD / Testing and Release Pipeline

### Review Limitations

This is a static review of repository source code and documentation. Full test suites were not executed within this review session, desktop apps were not installed on physical macOS/Windows targets, and large-scale dataset benchmarks were not run. Accordingly, findings should be interpreted in three categories:

1. **Observed Structure:** Verified directly from files and implementation.
2. **Needs Verification:** Structural risks or areas requiring additional runtime testing.
3. **Recommendations:** Suggested improvements aligned with product goals and long-term operability.

Benchmark figures or performance gains are not claimed without reproducible experiments. This document does not substitute for a formal security audit or runtime quality certification.

---

## 2. Executive Assessment

Lattice AI spans a broader platform architecture than a single chatbot or basic RAG application, combining a Rust Host/Core, a Python AI Worker, React/Tauri clients, Knowledge Graph + Hybrid Retrieval, and an Agent execution layer.

The most important strategic priority is **completing Brain accuracy, consistency, recoverability, and measurability before adding further features.**

### Strengths

- Clear separation between Rust product core paths and the Python AI compute layer
- Centralized knowledge writes through `GraphWriter`
- Hybrid Retrieval combining BM25F, Vector search, and GraphRAG
- Explicit index contracts and compatibility considerations for embedding models
- Adaptive Harness probing runtime execution capability rather than inferring from model names alone
- Split Core/Runtime Rust CI jobs with Cargo cache and `cargo-nextest`
- Local-first and user-approval-first product direction

### Key Risks

1. Are evaluation datasets and regression suites sufficient to objectively measure retrieval and answer quality?
2. Does the Knowledge Graph accurately manage duplicates, conflicts, and temporal updates?
3. Are existing indexes and data preserved safely across embedding model migrations?
4. Does evaluation distinguish between tool execution success and final answer correctness?
5. Are data contracts and fault recovery between Rust Host and Python Worker sufficiently tested?
6. Are workspace permissions enforced consistently across API, search, Graph Expansion, files, and Agent tools?
7. Are full install-to-shutdown-and-recovery lifecycles verified on physical Windows and macOS environments?

---

## 3. Architecture Review

Conceptual overview of the observed architecture:

```text
React / TypeScript / Tauri / CLI / VS Code / Browser Extension
                         |
                         v
                  Rust Host / Gateway
                         |
            +------------+------------+
            |                         |
            v                         v
      Rust Brain Core           Python AI Worker
      - Graph / Storage          - LLM Provider
      - GraphWriter              - Embedding Provider
      - Retrieval                - Parsing / AI Tasks
      - Jobs / Auth
            |
            v
      Local Knowledge Store
      - SQLite
      - Graph / Metadata
      - Embedding Index
      - Provenance / Archives
```

### Assessment

Keeping Rust on data/product critical paths and Python on model/AI compute is sound. However, separating languages alone does not enforce clean boundaries; API contracts, data ownership, version compatibility, error handling, and retry policies must be defined together.

### Recommendations

- Maintain a single source of truth for Request/Response schemas across Rust and Python.
- Run API Contract Tests and Rust/Python Parity Tests in CI.
- Document database write owners and read models explicitly.
- Integration-test worker failures, restarts, version mismatches, and timeouts.
- Gradually separate legacy data-handling logic in Python from true AI compute logic.
- Standardize job IDs, correlation IDs, error codes, cancellation, and retry rules across process boundaries.

---

## 4. Detailed Sector Reviews

## 4.1 Knowledge Graph / Brain Core

Key paths:
- `rust/lattice-core/`
- `rust/lattice-core/src/graph_write/`
- `rust/lattice-core/src/graph_write/schema.rs`
- `lattice_brain/`

### Strengths

- Centralizing write paths around the Rust `GraphWriter`
- Transactional SQLite mutations
- Node-and-edge knowledge relationship model
- Design accounting for provenance and knowledge lifecycle
- Compatibility with existing Python schemas

### Gaps and Risks

**1) Dual Schema Management Risk**

Duplicating schema definitions or data contracts between Rust and Python risks drift in fields, constraints, defaults, and migration behavior over time.

**Recommendations**
- Manage Schema Versions and Migration Registries explicitly
- Add pre- and post-migration Schema Parity Tests
- Run upgrade tests from older DB versions to latest
- Test rollback/recovery upon migration failure or interruption
- Document backward/forward compatibility policies

**2) Managing Knowledge Changes Over Time**

The same fact may be ingested at different times; storing active and historical facts as separate nodes alone is insufficient.

**Recommendations**
- Define temporal semantics (`valid_from`, `valid_to`, `observed_at`)
- Standardize `Active` / `Superseded` / `Expired` lifecycle policies
- Track Source Confidence and Evidence Provenance
- Support conflict detection and user confirmation flows
- Preserve original provenance when merging duplicate facts
- Provide knowledge revision history and rollback

**3) Relationship Quality**

Inaccurate Entity Resolution or relation extraction can pollute GraphRAG results.

**Recommendations**
- Build an Entity Deduplication evaluation set
- Measure Relation Extraction Precision/Recall
- Support correction/deletion policies to revert bad edges
- Store supporting evidence and provenance per edge
- Distinguish automatic merges from user-approved merges

**Recommended Priority: P0/P1**

---

## 4.2 RAG / Retrieval Engine

Key paths:
- `rust/lattice-retrieval/`
- `rust/lattice-retrieval/src/bm25/`
- `rust/lattice-retrieval/src/bm25/sidecar.rs`
- `rust/lattice-retrieval/src/bm25/graph_rag.rs`
- `rust/lattice-retrieval/src/bm25/scoring.rs`

### Strengths

- Field-weighted BM25F retrieval
- Hybrid integration with Vector Semantic Search
- Relationship expansion via GraphRAG
- Reciprocal Rank Fusion (RRF) result merging
- Sidecar Index and Incremental Indexing architecture
- Separating historical knowledge from active retrieval candidates
- Vector Exact Rescore for high-precision reranking

### Gaps and Risks

As retrieval logic grows more sophisticated, code complexity diverges from actual search quality. An objective measurement harness verifying whether required evidence lands in top results is essential.

**Recommendations**
- Build a fixed Query/Expected Evidence evaluation dataset
- Measure Recall@5/10, MRR, and nDCG
- Compare BM25F-only, Vector-only, GraphRAG-only, and Hybrid performance
- Run regression checks on RRF parameter changes
- Compare precision before and after Graph Expansion
- Test Active vs. Superseded knowledge separation
- Evaluate Korean spacing, particles, compound nouns, and mixed-script queries
- Test workspace boundaries and access control during retrieval
- Track retrieval latency (p50/p95/p99) and index footprint

### Recommended Evaluation Dataset

Include at least the following categories:

| Evaluation Type | Example |
|---|---|
| Exact Match | Document titles, product names, IDs |
| Semantic | Paraphrased queries with identical meaning |
| Multi-hop | Questions chaining two or more graph relations |
| Temporal | Distinguishing superseded values from current values |
| Conflict | Contradictory claims across different sources |
| Korean | Particles, spacing, compound nouns |
| Scope | Blocking cross-workspace leakage |
| No-answer | Abstaining honestly when evidence is absent |

**Recommended Priority: P0**

---

## 4.3 Embedding / Vector Index

Key paths:
- `latticeai/core/embedding_providers/`
- `latticeai/core/embedding_providers/text.py`
- `rust/lattice-core/src/embeddings/`
- `rust/lattice-retrieval/`

### Strengths

- Support for multiple embedding providers
- Explicit contract tracking model identity and vector dimensions
- Extensibility across local models and multiple runtimes

### Gaps and Risks

**1) Mixing Indexes During Model Changes**

When the embedding model changes, legacy vectors and newly generated vectors must never be assumed to share the same vector space.

**Recommendations**
1. Build a separate index for the new model
2. Maintain the existing index in parallel during transition
3. Validate dimension, normalization, and model contract on the new index
4. Compare retrieval quality against the evaluation suite
5. Atomically switch the Active Index once verified
6. Fall back to the previous index on failure

**2) Default and Fallback Embedding Quality**

Hash/subword fallbacks are helpful for offline operation and functional smoke tests, but do not guarantee semantic retrieval quality. Active provider vs. fallback state should be clearly surfaced to users.

**3) Large-Scale Processing Stability**

- Batch embedding
- Content-hash deduplication
- Incremental reindexing
- Resumability after interruption
- Memory and batch-size bounds
- Model download integrity checks
- Index build progress reporting and cancellation
- Corrupted index recovery

**Recommended Priority: P1**

---

## 4.4 Agent / Adaptive Harness

Key paths:
- `rust/lattice-agent/`
- `rust/lattice-agent/src/kernel/`
- `rust/lattice-agent/src/kernel/probe.rs`
- `rust/lattice-agent/src/kernel/agentloop/`
- `rust/lattice-agent/src/kernel/agentloop/execution/`

### Strengths

- Probing runtime capabilities rather than assuming them from model names or parameter counts
- Dynamic tool ranking and execution-path selection
- JSON repair and execution verification
- Guided Mode adapting to measured model capability
- Integrating outcome and grounding verification into the Agent Loop

### Benchmark Interpretation Caveat

The 25-task Qwen2.5 0.5B benchmark recorded in `docs/ADAPTIVE_HARNESS_BENCHMARK.md` is valuable as initial validation, but should not be over-extrapolated as universal proof across all models because it covers a single model and a small per-category task count.

Loopback and deterministic UI fixture scores should also be clearly distinguished from live internet and real OS Computer Use success rates.

### Recommendations

- Evaluate across multiple parameter scales (0.5B, 2B, 7B, 14B+)
- Compare across model families (Qwen, Gemma, Llama, etc.)
- Measure quantization impact (Q4, Q5, Q8) on accuracy and latency
- Expand task counts and run multi-trial evaluations
- Operate separate live-web and real-OS Computer Use suites
- Separate Tool Execution Success from Task Outcome Correctness
- Measure Answer Quality independently from RAG Grounding
- Preserve reproducible regression cases for every failure class
- Measure correlation between Probe overhead and end-to-end execution quality

### Agent Experience Memory Proposal

To reuse prior failure experience across runs, persist structured records capturing:

- Model and runtime version
- Tool and input shape
- Failure stage and error code
- Retry outcome
- Successful recovery strategy
- Scope and confidence of the recovery strategy

Never promote a single failure into a global rule; require repeated observation and evaluation before applying policy updates.

**Recommended Priority: P1**

---

## 4.5 Security / Privacy / Governance

Key paths:
- `rust/lattice-agent/src/kernel/`
- `rust/lattice-host/src/gateway/`
- `rust/lattice-auth/`
- `SECURITY.md`
- `PRIVACY.md`

### Strengths

- Local-first architecture
- Approval-gated execution design
- Workspace boundary and authentication layers
- Worker access restrictions
- Review-first approval workflow for file mutations and executions

### Areas for Improvement & Verification

| Area | Recommendation |
|---|---|
| Prompt Injection | Isolate instructions inside docs/web/email as untrusted data |
| Tool Permission | Enforce least privilege and explicit scopes per tool |
| Workspace Isolation | Verify isolation across API, retrieval, graph, file, and agent paths |
| Archive | Defend against ZIP Slip, symlinks, zip bombs, and path traversal |
| Secret | Scrub sensitive data from logs, errors, and diagnostic bundles |
| Network | Verify SSRF and loopback access policies |
| Approval | Re-verify target files/resources haven't mutated post-approval |
| Audit | Log permission changes, data mutations, and tool execution history |
| Restore | Verify backup integrity and permissions after restore |

In particular, Prompt Injection tests must confirm that malicious documents instructing file deletion or external exfiltration are treated strictly as untrusted data and never executed without explicit authorization and user approval.

**Recommended Priority: P0/P1**

---

## 4.6 Backend / Rust Host / Python Worker

Key paths:
- `rust/lattice-host/`
- `rust/lattice-host/src/gateway/`
- `latticeai/`
- `latticeai/worker_app.py`
- `latticeai/app_factory.py`

### Strengths

- Clear role split between product Gateway and AI Worker
- Constraining Python Worker routes while keeping product paths in Rust Host
- Synchronized API schemas and fixtures

### Recommendations

**Fault Recovery**
- Worker Health Checks
- Distinguish Startup Readiness from Liveness
- Request Timeouts
- Retry and Backoff policies
- Circuit Breakers
- Graceful Shutdown
- Host/Worker version mismatch handling
- Duplicate execution prevention and Idempotency Keys
- Resource cleanup on cancelled jobs

**Contracts & Compatibility**
- Single source of truth for OpenAPI/Schema
- Contract Tests
- Backward compatibility checks for older clients
- Python-Rust Parity Tests
- Standardized error codes and messages

**Dependencies**
- Keep only Python packages required on the live Worker path
- Separate dev, test, and runtime dependencies
- SBOM generation and vulnerability scanning

**Recommended Priority: P1/P2**

---

## 4.7 Frontend / React / TypeScript

Key paths:
- `frontend/src/`
- `frontend/src/App.tsx`
- `frontend/src/api/client.ts`
- `frontend/src/pages/`
- `frontend/src/features/`

### Strengths

- Lazy loading and route-level code splitting
- React Query for server state management
- Zustand for client state management
- Error Boundaries
- i18n architecture
- Vitest and Playwright test coverage

### Recommendations

- Separate App Root from Route Registry responsibilities
- Clarify feature-level layout and state boundaries
- Separate API hooks from domain logic
- Standardize progress, cancellation, and retry UX for long-running tasks
- Unify Loading / Empty / Error / Partial Success states
- Test accessibility and keyboard navigation
- Verify layout resilience across Korean/English string lengths
- Prevent mismatches between UI state and actual backend capability state

The core product flow should remain consistently visible:

```text
Capture -> Understand -> Connect -> Retrieve -> Apply -> Learn
```

A coherent workflow from capturing knowledge to understanding, connecting, and applying it matters more than raw feature count.

**Recommended Priority: P2**

---

## 4.8 Desktop / Tauri / CLI / Extensions

Key paths:
- `src-tauri/`
- `bin/`
- `vscode-extension/`
- `browser-extension/`

### Recommendations

- Prevent duplicate Host/Worker launches in Tauri
- Support recovery across process termination and app restarts
- Support rollback on failed app updates
- Provide structured CLI error codes and self-diagnostics
- Handle disconnects and automatic reconnection in VS Code and Browser extensions
- Retry and deduplicate failed captures
- Maintain a shared API contract across all clients
- Document feature parity/differences per surface

Automated verification should also ensure filenames and paths in READMEs match published release assets so users do not hit confusion during installation.

**Recommended Priority: P2**

---

## 4.9 CI/CD / Testing

Key paths:
- `.github/workflows/ci.yml`
- `scripts/run_rust_tests.mjs`
- `scripts/benchmark_adaptive_harness.py`

### Observed Strengths

- Separated Frontend, Python, and Rust test pipelines
- Split Rust Core and Runtime jobs
- Cargo caching
- `cargo-nextest` parallel execution
- Clippy and `rustfmt` checks
- Integration and Playwright visual smoke tests
- Pre-release validation gates

### Recommendations

**Test Execution Speed**
- Fast paths focused on changed crates
- Clear separation of Unit / Integration / E2E suites
- Per-test duration tracking
- Automatic identification of slow tests
- Eliminating redundant builds and recompilation
- Distinguishing PR Fast Gates from Release Full Gates

Optimize for faster developer feedback without removing full verification.

**Platforms**
- Expand Windows CI matrix coverage
- Clarify macOS Intel/Apple Silicon support scope
- Validate file paths, processes, SQLite, permissions, and file watching cross-platform
- Run smoke tests against installed distribution packages

**Supply Chain & Publishing**
- SBOM generation
- Artifact signing
- Dependency provenance
- Release manifests
- Post-install package smoke tests
- Idempotent publishing on retries

**Recommended Priority: P1/P2**

---

## 5. Prioritized Improvement Backlog

Execution backlog focused on reliability and quality rather than feature expansion.

### P0 — Core Quality Gates

| ID | Task | Acceptance Criteria |
|---|---|---|
| P0-01 | Build RAG Evaluation Dataset | Versioned fixed queries, expected evidence, and scoring criteria |
| P0-02 | Retrieval Benchmark | Automated comparison across BM25F/Vector/GraphRAG/Hybrid |
| P0-03 | Answer Grounding Evaluation | Measure evidence grounding and citation precision |
| P0-04 | Knowledge Conflict Tests | Verify separation of current, historical, and conflicting facts |
| P0-05 | Workspace Isolation Tests | Block cross-workspace access across search, graph, files, and tools |
| P0-06 | Prompt Injection Tests | Block execution of directives embedded in untrusted documents |

### P1 — Brain & Agent Reliability

| ID | Task | Acceptance Criteria |
|---|---|---|
| P1-01 | Graph Schema Migration Verification | Upgrade/rollback tests on existing DBs |
| P1-02 | Temporal Knowledge | Validate validity windows and revision history |
| P1-03 | Embedding Index Swap | Zero-downtime switching and rollback verification |
| P1-04 | Agent Multi-Model Benchmark | Compare multiple models, sizes, and quantizations |
| P1-05 | Agent Outcome Evaluation | Separate tool call success from final answer correctness |
| P1-06 | Worker Recovery Tests | Test shutdown, restart, and timeout recovery |
| P1-07 | Rust/Python Contract Tests | Detect schema and behavioral drift |

### P2 — Scalability & Product Polish

| ID | Task | Acceptance Criteria |
|---|---|---|
| P2-01 | Large-Scale Graph Benchmark | Measure 100K / 1M node workloads |
| P2-02 | Incremental Index Optimization | Reindex modified chunks only |
| P2-03 | Windows CI | Pass core user scenarios on Windows |
| P2-04 | Desktop Lifecycle | Verify install, update, shutdown, and recovery |
| P2-05 | UI Surface Parity | Document capability parity across Desktop/CLI/Extensions |
| P2-06 | SBOM & Artifact Signing | Verify release artifact provenance |

---

## 6. Recommended Execution Roadmap

### Phase 1 — Brain Reliability

- Knowledge Graph accuracy
- Temporal Knowledge
- RAG Evaluation Framework
- Citation and Grounding verification
- Embedding Index integrity

**Goal:** Measure and reduce errors where the Brain stores or retrieves incorrect knowledge.

### Phase 2 — Agent Intelligence

- Multi-model Adaptive Harness evaluation
- Tool failure recovery
- Agent Experience Memory
- Multi-agent coordination
- Final outcome quality evaluation

**Goal:** Evolve from an agent that merely invokes tools into one that produces verified, evidence-backed outcomes.

### Phase 3 — Performance & Scalability

- 100K–1M node benchmarks
- BM25F / Vector / Graph Expansion profiling
- Incremental indexing
- Memory and CPU profiling
- p50 / p95 / p99 latency tracking

**Goal:** Keep retrieval and indexing performance predictable as corpus size grows.

### Phase 4 — Platform Reliability

- Host/Worker fault recovery
- Workspace security verification
- Windows/macOS integration testing
- Backup/Restore verification
- Release artifact reliability

**Goal:** Ensure reliable operation on user machines from installation through recovery.

### Phase 5 — Digital Brain Intelligence

- Long-term experience accumulation
- Automated relationship generation and verification
- User context understanding
- Knowledge change detection and updates
- Proactive knowledge-driven workflows

**Goal:** Realize a continuously growing Digital Brain independent of any single AI model.

---

## 7. Proposed Performance & Quality Dashboard

Performance optimization should never rely on intuition or single-run timings; identical datasets and environments should be used across versions.

| Area | Metrics |
|---|---|
| Ingestion | Docs/sec, per-file processing time, failure rate |
| Parsing | Success rate by document type, extraction accuracy |
| Graph | Entity Precision/Recall, Relation Precision/Recall |
| Retrieval | Recall@K, MRR, nDCG |
| Grounding | Citation Precision, Evidence Coverage |
| Embedding | Throughput, memory usage, index swap duration |
| Agent | Task Success, Outcome Correctness, Retry Rate |
| Host/Worker | Startup time, fault recovery time, error rate |
| Frontend | Initial load time, core action latency |
| Storage | DB size, index size, backup/restore duration |

Persist the following metadata with every benchmark run:

- Lattice AI version and Git Commit
- OS and CPU/Memory spec
- Model and quantization version
- Dataset version and size
- Test configuration and iteration count
- Timestamp

---

## 8. Final Recommendation

Lattice AI's differentiation does not come from connecting the most models or shipping the most agent features.

The core pillars are:

1. **A Brain that remembers accurately:** Reliably managing knowledge, relations, time, and provenance.
2. **A Brain that retrieves accurately:** Finding necessary evidence while excluding stale or conflicting data.
3. **An Agent that applies knowledge:** Using grounded evidence to produce genuinely correct outcomes.

Recommended short-term development priority:

```text
Knowledge Integrity
       |
       v
Retrieval Evaluation
       |
       v
Answer Grounding
       |
       v
Agent Outcome Quality
       |
       v
Performance & Platform Reliability
```

Building automated quality gates to measure these pillars before adding new models or features will likely yield the highest return on effort.

**Final Verdict:** Rather than tearing down and rewriting the current technical foundation, pursue incremental improvements that preserve the existing Rust Core, Hybrid Retrieval, and Adaptive Harness while strengthening evaluation frameworks, data integrity, fault recovery, and real-world environment testing.

---

## 9. Reference Paths

Key reviewed paths:

- `rust/lattice-core/`
- `rust/lattice-retrieval/`
- `rust/lattice-agent/`
- `rust/lattice-host/`
- `latticeai/`
- `frontend/src/`
- `src-tauri/`
- `vscode-extension/`
- `browser-extension/`
- `.github/workflows/ci.yml`
- `docs/ADAPTIVE_HARNESS_BENCHMARK.md`
- `scripts/run_rust_tests.mjs`

Repository: https://github.com/TaeSooPark-PTS/LatticeAI

---

*This document represents a static code review and improvement proposal based on the repository state as of 2026-11-20. Completion of individual items should be verified independently through subsequent implementation and automated test results.*
