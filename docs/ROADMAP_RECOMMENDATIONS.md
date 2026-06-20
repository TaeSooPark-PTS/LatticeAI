# Strategic Roadmap Recommendations

This roadmap captures the 2026-06-20 product direction input and maps it to
small release-sized work. The operating principle stays unchanged:

> Models are temporary. Knowledge is durable. The Brain is the product.

## 7.3.0 Applied Slice

7.3.0 implements the first narrow slice of the roadmap:

- Runtime evolution: single-agent and multi-agent execution share
  `agent-run-contract/v1`, making mode/status/timeline evidence inspectable.
- Hybrid search optimization: the Brain quality gate now includes deterministic
  recall and ranking regression thresholds.
- Security and trust: run contracts distinguish runtime type and execution mode
  so simulated output is not presented as real execution.

## 7.4.0 Applied Slice

7.4.0 completes the next roadmap slice without deferring it:

- Runtime convergence: agent runs, workflow runs, audit events, and realtime
  events all expose the `agent-run-contract/v1` family envelope while keeping
  legacy top-level fields for compatibility.
- Trust and operations: persisted run rows refresh their contract through
  queued, running, terminal, cancelled, and interrupted states; audit events are
  contracted only after secret redaction.
- Retrieval quality and scale: the CI quality gate now seeds a real local
  Knowledge Graph corpus and scores SearchService hybrid retrieval with judged
  queries, recall, precision, NDCG, and must-include hit-rate thresholds.

## 7.5.0 Applied Slice

7.5.0 burns down the remaining 7.4.0 risk instead of deferring it:

- Contract consumption: AgentRuntime and realtime feed APIs now emit compact
  `contracts` views so UI, replay, admin, and export consumers can depend on the
  shared family envelope directly.
- Retrieval scale: the corpus fixture now runs against 250+ local records while
  keeping judged queries, graded relevance, and must-include expectations.
- Release trust: stale artifact mixing is handled through clean exact-version
  artifact generation, npm audit findings are cleared, and the Tauri 2 stack is
  updated past the old `block v0.1.6` future-incompatibility warning.

## Near-Term Tracks

1. Retrieval quality and scale
   - Add latency budgets for larger corpora.
   - Track per-channel keyword/vector/graph diagnostics in CI.
   - Tune semantic/graph/keyword fusion by query class.

2. Incremental ingestion
   - Add background indexing jobs.
   - Detect duplicates and merge memory candidates.
   - Surface conflict resolution for contradictory memories.

3. Runtime convergence
   - Migrate remaining UI run/replay/admin components to read the API-level
     compact contract views as their primary source.
   - Keep simulation mode explicit and never record it as product success.
   - Route tools through explicit registry/governance contracts.

4. Brain SDK boundary
   - Continue extracting `lattice_brain` as a reusable Brain Core package.
   - Keep compatibility shims until downstream imports are migrated.
   - Preserve migration rollback paths for graph and memory storage.

5. Trust and operations
   - Extend audit logging around tool execution and retrieval injection.
   - Add dependency vulnerability monitoring.
   - Add Tauri update/rollback planning before enabling auto-update.

## Longer-Term Tracks

- Multi-modal ingestion and retrieval for images, audio, and video.
- Proactive Brain synthesis, contradiction detection, and recommendations.
- Temporal reasoning over historical graph states.
- Interoperability with Obsidian, Notion, Email, Calendar, Git, Slack, and Teams.
- Encrypted Brain Network sharing.
- Plugin marketplace and public benchmarks.
