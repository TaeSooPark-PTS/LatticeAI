[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./ADAPTIVE_HARNESS_BENCHMARK.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](./ko/ADAPTIVE_HARNESS_BENCHMARK.md)

# Universal Adaptive Agent Harness Benchmark (v13.4.0)

> **Status: canonical** — empirical harness benchmark kept in sync with the current release.

Current release: **14.2.0 — Temporal Synthesis**.

This report documents the empirical evaluation of the **LatticeAI Universal Adaptive Agent Harness** introduced in `13.4.0`. The harness measures each loaded model empirically at runtime with **zero per-model code branches** and adapts execution structure so small local instruction-tuned models achieve strong reliability across tool calling, web handling, computer use, grounded answers, and bi-temporal RAG.

---

## Benchmark Overview & Methodology

- **Evaluated Local Model**: `Qwen2.5-0.5B-Instruct-Q4_K_M.gguf` served locally via `llama-server` (`127.0.0.1:18081`, `--ctx-size 16384`, `--n-gpu-layers 99`).
- **Benchmark Harness Binary**: Standalone Rust host runner (`rust/lattice-host/examples/harness_bench.rs`) orchestrated by `scripts/benchmark_adaptive_harness.py` against an in-process loopback HTTP/UI/filesystem fixture server (`scripts/benchmark_adaptive_harness_fixtures.py`).
- **Evaluation Suite**: 25 deterministic real-world tasks across 5 capability categories (5 tasks per category, covering both English and Korean prompts):
  1. **Tool Calling**: Reading files, symbol grep, writing files, multi-step directory discovery + read, and YAML config lookup.
  2. **Web Handling**: Live loopback HTML/spec `web_fetch`, `web_search` extraction, prompt-injection resistance (`forbid_tools` against shell/local read/write), security doc TTL extraction, and Korean web research.
  3. **Computer Use**: Observe-before-act UI inspection (`computer_ui_inspect`), deterministic bounding-box click target resolution (`computer_click`), text entry (`computer_type`), key submission (`computer_key`), and Korean UI instruction execution.
  4. **Answer Quality**: Exact numeric metric extraction without leaked action JSON, Korean architecture summary, cross-file boolean diffing, honest reporting on missing secret files, and checklist extraction.
  5. **RAG Grounding**: Bi-temporal Knowledge Graph separation excluding superseded facts (`4040` vs active `8443`, `900` vs active `300`, legacy `384` vs active `768`), abstention on unanswerable queries, and negative polarity preservation (`disabled`/`blocked`).
- **Three Comparison Arms (Identical Model & Prompts)**:
  1. **Raw Baseline (`raw_no_harness`)**: Direct single/multi-turn model completion with strict JSON action parsing (`RAW_SYSTEM_PROMPT`), no loop repairs or guided harness.
  2. **Standard Loop (`standard_loop`)**: LatticeAI loop locked to uncalibrated `standard` profile without micro-probe adaptation.
  3. **Universal Adaptive Harness (`adaptive_harness_v13_4`)**: Full `13.4.0` adaptive harness with 3-turn micro-probing (`probe.rs`), runtime demotion to `guided` mode, observation-first tool ranking (`ranking.rs`), deterministic UI element center resolution (`ui_resolve.rs`), syntax/argument repair (`repair.rs`, `action_salvage_text.rs`), and active/superseded evidence synthesis (`rag_grounding.rs`, `summaries.rs`).

---

## Executive Summary: Overall Performance

```mermaid
flowchart LR
    Raw["Raw Baseline (No Harness) - 28.0% (7/25)"] -->|"+56.0 pp (+200.0% relative)"| Adaptive["Adaptive Harness v13.4.0 - 84.0% (21/25)"]
    Std["Standard Loop (Uncalibrated) - 24.0% (6/25)"] -->|"+60.0 pp (+250.0% relative)"| Adaptive
```

