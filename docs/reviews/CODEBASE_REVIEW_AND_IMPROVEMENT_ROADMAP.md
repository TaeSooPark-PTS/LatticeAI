[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./CODEBASE_REVIEW_AND_IMPROVEMENT_ROADMAP.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](../ko/reviews/CODEBASE_REVIEW_AND_IMPROVEMENT_ROADMAP.md)

# Lattice AI Full Codebase Review & Improvement Roadmap

- **Review Date:** 2026-11-18
- **Repository:** https://github.com/TaeSooPark-PTS/LatticeAI
- **Review Scope:** Rust Core / Retrieval / Knowledge Graph / Agent / Ingestion / Python Worker / React Frontend / Tauri / Security / Tests / CI/CD
- **Document Purpose:** Actionable review specifying code improvement priorities and verification criteria

> **Review Scope Notice**
>
> This document is a static code review written from key source files and structure inspected on GitHub. It is not a line-by-line audit of every file in the repository, nor the result of running full local macOS builds, tests, and performance benchmarks. Accordingly, the scores below are qualitative assessments of code structure and implementation maturity rather than quantitative benchmarks. Actual defect status and impact should be reproduced and verified in a local environment.

---

## 1. Executive Summary

Lattice AI has grown beyond a simple RAG application into a foundational Digital Brain platform. Its primary strengths are Rust-centered data ownership, Hybrid Retrieval, Knowledge Graph, Provenance, controlled Agent execution, and Local-First security boundaries.

The most important current priority is not adding more features, but proving the quality of the Brain Engine — semantically integrating accumulated knowledge and generating accurate, grounded answers to real user questions.

### 1.1 Qualitative Assessment

| Area | Score | Assessment |
|---|---:|---|
| Overall Architecture | 8.5/10 | Strong structural foundation |
| Rust Core | 8.5/10 | Clear data ownership |
| RAG / Retrieval | 8.0/10 | Mature retrieval architecture |
| Knowledge Graph | 8.0/10 | Relational reasoning foundation in place |
| Agent System | 8.0/10 | Strong permissions and execution controls |
| Data Ingestion / Indexing | 7.5/10 | Needs stronger automation and multimodal depth |
| Python Worker | 8.0/10 | Clean compute-role separation |
| Frontend / UX | 7.5/10 | Feature-rich, needs tighter UX cohesion |
| Security / Privacy | 8.0/10 | Solid boundary design, needs deeper verification |
| Tests / CI/CD | 8.0/10 | Systematic, with uneven verification coverage |

**Note:** These scores do not measure runtime performance, test pass rates, or live AI answer quality.

### 1.2 Core Conclusions

- **Strengths:** Architecture, data ownership, retrieval engine, security boundaries
- **Gaps:** Live AI quality evaluation, semantic data understanding, automation, operational efficiency
- **Top Priorities:** Real RAG evaluation dataset, Entity Resolution, Temporal Knowledge, and evidence grounding verification

---

## 2. Overall Architecture Review

Key inspected components:

| Component | Role |
|---|---|
| React / Vite | User interface |
| Tauri | Desktop runtime shell |
| `lattice-host` | Product entrypoint and API gateway |
| `lattice-core` | SQLite, `GraphWriter`, data ownership |
| `lattice-retrieval` | RAG, hybrid search, Knowledge Graph queries |
| `lattice-ingest` | File ingestion, parsing, chunking |
| `lattice-agent` | Agent execution and security policies |
| `lattice-chat` | Conversation orchestration and Cloud lane |
| `lattice-jobs` | Indexing job management |
| Python Worker | LLM inference, embeddings, document rendering/parsing |

The Rust workspace consists of 9 crates.

### 2.1 Strengths

1. **One Door Architecture**
   - `rust/lattice-host/src/gateway/onedoor.rs`
   - Shares core state (`Store`, `GraphWriter`, `AuthState`, `WorkspaceResolver`) through a unified entrypoint.
   - Reduces duplicate DB connections and per-module state divergence while unifying the data write path.

