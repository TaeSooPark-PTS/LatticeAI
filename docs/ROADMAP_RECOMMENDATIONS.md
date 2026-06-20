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

## Near-Term Tracks

1. Retrieval quality and scale
   - Expand fixture datasets for hybrid search.
   - Add latency budgets for large corpora.
   - Track recall, precision, and ranking regressions in CI.
   - Tune semantic/graph/keyword fusion by query class.

2. Incremental ingestion
   - Add background indexing jobs.
   - Detect duplicates and merge memory candidates.
   - Surface conflict resolution for contradictory memories.

3. Runtime convergence
   - Use `agent-run-contract/v1` across single-agent, multi-agent, workflows,
     audit logs, and UI event streams.
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