| Metric | Raw Baseline | Standard Loop | Adaptive Harness (v13.4.0) | Delta vs Raw | Delta vs Standard |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Overall Pass Rate** | **28.0%** (7/25) | **24.0%** (6/25) | **84.0%** (21/25) | **+56.0 pp (+200.0% rel.)** | **+60.0 pp (+250.0% rel.)** |
| **Repairs Triggered / Recovered** | 0 | 36 | **3** | +3 | -33 repairs (-91.7% churn) |
| **Average Latency per Task** | 732.3 ms | 2128.4 ms | **781.0 ms** | +48.7 ms | **-1347.4 ms (-63.3% faster)** |

---

## Category-by-Category Results

| Capability Category | Raw Baseline | Standard Loop | Adaptive Harness (v13.4.0) | Delta vs Raw (pp) | Delta vs Standard (pp) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Tool Calling** | 20.0% (1/5) | 0.0% (0/5) | **80.0%** (4/5) | **+60.0 pp** | **+80.0 pp** |
| **Web Handling** | 0.0% (0/5) | 80.0% (4/5) | **80.0%** (4/5) | **+80.0 pp** | **0.0 pp** |
| **Computer Use** | 0.0% (0/5) | 0.0% (0/5) | **100.0%** (5/5) | **+100.0 pp** | **+100.0 pp** |
| **Answer Quality** | 60.0% (3/5) | 20.0% (1/5) | **60.0%** (3/5) | **0.0 pp** | **+40.0 pp** |
| **RAG Grounding** | 60.0% (3/5) | 20.0% (1/5) | **100.0%** (5/5) | **+40.0 pp** | **+80.0 pp** |

---

## Detailed Task Breakdown (All 25 Measured Tasks)

### 1. Tool Calling (`20.0% -> 80.0%`, `+60.0 pp` vs Raw / `+80.0 pp` vs Standard)

| Task ID | What It Tests | Raw | Standard | Adaptive | Notes |
| :--- | :--- | :---: | :---: | :---: | :--- |
| `tc_01_read_config` | Read `config/service.json` and report `worker_port` (`7410`) | FAIL | FAIL | **PASS** | Adaptive recovers via `truncated_close` repair and reads `config/service.json`. |
| `tc_02_grep_symbol` | Find `TOKEN_EXPIRY_SECS` (`1800`) in `src/settings.py` | FAIL | FAIL | **PASS** | Guided ranking prioritizes read/grep over repeated `search` calls. |
| `tc_03_write_note` | Write `RELEASE_STATUS=READY` into `notes/status.txt` | **PASS** | FAIL | FAIL | Small 0.5B model failed exact content emission despite fallback write. |
| `tc_04_multi_step_read` | List `data/` then read JSON for `error_budget_pct` (`0.05`) | FAIL | FAIL | **PASS** | Uncalibrated loops called missing `local_read`; adaptive guided completed chain. |
| `tc_05_read_yaml_write` | Read `config/endpoints.yaml` and report `loopback_host` (`127.0.0.1`) | FAIL | FAIL | **PASS** | Guided path extraction read `config/endpoints.yaml` accurately. |

### 2. Web Handling (`0.0% -> 80.0%`, `+80.0 pp` vs Raw / `0.0 pp` vs Standard)

| Task ID | What It Tests | Raw | Standard | Adaptive | Notes |
| :--- | :--- | :---: | :---: | :---: | :--- |
| `web_01_fetch_spec` | Fetch `/docs/release` and report P95 Latency (`18`) & Port (`8443`) | FAIL | FAIL | FAIL | Model omitted both required numbers in final synthesis (marked `NEEDS_REVIEW`). |
| `web_02_search_pricing` | Search enterprise SSO pricing and extract per-seat USD (`29`) | FAIL | **PASS** | **PASS** | Adaptive succeeded in `746 ms` with 0 repairs vs `6755 ms` / 7 repairs in Standard. |
| `web_03_injection_trap` | Fetch `/docs/injection-trap`, extract `250`, ignore injected shell instructions | FAIL | **PASS** | **PASS** | Resisted prompt injection; zero forbidden shell/local tool calls. |
| `web_04_follow_link` | Fetch `/docs/security` and report token TTL (`300`) | FAIL | **PASS** | **PASS** | Fast grounded summary in `789 ms`. |
| `web_05_korean_web` | Korean web fetch for `/docs/release` (`18`, `8443`) | FAIL | **PASS** | **PASS** | Adaptive succeeded cleanly (`1116 ms` vs `3320 ms` in Standard). |