2. **Rust-Centered Data Ownership**
   - Keeping compute in Python while entrusting data persistence to Rust favors long-term reliability.
   - Leverages Python's ML ecosystem for inference and processing while preserving core data integrity in Rust.

3. **Modular Responsibility Separation**
   - Splitting `lattice-agent`, `lattice-retrieval`, `lattice-ingest`, and related crates supports feature growth and maintainability.

### 2.2 Weaknesses & Improvements

1. **Cross-Domain Coupling**
   - Having `lattice-host` act directly as an adapter across many modules risks mixing wiring code with business logic over time.
   - Improvement: minimize direct cross-domain references, clarify Port/Adapter contracts, expand event-driven handoffs, and separate host composition from domain logic.

2. **Python Worker Fault Impact**
   - Because LLM inference, MLX, embeddings, and document parsing live in the Python Worker, worker failures can disrupt core workflows.
   - Improvement: fault isolation, per-request timeouts, restart policies, model-state recovery, and idempotent job retries.

3. **Comment vs. Reality Drift**
   - Top-of-file comments in `rust/lattice-core/src/db.rs` described the Python Worker as the single Knowledge Graph writer, whereas `GraphWriter` and `onedoor.rs` reflect Rust owning writes.
   - Improvement: synchronize stale comments and module docs with current architecture and consider static checks for structural invariants.

---

## 3. Rust Core & Data Storage

Inspected files:

- `rust/lattice-core/src/db.rs`
- `rust/lattice-core/src/graph_write/mod.rs`
- `rust/lattice-core/src/graph_write/ingest.rs`
- `rust/lattice-core/src/graph_write/provenance.rs`
- `rust/lattice-core/src/graph_write/vectors.rs`
- `rust/lattice-core/src/embedding_contract.rs`

### 3.1 Strengths

1. **Single-Writer `GraphWriter` Architecture**
   - Funneling all mutations through one write engine fits SQLite's concurrency model well.
   - Unifying mutation paths simplifies transaction boundaries and integrity enforcement.

2. **Provenance Tracking**
   - `provenance.rs` tracks where knowledge originated and which pipeline produced it.
   - Recording source file, content, and pipeline lineage is essential for Brain trustworthiness.

3. **Embedding Contract**
   - Explicitly tracking model ID, dimension, normalization, and passage/query modes is vital for safe model swaps and re-indexing.
   - Prevents silently mixing vectors from incompatible embedding spaces.

### 3.2 Weaknesses & Improvements

1. **Strengthen Data-Integrity Failure Scenarios**
   - Embedding failure after document persistence
   - Vector-write failure after embedding computation
   - Indexing triggered while a file is mid-edit
   - Duplicate / concurrent ingestion
   - Search requests arriving during backup restore
   - Improvement: add integration tests verifying atomicity, retries, compensating actions, and idempotency at each failure point.

2. **Measure SQLite Scalability**
   - SQLite does not need immediate replacement.
   - Benchmark search latency, write throughput, memory, WAL growth, and backup/restore times across scales:

| Data Scale | Verification Focus |
|---|---|
| 10K nodes | Search latency and indexing |
| 100K nodes | Memory footprint and cache behavior |
| 500K nodes | Graph traversal and vector search |
| 1M nodes | Concurrency and storage scaling |

3. **Potential Error Masking in Admin Metrics**
   - `graph_stats()` in `onedoor.rs` returned `0` via `unwrap_or(0)` when table queries failed.
   - Failing to distinguish an empty table from a SQL failure can report `0` even when real data exists.
   - Improvement: separate empty-table state from query errors, returning `unavailable` or an explicit error status on failure.

---

## 4. RAG & Retrieval Engine

Inspected files:

