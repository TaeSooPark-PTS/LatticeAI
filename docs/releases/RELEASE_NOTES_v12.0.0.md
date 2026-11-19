[![English](https://img.shields.io/badge/English-Active-2563eb?style=flat-square)](./RELEASE_NOTES_v12.0.0.md) [![한국어](https://img.shields.io/badge/한국어-Korean-475569?style=flat-square)](../ko/releases/RELEASE_NOTES_v12.0.0.md)

# Lattice AI v12.0.0 — Open House (2026-08-18)

> **Status: historical** — point-in-time release note.

11.9.0 made the door that had previously existed only in documentation actually open. 12.0.0 tidies up that house so we can invite others in: it splits the two largest crates into named domains (pure moves, zero behavioral changes), rewrites the onboarding documentation for newcomers, and closes four honest gaps left open in 11.9.0. On top of that, it improves Graph RAG quality and speed and adds a **model-agnostic** small-model harness. The door stays the same — `lattice-host` serves **422 operations / 41 families** (adding `POST /mcp` and the folder-prune route), and Python stays a **20-route AI worker** (adding `POST /worker/vector/query`).

| | |
|---|---|
| Theme | **Open House** — organizing the house, naming the rooms, and welcoming contributors |
| Complexity control | `lattice-agent` 43 files → 6 groups, `lattice-platform` 31 flat modules → 7 domains. All `git mv`, zero behavior change |
| Documentation | 2 crate-local `ARCHITECTURE.md` maps · rewritten `docs/DEVELOPMENT.md` onboarding · new `docs/ROADMAP.md` |
| Honest gaps closed | Restore (store epoch) · install execution (per-item consent) · `POST /mcp` in OpenAPI contract · pointer extra declared — 4 closed |
| Graph RAG | Two-stage Korean particle stripping + grounding gate, section tree, directed typed edges, embedding auto-detection |
| Speed | Unchanged re-index 33s → **0.26s** · initial indexing 25.8s → 7.2s · drain ~66 → ~1,300 items/s · 991-item backlog 40m → 15.3s |
| Small models | `guided` mode removes the JSON requirement altogether. Qwen 0.5B reaches DONE in guided mode (3.9s, real file) |
| Gates | cargo 90 suites (2,159 passed) · pytest 1,287 passed (coverage ≥90%) · vitest 108 files (1,846 passed) · model matrix 18/18 met |

## One-Line Summary

The crates are organized by domain with written maps, four gaps documented in 11.9.0 are closed, unchanged re-indexing dropped from 33s to 0.26s, and even a 0.5B model can complete agent tasks. Everything left unclosed is recorded verbatim in the Honest Disclosures below.

## 1 · House Cleaning — Domain Restructuring of Two Crates & Onboarding Docs

The first requirement for this release was not a feature but **complexity control**. We organized the two largest and most frequently read crates by *what each file is for*. Every move was a `git mv` with zero behavioral change — proven by the golden and contract test suites.

**`lattice-agent` — six groups, one rule: the kernel decides, the surface carries.** 43 files moved into `kernel/` (the agent loop and every decision that can reject), `parse/` (untrusted model text → typed values), `content/` (bytes a run is about to write), `tools/` (what tools do and the ground they may step on), `surface/` (inbound/outbound HTTP and the client calling the Python worker), and `prompts/` (defaults when the caller does not provide one). Dependency arrows only point downward: `kernel` never imports `surface`, and `parse`, `content`, and `tools` never import `kernel`. If an upward arrow seems necessary, the argument list is wrong, not the layering.

**`lattice-platform` — seven domains, one rule: this crate is the product surface.** 31 flat modules moved into `workspaceos/` (where people work and who is in it), `toolsurface/` (every capability the product can reach and its catalog), `governance/` (what the product does autonomously and the gates in front of it), `adminops/` (operating an installation), `knowledge/` (how graph contents cross trust boundaries), `modelops/` (which models run here), and `shell/` (first browser contact) — 100 `git mv` operations in total. This crate *proposes*; it does not decide what is allowed (that belongs to `lattice-agent`) and does not own what is true (that belongs to `lattice-core`).

**The maps live in documentation.** Both crates ship a crate-local `ARCHITECTURE.md` — [`rust/lattice-agent/ARCHITECTURE.md`](../../rust/lattice-agent/ARCHITECTURE.md) and [`rust/lattice-platform/ARCHITECTURE.md`](../../rust/lattice-platform/ARCHITECTURE.md). Each group's `mod.rs` documents what belongs inside, what must never enter, and which invariants hold — `ARCHITECTURE.md` is the index, and `mod.rs` is the contract. Both `src/lib.rs` files end with a compatibility map so every pre-restructure `lattice_agent::...` and `lattice_platform::...` path still resolves verbatim. That map also serves as an honest inventory of external coupling so nothing can be deleted without updating callers together.

**Contributor documentation was rewritten.** [`docs/DEVELOPMENT.md`](../DEVELOPMENT.md) is now a contributor onboarding guide with a 10-minute quickstart, a **where to put what** table, and a guide to why each CI gate fails. The new [`docs/ROADMAP.md`](../ROADMAP.md) tracks remaining gaps with priority (`P1`/`P2`/`P3`), effort, and suggested paths — items closed in this release are marked closed so they are not re-opened.

## 2 · Graph RAG — Quality and Speed

**Korean-aware retrieval.** Korean postposition stripping now runs in two stages (covering compound particles) so suffixed forms of the same noun reach the same candidates. A grounding gate ensures lexical matching alone cannot justify an answer, and containment deduplication prevents a long chunk and an inner short chunk from being double-counted.

**Directed typed edges.** Extraction now actively **produces** `PART_OF` and `CONTRADICTS` edges (previously only defined in the vocabulary). Evidence is classified and stored on the relation itself, recording whether a relation came from a verb phrase or co-occurrence.

**Documents have a table of contents.** The typed chunker already tracked each chunk's `heading_path` (e.g., `Architecture > Storage`), but before 12.0.0 that string stopped at chunk metadata. Now that outline becomes a tree in the graph: `Document ← part_of ─ Section ← part_of ─ Section`, and `Section ─ has_chunk → Chunk`. You can answer both "which section did this fact come from?" and "what else is in that section?" without expanding the schema — `Section` nodes and `PART_OF`/`HAS_CHUNK` edges already existed in the taxonomy and simply had no writer. Documents without headings do not emit synthetic "untitled" sections. In measured runs, **549 of 555 triples** carry section provenance.

**Embedding auto-detection.** When a real downloaded embedding model is present, it can be adopted automatically; when absent, the hash fallback is **honestly labeled as fallback**. Vector identity `(model, dim)` filtering is verified so two embedding spaces never silently mix.

Schema documentation in [`docs/kg-schema.md`](../kg-schema.md) specifies `Section`, `HAS_CHUNK`, `PART_OF`, and `CONTRADICTS`.

**Speed gains came from eliminating wasted work:**

- **Unchanged re-index: 33s → 0.26s.** Per-file fingerprints (path + size + mtime + content SHA-256) are stored in `ingestion_provenance.metadata_json` without creating a second table. SHA-256 is computed only when size or mtime changes. The waste ratio (re-reading, re-chunking, and re-embedding unchanged files) dropped from `1.00` to `0.00`.
- **Initial indexing: 25.8s → 7.2s.**
- **Drain throughput: ~66 → ~1,300 items/s.** Embedding moved *outside* the write transaction so the store no longer holds the write lock while waiting on the model.
- **991-item backlog: 40m → 15.3s.** An adaptive scheduler polls more aggressively when work is queued and backs off when idle.
- **Incremental HNSW append + live search usage.** With `LATTICEAI_VECTOR_INDEX=hnsw`, the worker sidecar supplies candidates and Rust re-scores them with the **exact same cosine metric** as brute force — approximate recall, exact ordering. Results report index name `hnsw+rescore` and fall back to brute force with an explicit reason on failure. **The default remains `brute`** (exact and byte-compatible with prior releases).
- **`vault-watch` skips unchanged diffs.** Unmodified notes are no longer re-ingested.

**Deleted file pruning.** Incremental indexing previously only *counted* missing files and left nodes intact — deleting nodes is a product decision, not a side effect of indexing. There is now an explicit door: `POST /api/ingestion/folder/prune` performs a dry-run without `confirm`, and only removes nodes via `delete_document_tree` (bidirectional edges) when confirmed. The UI adds a "Prune deleted files (N)" card action. `delete_node` is never used here (avoiding the 11.7 trap that left `PART_OF` edges behind), verified with 0 dangling edges after pruning.

## 3 · A Harness for Every Small Model

This product should run on whatever local model fits the machine, and a 0.5B model is not merely a small 70B — it often **cannot satisfy a JSON tool-calling contract at all**. The solution has four parts, none of which hardcode model names. Per-model hacks remain forbidden.

**`guided` mode — removes the JSON requirement.** Alongside `standard` and `compact`, a new `guided` dial decomposes each step into micro-turns: choose one number from the run's live **numbered catalog**, then provide one turn per required argument (`path` as a single line, `content` as free text — no JSON escaping needed). **The harness assembles the action struct** and hands it to `perform_action` — the exact same tail used by every mode, preserving the gate chain, loop guard, pre-write snapshot, and `sanitize_write_content`. Verification works the same way: a closed `PASS`/`FAIL` question plus a one-line reason evaluated by the same grounding and coverage gates as JSON verdicts. There is no governance shortcut — the only thing skipped is JSON syntax.

**Measured probing — measured rather than guessed.** On load, two fixed probe prompts are sent to the model (one containing newlines and quotes inside an action object — the exact characters that break weak models inside JSON strings) and scored by **the loop's own parser** to choose the dial: clean parse → `standard`, repaired parse → `compact`, unparseable → `guided`. The verdict is cached by model ID and **crate version**. `LATTICEAI_AGENT_PROFILE` still overrides everything, and `LATTICEAI_AGENT_PROBE=0` disables probing. Size regexes remain only as a fallback prior when probing is unavailable.

**Mid-run self-demotion — downward only.** Probing tests a small synthetic prompt, while real tasks are harder. If a run **measured** as `standard` or `compact` exhausts its format budget without producing valid progress (e.g., copying worked examples or producing no parseable action), it demotes itself to `guided` and finishes there. Three guards apply: the dial must have been measured, it must not already be `guided`, and no execution evidence has been produced yet. It never promotes upward.

**Unified catalog — three surfaces in one menu.** Native tools, `mcp.<tool>`, and `skill.<name>` share a single numbered menu, one set of argument signatures, and one dispatch decision. Names already governed by the run follow the **native path** (the stricter chain), while names without run-level policy reach the host catalog — and MCP dispatch passes through the **same** governance check as `POST /mcp`. Governance rejections, unknown tools, and missing tool surfaces return tool errors rather than bypasses.

**Seam-level structural prefill.** `Completion::prefix` forces a completion to *begin* with a given prefix — `compact` sends `{"thoughts": "`. Preambles, Markdown fences, and `<|channel|>` frames are prevented at generation time instead of relying purely on downstream repairs. The worker prefills the prefix after the generation marker and guarantees the returned text begins with it. Token-level JSON grammar constraints were **intentionally omitted**: they require a tokenizer-aware incremental parser and per-token Python callbacks over a single MLX runner, whereas `guided` gives the same guarantee at zero per-token overhead by not requiring JSON at all.

**Proof point.** Qwen 0.5B reached **DONE** in `guided` mode — 3.9 seconds, producing a real file. This is the first time a model in this size class completed an end-to-end agent run in Lattice AI.

**Model matrix.** Running multiple model sizes on the same benchmark suite is a release gate artifact. Measured pass12 results across 3 models:

| Model | Profile (measured) | Result | Notes |
| --- | --- | --- | --- |
| `qwen05b` (`Qwen/Qwen2.5-0.5B-Instruct-AWQ`) | `guided` | S1 2/3 · S2 2/3 · S3 2/3 · S4 1/3 · S5 1/1 · S6 1/1 (all scenarios MET) | F5 artifact restoration eliminated false negative answers; mechanical file/count tasks completed |
| `gemma2b` (`mlx-community/quantized-gemma-2b-it`) | `guided` | S1 2/3 · S2 2/2 · S3 2/3 · S4 1/3 · S5 1/3 · S6 1/1 (all scenarios MET) | F1 default-arg menu + F4 skill pre-lookup completed the loop; critic doubts hedge to `NEEDS_REVIEW` |
| `gemma_e2b` (`mlx-community/gemma-4-e2b-it-4bit`) | `compact` | S1 2/3 · S2 2/2 · S3 2/3 · S4 1/3 · S5 1/3 · S6 1/1 (all scenarios MET) | F2 count recovery + compact dial completed with fluent Markdown output |

Pass12 verification satisfied **18/18 targets** and passed the **"0 confidently wrong unhedged answers"** rule. What remains is an honest limit of content quality: a 0.5B model may append unrelated sentences after creating a file (14B), and semantic quality of generated content scales with model capacity. `gemma2b` lands safely on `NEEDS_REVIEW` when verifier output is malformed.

## 4 · Four Honest Gaps Closed, Two Remaining

Four items listed in 11.9.0's Honest Disclosures are now closed:

- **No restart required after restore.** The store now tracks a generation epoch. When restore increments the epoch, cached pool connections are immediately detected as stale and discarded so the next read observes the restored bytes without restarting the process.
- **`/setup/install` executes real installs — strictly with per-item explicit consent.** `brew`/`pip`/`uv` commands run only when the named item belongs to the **server-derived allowlist**. Anything outside the allowlist is rejected. **Manual mode remains the default**; execution requires explicit user consent per item.
- **`POST /mcp` is now in the OpenAPI contract.** Declared as a single JSON-RPC envelope operation across both the mount table and the OpenAPI spec. Because it is natively mounted, requests are handled in-process and never proxied to the worker.
- **Pointer tools are explicitly declared.** `pyproject.toml` now declares `pip install "ltcai[pointer]"` as the installation path for the six `pyautogui` pointer tools. Stock installations still honestly report `capabilities.pointer_tools=false`, closing the gap where the capability was previously undeclared.

Two items intentionally left open remain documented as-is: **HNSW is opt-in** with `brute` as the search default, and **watch does not auto-delete files** (pruning happens only via the explicit prune door above).

## 5 · Frontend Hardening

- **`ErrorBoundary` across the app.** Installed per route and around heavy panels so a single throwing panel degrades cleanly into an inline "Retry" fallback instead of blanking the whole viewport.
- **Lazy-split Act / Brain sub-routes.** First paint no longer ships code for unopened screens.
- **Broader preview patterns.** Permission-mode changes preview a diff of what changes beforehand, and risky feature toggles require explicit acknowledgment.
- Bundle stays at **104.3 KiB / 150 KiB budget**.

## 6 · Existing Bugs Fixed

- **RAG citation instructions leaking into agent prompts.** Chat citation rules had leaked into agent turns, causing models to print citations where they should invoke tools.
- **Model-loading name roster gate.** An allowlist check in the loader previously prevented Qwen AWQ models from loading at all.
- **Copyable worked examples.** Weak models tend to continue the nearest complete pattern in the prompt. If `write_file` `content` matches our prompt example constant verbatim, the loop records `COPIED_EXAMPLE` and **writes nothing** (fail-closed). Because the comparison is against crate-owned constants, genuine user answers cannot be falsely rejected.
- **`<|channel>` single-pipe control frame leak.** Blocked a variant missing its closing pipe that previously passed through the parser into response bodies.

## Honest Disclosures

- **Small-model *content quality* still hits fail-closed gates.** Mechanical execution — writing the requested file and reaching DONE — is now reliable under `guided`, while thin summaries or weak explanations can still be rejected by the critic into `FAILED`/`NEEDS_REVIEW`. Those are visible failures rather than silent ones.
- **`api_key` cloud paths remain mock-verified only.** Contract tests exist without live paid calls, consistent with the zero-spend policy; live verification covers `cli_oauth` (`agy`/`grok` at zero billing).
- **The macOS DMG is ad-hoc signed (= unsigned).** Unchanged from prior releases; first launch requires Gatekeeper bypass steps.
- **Vector search defaults to `brute`.** `hnsw` is opt-in (`hnsw+rescore`) and falls back to `brute` with an explicit reason on failure.
- **`watch` does not auto-delete.** Missing files are reported; removal occurs only through explicit prune (`dry-run` → `confirm`).
- **Crate restructuring was pure movement.** Drawing domain boundaries did not eliminate every cross-domain coupling; the four remaining cross-boundary couplings are explicitly listed in `lattice-platform`'s architecture map.

The remaining items left open in 11.9.0 stay documented as-is — 90% line-coverage floor, multimodal image/video HTTP doors partially unexposed, Self-Model `open_keys` limited to `pending` with no UI screen, no extraction `refiner`, ownerless review events silent, KG-API ingest text-only, and review mutations taking 2 store cycles. Telegram bridge and SSO OIDC login/callback remain removed as of 11.6.0. Priorities and suggested paths for remaining gaps live in [`docs/ROADMAP.md`](../ROADMAP.md).

## Gates

Measurements at **release-gate execution time**:

- Cargo tests all passing (90 suites / 2,159 passed, 0 failed, 2 ignored). `cargo clippy --workspace --all-targets -- -D warnings` clean.
- Pytest all passing (1,287 passed, 0 failed, 3 skipped). Enforced line coverage floor: 90%.
- Vitest all passing (108 test files / 1,846 tests passed, 0 failed), frontend coverage 100% across all 4 metrics.
- Crate restructuring verified with **zero behavioral change**: golden suites and contract tests produce identical outputs before and after.
- Bundle budget: **104.3 / 150 KiB**.
- `product-readiness`, lint, and OpenAPI drift gates passing.
- Model matrix: 18/18 targets met on pass12 ("0 confidently wrong unhedged answers" passed).

## Artifacts

- `dist/ltcai-12.0.0-py3-none-any.whl`
- `dist/ltcai-12.0.0.tar.gz`
- `ltcai-12.0.0.tgz`
- `dist/ltcai-12.0.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_12.0.0_aarch64.dmg`

Wildcard uploads (`dist/*`) are not used.

## Support Policy

The public history and security support floor is **11.0.0**. 12.0.0 builds on the same architecture as 11.9.0 — the Rust product server plus Python AI worker. 10.x and 9.x are different programs, and `SECURITY.md` supports 11.x and 12.x only. Earlier notes remain in the tree as `RELEASE_NOTES_v*.md`.