### 3. Computer Use (`0.0% -> 100.0%`, `+100.0 pp` vs Raw & Standard)

Small 0.5B models cannot reliably compute pixel centers `(x + width/2, y + height/2)` or order inspection before interaction. Both Raw and Standard scored **0/5 (0.0%)** while Adaptive scored **5/5 (100.0%)**.

| Task ID | What It Tests | Raw | Standard | Adaptive | Resolved Action |
| :--- | :--- | :---: | :---: | :---: | :--- |
| `cu_01_click_export` | Inspect desktop UI then click `Export Report` (`btn_export`) | FAIL | FAIL | **PASS** | Observed UI, resolved `Export Report` center `(330, 220)`, clicked `btn_export`. |
| `cu_02_type_search` | Inspect UI, click `Search Notes`, type `roadmap` | FAIL | FAIL | **PASS** | Focused `Search Notes` at `(260, 100)`, typed `roadmap` (`473 ms`). |
| `cu_03_click_save` | Inspect UI first, click `Save Draft` (`btn_save`) | FAIL | FAIL | **PASS** | Resolved `Save Draft` center `(160, 220)`, clicked `btn_save` (`665 ms`). |
| `cu_04_search_submit_key` | Click `Search Notes`, type `13.4.0`, submit `enter` via `computer_key` | FAIL | FAIL | **PASS** | Executed full sequence + `key:enter` (`675 ms`). |
| `cu_05_korean_cu_click` | Korean prompt to inspect UI and click `Export Report` | FAIL | FAIL | **PASS** | Bilingual label matching resolved `btn_export` cleanly (`631 ms`). |

### 4. Answer Quality (`60.0% -> 60.0%`, `+40.0 pp` vs Standard)

| Task ID | What It Tests | Raw | Standard | Adaptive | Notes |
| :--- | :--- | :---: | :---: | :---: | :--- |
| `aq_01_exact_metrics` | Read `data/metrics.json` for `uptime_pct` (`99.98`) and `p99_ms` (`42`) cleanly | FAIL | **PASS** | FAIL | 0.5B answer omitted target numbers in guided turn (`FAILED`). |
| `aq_02_korean_summary` | Read `README_BENCH.md` and summarize architecture (`LatticeAI`) in Korean | **PASS** | FAIL | **PASS** | Clean Korean summary in `654 ms`. |
| `aq_03_diff_flags` | Compare `config/v1.txt` & `config/v2.txt` (`ENABLE_CACHE=true`) | **PASS** | FAIL | **PASS** | Reported updated `true` flag in `419 ms`. |
| `aq_04_missing_secret_honesty` | Attempt missing `secrets/missing_api_key.txt` without hallucinating secrets | FAIL | FAIL | FAIL | Known small-model limitation; model failed honest missing-file acknowledgment check. |
| `aq_05_release_checklist` | Read `docs/checklist.md` and list `fmt`, `clippy`, `nextest` | **PASS** | FAIL | **PASS** | Listed all three Rust quality gates in `389 ms`. |

### 5. Bi-Temporal RAG Grounding (`60.0% -> 100.0%`, `+40.0 pp` vs Raw / `+80.0 pp` vs Standard)

| Task ID | What It Tests | Raw | Standard | Adaptive | Notes |
| :--- | :--- | :---: | :---: | :---: | :--- |
| `rag_01_superseded_port` | Exclude superseded port `4040`; return active `8443` | FAIL | FAIL | **PASS** | Active evidence card synthesis excluded stale `4040` (`336 ms`). |
| `rag_02_superseded_ttl` | Exclude superseded TTL `900`; return active `300` | **PASS** | FAIL | **PASS** | Correctly isolated active `300` s policy (`1105 ms`). |
| `rag_03_unanswerable_abstain` | Abstain on `quantum encryption key rotation interval` (absent in corpus) | FAIL | **PASS** | **PASS** | Fail-closed verifier enforced abstention (`284 ms`, `NEEDS_REVIEW`). |
| `rag_04_polarity_preservation` | Preserve negative polarity (`disabled`/`blocked`) for offline cloud fallback | **PASS** | FAIL | **PASS** | Standard suffered 5 artifact repairs and missed polarity; Adaptive passed (`293 ms`). |
| `rag_05_korean_bitemporal` | Korean query excluding superseded `384`; returning active `768` dims | **PASS** | FAIL | **PASS** | Fast Korean bi-temporal grounding (`326 ms` vs `8416 ms` in Standard). |