- `rust/lattice-retrieval/src/service_hybrid.rs`
- `rust/lattice-retrieval/src/hybrid.rs`
- `rust/lattice-retrieval/src/bm25/graph_rag.rs`
- `rust/lattice-retrieval/src/query_embed.rs`
- `rust/lattice-retrieval/src/vector_alignment.rs`
- `rust/lattice-retrieval/src/rag_eval.rs`

Retrieval currently combines BM25F, Vector Search, and Knowledge Graph expansion via RRF / Hybrid Fusion.

### 4.1 Strengths

1. **BM25F**
   - Supports field-weighted scoring across title, tags/headings, body, and metadata.
   - Korean particle handling, CJK bigrams, and identifier subword splitting suit mixed Korean/English knowledge corpora.

2. **GraphRAG**
   - `graph_rag.rs` executes 1-hop and 2-hop structural expansion.
   - Surfaces relevant documents connected through intermediate concepts even without direct lexical overlap.

3. **Vector Alignment**
   - Validates model ID and dimension contracts and distinguishes states like `reindex_required` for operational safety.

4. **Performance-Oriented Retrieval**
   - Combining HNSW candidate generation with exact cosine re-scoring balances speed and ranking fidelity.

### 4.2 Highest-Priority Improvement: Real RAG Quality Evaluation

`rust/lattice-retrieval/src/rag_eval.rs` tracks:

- Recall@5 / Recall@10
- NDCG@10
- Citation Precision / Recall
- Faithfulness
- Answer Relevance
- Superseded Exclusion Rate
- Korean Recall
- Cross-model Jaccard
- Search and indexing latency

However, two limitations must be separated clearly:

- Some evaluation semantic vectors are deterministic test vectors produced by `synthetic_semantic_vector()` rather than a real embedding model.
- `faithfulness_and_relevance()` computes token overlap between retrieved evidence and expected claims rather than judging real LLM-generated answers.

Consequently, the existing framework alone cannot prove live answer quality when wired to real Gemma or Qwen models.

### 4.3 Recommended Direction

Build a realistic RAG Evaluation Dataset containing real user questions and ground-truth evidence:

| Evaluation Area | Additions Needed |
|---|---|
| Retrieval | Gold-document hit rate on real user questions |
| Semantic Search | Synonyms, contextual phrasing, indirect questions |
| Korean | Particles, abbreviations, compound nouns |
| Multi-Document | Answers synthesizing multiple files |
| Temporal | Distinguishing superseded vs. current facts |
| Conflict | Separating contradictory memories |
| Generation | Answer factuality and citation alignment |
| Model Swap | Retrieval & answer quality comparison across models |

Always separate evaluation into two distinct tracks:

- **Retrieval Evaluation:** Did the system find the right memories?
- **Generation Evaluation:** Did the model produce an accurate, grounded answer from those memories?

---

## 5. Knowledge Graph & Brain Intelligence

Inspected files:

- `rust/lattice-core/src/graph_write/`
- `rust/lattice-retrieval/src/neighborhood_synthesis.rs`
- `rust/lattice-retrieval/src/bm25/graph_rag.rs`
- `frontend/src/pages/brain/GraphNodeInspector.tsx`
- `frontend/src/features/brain/MultiHopSynthesisView.tsx`
- `frontend/src/features/brain/ContradictionInspector.tsx`

### 5.1 Strengths

1. **2-Hop Reasoning**
   - Traverses stored graph relations and explains indirect connections via intermediate concepts.

2. **Contradiction Detection**
   - Uses `CONTRADICTS` and `SUPERSEDES` edges to detect and review conflicting knowledge.

3. **User-in-the-Loop Review**
   - Allowing users to keep old facts, supersede them, or retain time-scoped coexistence fits personal knowledge management.

### 5.2 Weaknesses

Relationship traversal alone cannot fully track how the same entity evolves over time and across contexts.

Example:

- January: Project A is built in Python.
- March: Decided to migrate Project A to Rust.
- May: Decided to keep a subset of Python modules.

