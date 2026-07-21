# Lattice AI v9.9.2 — Artifact Trust

**Release date:** 2026-07-21

9.9.2 turns the biggest recommendation of the 9.9.1 full-stack review
(`docs/reviews/FULL_STACK_UX_HARNESS_KG_FILEGEN_REVIEW_2026-07-21.md`) into shipped behavior: **every file
write, from every entry path, now passes the same validation pipeline**, and
the chat surface tells the truth about what was produced. A file-creation
request ends with a real, structurally valid file — whether it came through
the direct chat path, the agent JSON loop, or a weak local model that wrapped
its output in fences and pleasantries.

## ArtifactWritePipeline — one write path, one guarantee (review L1/§8)

- `sanitize_write_content` (in `latticeai/core/file_generation.py`) is the
  single write-side gate: content that already validates is saved
  byte-for-byte; fenced/chatty/truncated model output is extracted and
  re-validated; unusable output falls back to the deterministic repair
  scaffold. Empty writes (e.g. `__init__.py`) are always left untouched.
- The agent executor now routes every `write_file` `args.content` through this
  pipeline before dispatch. The transcript records a `content_sanitize` verdict
  and the loop trace tags `artifact_sanitize` / `artifact_repair`, so weak-model
  robustness is measurable, not assumed.
- HTML validation is stricter and honest: a document wrapped in prose or
  Markdown fences no longer counts as valid, which is exactly what lets the
  extraction pass rescue it.

## Artifact-first chat contract (review §3/§8.4)

- Direct file creation responses now include an `artifacts[]` array
  (`kind/path/filename/bytes/previewable/valid/repaired`) alongside
  `created_files`, and agent runs expose the same via
  `collect_artifacts(transcript)`.
- **Never silently overwrite:** the direct path auto-suffixes an existing
  target (`generated_page.html` → `generated_page_2.html`) and says so in the
  reply. Overwrites stay in the reviewable-proposal flow.
- **What Lattice makes, Lattice remembers:** generated files are optionally
  indexed into the Brain through the unified `IngestionPipeline`
  (`workspace://` provenance, `origin: generated_file`). Enabled by default;
  `LATTICEAI_INGEST_GENERATED=0` turns it off. Ingestion problems never fail
  the file creation.

## Honest UI: repaired badges and unmistakable non-success (review §4)

- File cards show an **"Auto-repaired — please double-check"** badge when the
  deterministic scaffold produced the file, so a fallback is never oversold as
  clean model output (ko/en localized).
- `NEEDS_REVIEW` and `FAILED` terminal agent states render as a distinct warm
  warning strip on the message (`role="alert"`), including when no file was
  produced — they can no longer be visually confused with success. Dark/light
  themed via the standard `data-theme` convention.

## Loop quality (review §6 L3/L5)

- **Plan schema enforcement:** `normalize_plan` guarantees a non-empty goal,
  filters junk steps, clamps `estimated_steps`, and synthesizes a
  deterministic single `write_file` step when a weak planner returns an empty
  plan for an obvious file-creation request. Every fix is recorded in the loop
  trace.
- **Memory quality filter:** `filter_learnings` drops trivial
  ("파일을 만들었습니다"-class) and duplicate learnings before they enter the
  Brain, keeping recall signal-dense.

## FG harness — the scenario matrix is now a permanent gate (review §5 H1)

- `tests/unit/test_artifact_write_scenarios.py` pins FG-01..FG-08: explicit
  filename targets, type-keyword inference, dirty weak-model output, truncated
  HTML repair, JSON slicing, agent-path sanitization (clean content untouched),
  how-to questions never routing to file tools, and multi-file scaffold
  validity.
- `product_readiness` `action-aware-chat` gate now proves the
  ArtifactWritePipeline evidence on disk.

## Verification

- 1301 unit tests green (17 new FG-harness/loop-quality tests), 47 frontend
  tests green, agent loop eval 20/20 @ 1.0, brain quality eval green, ruff,
  tsc, frontend lint, i18n parity, bundle budget, OpenAPI drift,
  current-release + doc-status gates pass.

## Honest limitations

- The multi-file **Artifact Loop** (manifest-driven project generation) and
  interactive `WAITING_APPROVAL` resume UI from the review remain future work
  (review Waves 3–4); `create_web_project` covers the scaffold case today.
- `sanitize_write_content` applies to the agent loop and direct chat path;
  the user-driven `/tools/write_file` API intentionally trusts explicit user
  content and is unchanged.
- Auto-indexing of generated files follows the ingestion pipeline's quality
  gates; it records an honest `brain_ingest.status` instead of claiming
  success unconditionally.