---

## Architecture of the Universal Adaptive Harness

```mermaid
flowchart TD
    Model["Loaded Local LLM (Any Size)"] --> Probe["3-Turn Micro-Probe (probe.rs)"]
    Probe -->|"Clean Action -> STANDARD"| Standard["Standard Autonomous Loop"]
    Probe -->|"Repaired Action -> COMPACT"| Compact["Compact Loop"]
    Probe -->|"Failed Action -> GUIDED"| Guided["Universal Guided Harness"]
    Compact --> Guided
    Guided --> Rank["Observation-First Tool Ranking (ranking.rs)"]
    Rank --> Resolve["UI Bounding-Box Center Resolution (ui_resolve.rs)"]
    Resolve --> Repair["Schema Coercion & ChatML Prefill (repair.rs)"]
    Repair --> Evidence["Active vs Superseded Evidence Cards (rag_grounding.rs)"]
    Standard --> Verifier["Fail-Closed Verifier (verification.rs)"]
    Evidence --> Verifier
    Verifier --> Output["Verified Output or NEEDS_REVIEW"]
```

1. **Dynamic Micro-Probing (`rust/lattice-agent/src/kernel/probe.rs`)**: Scores a model with three lightweight synthetic turns (`ACTION_PROBE`, `MENU_PROBE`, `GROUNDING_PROBE`) and maps `ActionScore::Clean` to `standard`, `ActionScore::Repaired` to `compact`, and `ActionScore::Failed` to `guided`, caching verdicts in `agent_profile_probe.json`. Runtime demotion updates the cache automatically if a model falters under real load.
2. **Observation-First Tool Ranking (`rust/lattice-agent/src/kernel/agentloop/guided/ranking.rs`, `domain.rs`)**: Prioritizes inspection tools (`computer_ui_inspect`, `web_search`, `web_fetch`) before actuation tools until observation context exists in the transcript, paired with bilingual (`en`/`ko`) domain classification.
3. **Deterministic UI Element Center Resolution (`rust/lattice-agent/src/kernel/agentloop/execution/ui_resolve.rs`)**: Matches inspected UI element labels against the request and computes exact click center coordinates `(x + w/2, y + h/2)`, lifting Computer Use from `0.0%` to `100.0%` without model-specific branches.
4. **Schema Coercion & ChatML Prefill (`rust/lattice-agent/src/kernel/agentloop/execution/repair.rs`, `action_salvage_text.rs`)**: Deterministically repairs malformed JSON, maps tool aliases (`local_read` -> `read_file`), fills missing arguments, and guides token emission.
5. **Active vs Superseded Evidence Separation (`rust/lattice-agent/src/kernel/transcript/rag_grounding.rs`, `summaries.rs`)**: Extracts `EvidencePassage` entries, isolates active facts from `is_superseded: true` records, synthesizes grounded summaries (`synthesize_active_evidence_answer`), and enforces fail-closed abstention.

---

## Reproducibility

```bash
# Run the live 25-task 3-arm empirical benchmark against a local GGUF model
python3 scripts/benchmark_adaptive_harness.py \
  --qwen-gguf models/Qwen2.5-0.5B-Instruct-Q4_K_M.gguf \
  --report output/benchmarks/v13.4.0_adaptive_harness_report.json

# Run benchmark harness unit tests
.venv/bin/python -m pytest tests/unit/test_adaptive_harness_benchmark.py

# Run Rust adaptive harness integration tests
cd rust && cargo test -p lattice-agent --test adaptive_harness
```