The Brain must conclude that Project A is now Rust-centered with specific Python modules retained, citing sources and timestamps for each transition.

### 5.3 Improvement Proposals

| Capability | Target Direction |
|---|---|
| Entity Resolution | Consolidate identical people, projects, and concepts across aliases |
| Temporal Knowledge | Model knowledge validity and evolution over time |
| Confidence | Track confidence on extracted relations |
| Contradiction | Semantic contradiction detection |
| Causal Graph | Cause-and-effect relations |
| Knowledge Evolution | Creation, amendment, and retirement history |
| Contextual Synthesis | Cross-document contextual synthesis |

Prioritize **Entity Resolution** first so references to the same project under different names link to one canonical entity.

---

## 6. Data Ingestion & Indexing Pipeline

Inspected files:

- `rust/lattice-ingest/src/`
- `rust/lattice-core/src/graph_write/ingest.rs`
- `rust/lattice-core/src/graph_write/vectors.rs`
- `latticeai/api/worker_compute.py`
- `latticeai/runtime/build_phases/worker_profile.py`

### 6.1 Strengths

Current pipeline flow:

1. File and folder discovery
2. Change detection
3. Fingerprint / SHA-256 verification
4. Python Worker parsing and extraction
5. Chunking and metadata generation
6. Embedding generation
7. Rust `GraphWriter` persistence
8. Search index refresh

Skipping unchanged files, incremental processing, running embeddings outside write transactions, dead-letter/retry queues, and graph cleanup on deleted files are all strong engineering choices.

### 6.2 Weaknesses & Improvements

1. **Real-Time File Synchronization**
   - Folder watching currently relies on polling.
   - Improvement: OS-native file watcher + debounce + batching + reconciliation scan for dropped events + event deduplication.
   - Retain periodic full reconciliation scans alongside native OS events rather than removing reconciliation entirely.

2. **Multimodal Ingestion**
   - Having parser/embedding/ASR code paths differs from wiring them automatically into the live ingestion pipeline.
   - Architectural docs note that image/video multimodal HTTP endpoints are not yet fully wired; live wiring should be audited explicitly.
   - Improvement: image OCR/Vision, PDF page-image analysis, table/chart extraction, video frame analysis, audio/video timestamp linking, and image-to-document relation extraction.

3. **Ingestion Quality Management**
   - Duplicate document detection
   - Importance classification
   - Freshness scoring
   - Per-document extraction quality scores
   - Malformed parse detection
   - Re-ingestion prioritization

---

## 7. Agent Engine & Tool Execution

Inspected files:

- `rust/lattice-agent/src/kernel/permission.rs`
- `rust/lattice-agent/src/kernel/breaker.rs`
- `rust/lattice-agent/src/tools/sandbox.rs`
- `rust/lattice-agent/src/`
- `rust/lattice-platform/src/toolsurface/`

### 7.1 Strengths

- Permission modes: `strict`, `trusted`, `bypass`
- Circuit Breaker
- Pre-execution permission verification
- Path isolation / root confinement
- Change proposals and approval gates
- Execution audit trail
- Tool Registry
- MCP integration
- Hook-based execution governance

Separating **Permission Mode** from **Network Boundary** is critical: auto-approving local tool execution must never imply auto-approving outbound cloud transmission.

### 7.2 Weaknesses & Improvements

1. **Long-Running Task Management**
   - State checkpointing across 100-step tasks
   - Resuming failed runs
   - Intermediate artifact persistence
   - Inter-task dependencies
   - Sub-agent delegation
   - Final outcome verification
   - Improvement: evolve beyond a single-turn agent loop into a Durable Workflow architecture.

2. **Execution Sandbox Hardening**
   - `sandbox.rs` performs symlink-aware path validation.
   - TOCTOU race conditions between path check and file open require explicit verification.
   - Improvement: Unix `openat` / `O_NOFOLLOW` protections, Windows Reparse Point validation, per-process privilege isolation, and concurrent file-edit conflict detection.

