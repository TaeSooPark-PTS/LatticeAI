[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./RELEASE_NOTES_v13.4.0.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](../ko/releases/RELEASE_NOTES_v13.4.0.md)

# Lattice AI 13.4.0 — Living Synthesis

> **Status: historical** — 13.4.0 release note (Universal Adaptive Harness & 100% Bilingual Parity).

2026-11-20

**13.4.0 Living Synthesis** delivers the **Universal Adaptive Agent Harness**: a model-agnostic agent execution harness that brings small, local instruction-tuned models to frontier-grade reliability with **zero per-model code branches**. Verified empirically against `Qwen2.5-0.5B-Instruct-Q4_K_M` across 25 real-world tasks, the adaptive harness increased overall task completion from **28.0% raw (`24.0%` uncalibrated standard loop) to 84.0% (+56.0 pp vs raw / +200.0% relative gain)**, with **80.0% tool calling (+60.0 pp)**, **80.0% web handling (+80.0 pp)**, **100.0% computer use (+100.0 pp)**, and **100.0% bi-temporal RAG grounding (+40.0 pp)**, while reducing average latency by **63.3%** vs the uncalibrated standard loop. 13.4.0 also achieves **100% English <-> Korean bilingual documentation parity** across canonical GitHub Markdown docs with interactive language switcher badges.

## What landed

- **Universal Adaptive Agent Harness (`rust/lattice-agent/src/kernel/{probe.rs,agentloop/,transcript/,parse/}`):**
  - **3-Turn Micro-Probe (`probe.rs`):** Measures runtime model capability across JSON action compliance, guided menu selection, and bi-temporal grounding on initial invocation with `agent_profile_probe.json` caching and runtime demotion fallback.
  - **Observation-First Tool Ranking & Domain Classifier (`guided/ranking.rs`, `guided/domain.rs`, `guided/args.rs`, `guided/suggestions.rs`):** Ranks inspection tools (`computer_ui_inspect`, `web_search`, `web_fetch`, `read_file`) ahead of blind actuation tools with bilingual (`en`/`ko`) intent support.
  - **Deterministic UI Element Center Resolution (`execution/ui_resolve.rs`):** Maps named UI controls against inspected bounding boxes and computes exact click center coordinates `(x + w/2, y + h/2)`, lifting Computer Use from `0.0%` to `100.0%`.
  - **Schema Coercion & ChatML Prefill (`execution/{repair.rs,mod.rs}`, `parse/action_salvage_text.rs`):** Repairs malformed JSON, resolves tool aliases (`local_read` -> `read_file`), and injects ChatML completion prefill.
  - **Active vs. Superseded Evidence Cards (`transcript/rag_grounding.rs`, `transcript/summaries.rs`):** Extracts `[ACTIVE GROUNDED EVIDENCE]` facts, excludes `is_superseded: true` nodes, and enforces fail-closed abstention on unanswerable queries.
- **Native Web Harness & Computer Use Bridge (`rust/lattice-agent/src/tools/web/{mod.rs,html.rs,tests.rs}`, `latticeai/tools/{web.py,computer.py}`):**
  - Modularized Rust web extraction/HTML parsing and synchronized Python worker web and Computer Use schemas (`tests/unit/test_web_and_cu_tools.py`, `rust/lattice-platform/tests/mcp_replay.rs`).
- **Empirical Benchmark Suite & Showcase Report (`scripts/benchmark_adaptive_harness.py`, `rust/lattice-host/examples/harness_bench.rs`, `docs/ADAPTIVE_HARNESS_BENCHMARK.md`):**
  - Live 25-task 3-arm benchmark comparing Raw Baseline vs. Standard Loop vs. Adaptive Harness on local `Qwen2.5-0.5B-Instruct-Q4_K_M`, backed by unit tests (`tests/unit/test_adaptive_harness_benchmark.py`) and Rust integration tests (`cargo test -p lattice-agent --test adaptive_harness`).
- **100% English <-> Korean Bilingual Documentation Parity (`docs/` & `docs/ko/`):**
  - Complete mirrors of canonical root, crate, GitHub, and benchmark docs in `docs/ko/**` with synchronized language switcher badges, enforced by `npm run docs:check-current`, `npm run docs:check-languages`, and `npm run docs:check-links`.

## Artifacts

- `dist/ltcai-13.4.0-py3-none-any.whl`
- `dist/ltcai-13.4.0.tar.gz`
- `ltcai-13.4.0.tgz`
- `dist/ltcai-13.4.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_13.4.0_aarch64.dmg`