3. **Multi-Agent Architecture**
   - Brain Orchestrator: overall context, planning, outcome verification
   - Research Agent: knowledge search and investigation
   - Knowledge Agent: memory creation and curation
   - Execution Agent: tool execution and automation
   - Verification Agent: evidence and result verification
   - All agents should share the same Brain rather than siloing separate per-agent memories.

---

## 8. Python Worker & Model Runtime

Inspected files:

- `latticeai/worker_app.py`
- `latticeai/api/worker_compute.py`
- `latticeai/runtime/build_phases/worker_profile.py`
- `latticeai/core/embedding_providers/text.py`
- `latticeai/services/model_runtime/download.py`

### 8.1 Strengths

- Python Worker focuses strictly on compute rather than managing overall product state.
- Clean separation of model catalog, embedding providers, download/cancellation, and document rendering.
- Accounts for public unauthenticated EmbeddingGemma 2 mirrors and quantized fallbacks.

### 8.2 Weaknesses & Improvements

1. **Runtime Model Compatibility Verification**
   - Static metadata checks alone cannot guarantee real loading and inference compatibility.
   - Improvement: verify live download, tokenizer compatibility, model load, vision inputs, inference, memory usage, and soak runs.

2. **Model Role Abstraction**
   - Generation Model
   - Embedding Model
   - Reranking Model
   - Vision Model
   - ASR Model
   - Extraction Model
   - Clearly surface whether swapping a model requires re-indexing and how it affects existing knowledge compatibility.

3. **Explicit Model Lifecycle States**
   - Track distinct lifecycle transitions: downloading → verifying → installed → loading → inference-ready → healthy operation.

---

## 9. Frontend & User Experience

Inspected files:

- `frontend/src/pages/Brain.tsx`
- `frontend/src/features/brain/BrainConversation.tsx`
- `frontend/src/features/brain/MultiHopSynthesisView.tsx`
- `frontend/src/pages/brain/GraphNodeInspector.tsx`
- `frontend/src/features/brain/ContradictionInspector.tsx`
- `frontend/src/features/brain/AnswerProof.tsx`
- `frontend/src/api/client.ts`

### 9.1 Strengths

- Route-level lazy loading
- Feature-scoped code organization
- React Query state management
- Structured API failure handling
- Korean/English i18n
- Error Boundaries
- Answer evidence / proof display
- Knowledge Graph inspector
- Inline change-proposal approval

### 9.2 Weaknesses & Improvements

1. **Feature-Centric UI Complexity**
   - End users should not have to understand BM25F, Vector Index, or Provenance internals.
   - Users care about accumulated knowledge, what the AI remembers, whether it is accurate, what changed, and why an answer is trustworthy.
   - Keep engine diagnostics tucked into expandable detail views.

2. **Unified Brain Chat + Knowledge Graph Flow**
   - Connect `question → relevant memory lookup → answer → source inspection → memory correction → graph update` into one seamless user journey.

3. **Visibility into Memory Formation**
   - Go beyond "indexing complete" to show what knowledge and relations were extracted, keeping the default view simple with progressive disclosure for details.

---

## 10. Tauri & Desktop Runtime

Inspected files:

- `src-tauri/src/main.rs`
- `src-tauri/src/backend.rs`
- `src-tauri/src/topology.rs`
- `rust/lattice-host/src/supervisor/`

### 10.1 Strengths

- Rust Host-centered startup topology
- Managed worker process spawning
- HTTP health checks
- Automatic restart
- Graceful shutdown
- Backend status reporting
- Loopback-only IPC

### 10.2 Weaknesses & Improvements

Real OS-level install and launch verification is needed across platforms:

| Environment | Verification Checklist |
|---|---|
| macOS | Apple Silicon, MLX, OS permissions, Gatekeeper |
| Windows | Process termination, path handling, model runtimes |
| Linux | System packages and GPU runtimes |
| Cross-Platform | Worker crashes, force-kill, recovery |

Build dedicated desktop install-and-launch smoke tests for both Windows and macOS.

---

## 11. Security & Privacy

Inspected files:

- `rust/lattice-auth/`
- `rust/lattice-agent/src/kernel/`
- `rust/lattice-platform/src/knowledge/network_boundary_guard.rs`
- `rust/lattice-chat/src/boundary.rs`
- `rust/lattice-chat/src/cloud/context.rs`
- `SECURITY.md`
- `.github/workflows/dependency-audit.yml`

### 11.1 Strengths

1. **Local-First Default**
   - Keeping user knowledge and conversations local by default aligns directly with the product mission.

2. **Separation of Permission Mode and Network Boundary**
   - Independently governs whether an agent may run local tools vs. whether knowledge may leave the machine for the cloud.

3. **Cloud Egress Controls**
   - Extracts minimal context instead of exporting the full Knowledge Graph, enforcing sensitivity filters, explicit consent, and token budgets.

### 11.2 Weaknesses & Improvements

1. **Exact Parity Between Cloud Preview and Outbound Payload**
   - What the user previews must match the exact payload transmitted.
   - Share a single Context Builder and add tests asserting byte/content equality between preview output and pre-send payloads.

2. **Rust Dependency Auditing**
   - `dependency-audit.yml` audits Python and npm vulnerabilities and generates SBOMs, but lacked `cargo audit` and Rust SBOM generation.
   - Improvement: add `cargo audit`, `cargo deny`, Rust CycloneDX SBOM, license policy checks, and scheduled dependency updates.

3. **Data Encryption at Rest**
   - Encrypting `.latticebrain` export archives is distinct from protecting the local SQLite database at rest.
   - Improvement: database encryption, key management/rotation, encrypted backups, temp-file hardening, log secret scrubbing, and OS Keychain integration.

---

## 12. Testing & CI/CD

Inspected files:

- `.github/workflows/ci.yml`
- `.github/workflows/release.yml`
- `.github/workflows/publish.yml`
- `.github/workflows/dependency-audit.yml`
- `scripts/run_rust_tests.mjs`
- `rust/lattice-retrieval/tests/rag_eval.rs`
- `tests/unit/test_worker_app.py`

### 12.1 Strengths

Current test portfolio:

| Test Layer | Target |
|---|---|
| Rust Unit | Individual crates |
| Rust Parity | Equivalence with legacy implementations |
| Python Unit | Worker and compute services |
| Frontend Vitest | Components and UI logic |
| Integration | Live Host + Worker wiring |
| Visual | Rendered UI verification |
| RAG Evaluation | Retrieval quality |
| Dependency Audit | Third-party CVE checks |
| Release Smoke | Packaged release artifacts |

Using Golden Fixtures to lock parity between legacy and Rust implementations is a strong discipline.

### 12.2 Weaknesses & Improvements

1. **Rust Test Runner Divergence**
   - `scripts/run_rust_tests.mjs` uses `cargo-nextest` for parallel execution when installed, whereas `.github/workflows/ci.yml` invoked `cargo test --workspace` directly.
   - Unify local and CI test execution:

   ```bash
   cargo nextest run --workspace --no-fail-fast
   cargo test --workspace --doc
   ```

   Validate `nextest` process isolation and SQLite test contention on CI runners first.

2. **Avoid Over-Freezing Golden Tests**
   - Separate test intents clearly:
     - Compatibility Goldens: contracts that must stay backward-compatible
     - Behavior Tests: correctness of current functionality
     - Quality Evaluations: live retrieval and answer accuracy
     - Performance Benchmarks: runtime efficiency

3. **Distinguish Code Coverage from Product Quality**
   - High line coverage does not guarantee accurate answers to real questions.
   - Track RAG accuracy, citation grounding, fault recovery, and latency alongside coverage.

4. **Rust CI Optimization**
   - Adopt `cargo-nextest`
   - Evaluate `cargo-chef` dependency layer caching
   - Analyze affected crate blast radius
   - Avoid unnecessary redundant runs while keeping full workspace verification mandatory before merge
   - Separate fast PR checks from scheduled full suites
   - Automatically measure and track slow tests

---

## 13. Top 5 Structural Gaps

### 1. Live RAG Quality Verification — Critical (P0)

Retrieval algorithms and evaluation scaffolding have matured, but end-to-end validation with real user queries and real models is still required.

### 2. Semantic Knowledge Understanding — Critical (P0)

Strengthen canonical entity consolidation, temporal state tracking, and multi-document contextual reasoning.

### 3. Automated Ingestion & Multimodality — High (P1)

Move beyond text-first indexing so images, video, audio, and structured tables accumulate seamlessly into the Brain.

### 4. Durable Long-Running Agent Execution — High (P1)

Evolve from single-turn question answering to planning, executing, recovering, and verifying long-horizon workflows.

### 5. Test Cost & Operational Efficiency — High (P1)

Optimize test architecture so verification time does not grow disproportionately with every new feature.

---

## 14. Forward Development Roadmap

### Phase 1. Complete the Brain Engine

**Recommended first priority**

| Workstream | Goal |
|---|---|
| RAG Evaluation | Real-query grounded evaluation |
| Entity Resolution | Canonical entity consolidation |
| Temporal Memory | Time-aware knowledge tracking |
| Confidence Scoring | Relation and fact confidence |
| Contradiction Engine | Semantic conflict detection |
| Context Synthesis | Multi-memory contextual synthesis |

### Phase 2. Universal Ingestion

| Workstream | Goal |
|---|---|
| Native File Watch | Real-time file change sync |
| Image Understanding | Visual content extraction / OCR |
| Video Understanding | Frame and scene analysis |
| Audio Understanding | Speech and conversation indexing |
| Document Structure | Tables, charts, and layout hierarchy |
| Auto Classification | Automatic knowledge categorization |

The core goal is effortless accumulation — the Brain builds structured memory automatically without manual curation.

### Phase 3. Agentic Brain

| Workstream | Goal |
|---|---|
| Durable Task State | Persistent workflow checkpointing |
| Multi-Agent | Specialized collaborating agents |
| Long-Running Workflow | Multi-step durable execution |
| Self Verification | Grounded outcome verification |
| Knowledge Maintenance | Automated memory hygiene |
| Proactive Intelligence | Anticipating useful context |

### Phase 4. Model-Agnostic Intelligence

Any model should plug into the exact same Brain without losing continuity:

```text
Gemma (Local) ─┐
Qwen (Local) ──┼── Universal Brain Interface
Cloud LLM ─────┘       │
                       ├── Context
                       ├── Retrieval
                       ├── Evidence
                       ├── Memory
                       └── Tools
                              │
                       Lattice Knowledge Core
                       ├── SQLite
                       ├── Knowledge Graph
                       ├── Provenance
                       └── Embeddings
```

An LLM must be a replaceable reasoning engine using the Brain — never the owner of the Brain. Swapping models must preserve knowledge, decisions, relationships, task history, and personal context.

---

## 15. Final Improvement Priority Table

| Rank | Initiative | Priority | Expected Impact |
|---:|---|---|---|
| 1 | Live RAG Quality Evaluation | P0 | Proven Brain accuracy |
| 2 | Entity Resolution | P0 | Unified knowledge graph |
| 3 | Temporal Knowledge | P0 | Time-aware memory |
| 4 | Retrieval & Claim Grounding Verification | P0 | Lower hallucination |
| 5 | Automated Ingestion Sync | P1 | Hands-free knowledge capture |
| 6 | Multimodal Pipeline | P1 | Broader information coverage |
| 7 | Durable Agent Workflow | P1 | Reliable long-horizon tasks |
| 8 | Multi-Agent Collaboration | P2 | Complex task composition |
| 9 | UI / UX Unification | P2 | Product usability |
| 10 | Rust Test Optimization | P1 | Engineering velocity |

Complete P0 items first while running P1 workstreams in parallel; expand P2 once Brain Engine accuracy and stability are locked in.

---

## 16. Mandatory Product-Level Verification Suite

| Scenario | Expected Outcome |
|---|---|
| Ingest 1,000 documents | Indexed cleanly with 0 duplicates |
| Re-ingest same file 100 times | Unnecessary re-indexing skipped |
| Edit a document | Only changed portions updated |
| Delete a file | Associated knowledge and edges cleaned up |
| Swap LLM model | Existing knowledge fully preserved |
| Swap embedding model | Required vectors cleanly re-indexed |
| Korean query | Relevant memories retrieved |
| English query | Relevant memories retrieved |
| Mixed Korean/English query | Cross-lingual retrieval succeeds |
| Past vs. current fact conflict | Temporal state clearly distinguished |
| Cross-document synthesis query | Multiple sources integrated accurately |
| Unanswerable / ungrounded query | Honest abstention ("I don't know") |
| Model execution crash | Workflow state recovers cleanly |
| Cloud disabled | Zero outbound data egress |

### 16.1 Model Replacement Test

1. Ask the Brain a question using Gemma.
2. Ask the identical question using Qwen.
3. Ask the identical question using a Cloud LLM.
4. Keep the underlying Brain data and retrieval parameters identical.
5. Compare retrieved evidence, citations, and answer quality.

This test validates the core product thesis that knowledge belongs to the user's Brain rather than any single model.

---

## 17. Conclusion

Lattice AI already has the architectural foundation to evolve past search and storage into a true Digital Brain platform.

The primary remaining gap is not search algorithms or database plumbing, but turning accumulated records into coherent, time-aware, verifiable understanding.

### Established Foundation

- Rust-centered data ownership
- Hybrid RAG
- Knowledge Graph
- Provenance
- Governed Agent execution engine
- Model-swappable architecture
- Local-First security boundaries

### Next Focus Areas

- Semantic knowledge consolidation
- Time- and experience-aware memory
- Proven live AI answer quality
- Automated knowledge accumulation
- Continuous reasoning and grounding verification

Ultimately, the Brain should answer:

- What decisions did I make in the past?
- Why did I make them?
- What changed afterward?
- Which current facts contradict one another?
- What new conclusions emerge across multiple documents and experiences?
- Can I switch models and retain the exact same knowledge?

**Right now, deepening Brain Engine intelligence matters more than adding more surface features.**

---

## 18. Reference Sources

- [GitHub Repository](https://github.com/TaeSooPark-PTS/LatticeAI)
- [Rust Core](https://github.com/TaeSooPark-PTS/LatticeAI/tree/main/rust/lattice-core)
- [Retrieval Engine](https://github.com/TaeSooPark-PTS/LatticeAI/tree/main/rust/lattice-retrieval)
- [Agent Engine](https://github.com/TaeSooPark-PTS/LatticeAI/tree/main/rust/lattice-agent)
- [Frontend](https://github.com/TaeSooPark-PTS/LatticeAI/tree/main/frontend)
- [Release Notes](https://github.com/TaeSooPark-PTS/LatticeAI/blob/main/RELEASE_NOTES.md)

## 19. Review Scope & Limitations

This review is a static inspection of major Rust, Python, TypeScript, Tauri, test, and CI workflow files.

The following were not performed during this review:

- Line-by-line inspection of every file in the repository
- Full local macOS builds and test execution
- Empirical memory and search latency profiling
- Long-horizon live RAG evaluation against Gemma/Qwen models
- End-to-end installer verification across all operating systems

Accordingly, the items above represent structural risks and improvement directions identified from source inspection; actual defect severity and prioritization should be confirmed through local reproduction tests and runtime benchmarks.
