# Lattice AI 9.9.6 — Same Brain Everywhere

> **Status: historical** — point-in-time release note.

**Release date:** 2026-07-27

9.9.6 answers the 2026-07-27 full-stack review. Its theme is the review's
sharpest finding: *"표면마다 다른 Lattice"* — the Brain was one thing, but what
you could see and do with it changed depending on whether you were in the web
app, VS Code, or Telegram. This release closes that gap, turns evidence into
action, teaches the loop to remember work that spans several runs, and makes
the graph honest about the difference between a relation and a coincidence.

## Highlights

### 1. Surface parity — VS Code and Telegram catch up (P0)

The web app badged every answer with its grounding verdict, showed a Review
Center, and explained how a run ended. The editor showed none of it.

- **Recall grounding badge** — `Lattice AI: Ask About Current File` and the new
  `Lattice AI: Ask Your Brain` read the same `POST /chat` `grounding` verdict
  the web badge uses. A missing verdict reports `unknown`; the extension never
  promotes an unverified answer to "근거 있음".
- **Review Center** — `Lattice AI: Review Center (Change Proposals)` lists
  staged change proposals from the same `/api/proposals` surface, and
  approves/rejects them in place. A 409 (the file changed since staging) is
  reported as a conflict with nothing written, not retried behind your back.
- **Agent step summary** — `Lattice AI: Run Agent Task` reports the run's
  steps, created files, and plain-language outcome in an output channel.
- **Telegram** now repeats the same plain-language outcome after an answer, so
  a `NEEDS_REVIEW` run can no longer be skimmed as a success.
- Every parity decision is pure data-shaping in `vscode-extension/surface.ts`,
  asserted against real sidecar payloads by `tests/vscode-extension.test.cjs`
  (now part of `npm run lint`).
- [`docs/SURFACE_PARITY.md`](../SURFACE_PARITY.md) records what each surface
  provides — including what is **intentionally** not provided (the browser
  extension is capture-only by design) versus what is still a gap.

### 2. Evidence → action, in one click (P0)

`POST /api/evidence/actions` turns the citations an answer actually used into
ready-to-send, evidence-scoped prompts: **요약 / 체크리스트 / 문서 파일 /
한 페이지**. Composition is deterministic and model-free — the prompt runs
through the normal chat path, so there is exactly one road from a request to an
artifact. Citations that no longer resolve are reported in `missing`; when
nothing resolves, no action is offered and the UI says why.

### 3. Weak models explained in plain language (P0)

Every agent run now returns an `explanation`:

```json
{"code": "no_evidence", "ok": false,
 "headline": {"ko": "...", "en": "..."},
 "details": [{"ko": "모델이 정해진 형식을 3번 벗어났고, 그중 2번은 자동으로 복구했습니다.", "en": "..."}],
 "model_strain": {"level": "heavy", "parse_errors": 3, "repairs": {...}},
 "next_step": {"ko": "더 큰 모델로 다시 시도하면 성공률이 올라갑니다.", "en": "..."}}
```

Deterministic, honest (it never upgrades an outcome), and shown on the web,
in VS Code, and in Telegram. `ok` is true only for a verified `DONE`.

### 4. Citation precision + prose chunking (P1)

- A new **`prose`** chunking strategy ends chunks at sentence and paragraph
  boundaries instead of cutting every N characters. This matters most in
  Korean, where the sentence-final verb carries the claim. Routed for
  `.txt/.pdf/.docx/.html/…`; the `plain` strategy stays byte-identical to the
  legacy walk, so unchanged content keeps identical chunk ids.
- Chunk hits now carry their own provenance into the answer: a **locator**
  (`"Guide > Setup · p.4"`, `p.4–5` when a chunk spans a page break) instead of
  citing only the parent document. Absent when the chunk cannot prove it.

### 5. One context contract for chat and document generation (P1)

Document generation went through its own path with no budget and no quality
signal. It now shares chat's contract: the same `approx_tokens` accounting and
explicit budget, the same `context_quality` signal, and an assembly `trace` in
the same shape. Rendering still differs on purpose; the guarantees no longer do.

### 6. Meaning edges vs adjacency edges (P1)

The graph drifted toward co-occurrence because a verb-less sentence still
produced a `관련됨` edge indistinguishable from a real relation. Now:

- `infer_edge_relation()` classifies each relation as `verb` or `cooccurrence`
  and weights it accordingly (1.0 vs 0.35);
- a verb-less sentence listing more than four concepts produces **no** edges —
  that is a list, not a set of relations;
- `plan_relation_noise_reduction()` lets the curator demote weak and hub
  co-occurrence edges while never touching verb-backed ones. Legacy edges with
  no recorded evidence class are kept and labelled `unknown_evidence`, never
  guessed at.

### 7. Project sessions — the multi-turn project loop (P1)

`/api/projects` keeps what a single run cannot: the files a project produced,
what is still open, and the last honest verification. Passing `project_id` to
`/agent` injects that state into the planner and executor prompt and folds the
run's outcome back in when it finishes. A `NEEDS_REVIEW` run never becomes a
project's "done".

### 8. Three agent loops closed

- **Re-search** — an `ArtifactLedger` records what a run just wrote, and the
  context assembler injects it as a high-priority section, so "그 파일에
  다크모드 넣어줘" works before asynchronous indexing catches up.
- **Critic semantics** — `requirement_coverage()` checks whether the *requested
  files* actually exist. A critic `PASS` that leaves a declared manifest file
  unwritten is now `NEEDS_REVIEW`, not `DONE`. Explicit requirement lines the
  user wrote out are shown to the critic but never block on their own.
- **Failure learning** — every non-clean run produces a concrete `next_step`,
  and a project session carries the last failure's diagnosis into the next
  plan instead of losing it with the run.

### 9. Funnel metrics become decisions (P2)

`GET /api/admin/funnel-metrics` now returns `alerts`: named, actionable signals
(`real_file_rate_low`, `code_only_rate_high`, `needs_review_rate_high`,
`approval_resume_rate_low`, `no_grounded_recall`) with the number that
triggered them. Rules stay silent below 10 samples — an alert nobody can trust
gets ignored.

### 10. Embedding-swap recovery UX (P2)

`stale_embedder` (part of the index still built by the previous embedder) was
computed but invisible. It now renders a notice that names the problem and
offers the one action that fixes it, with an honest failure message.

### 11. Structure and harness

- `lattice_brain/ingestion_jobs.py` — background job scheduling/progress split
  out of the ingestion pipeline (behaviour-preserving; all imports still work).
- `latticeai/core/workspace_review_items.py` — review-queue persistence split
  out of `WorkspaceOSStore`, following the existing collaborator pattern.
- Six new multi-agent/workflow scenarios: retry exhaustion, recovery without
  hiding the earlier failure, two-gate pause/resume, stale resume cursors,
  per-role observability, and cross-run role isolation.

## Honest limitations

- **VS Code** has no evidence→action one-click and no live step SSE timeline;
  its agent summary is post-run. Telegram has no grounding badge and no Review
  Center. Both are recorded as `✖` gaps in `docs/SURFACE_PARITY.md`.
- **The browser extension stays capture-only by design** — recall, approval and
  generation are intentionally not provided there.
- The **artifact ledger is process-local** and bounded: it answers "what did
  this conversation just make?" for minutes, not days. After a restart, normal
  retrieval answers better because indexing has caught up.
- **Explicit requirement lines are advisory.** Only *declared files* block a
  `DONE`; matching a prose feature to a transcript stays the critic's judgement.
- Changing the chunking strategy for prose formats means newly ingested
  `.txt/.pdf/.docx/.html` content gets different chunk ids than before. Already
  indexed content is untouched.

## Verification

- unit: 1670+ tests, including new suites for run explanation, evidence
  actions, prose chunking + citation locators, the context contract, relation
  evidence, project sessions, and the three loop closures
- frontend: vitest (evidence actions, run explanation, stale-embedder recovery)
- VS Code: `node --test tests/vscode-extension.test.cjs` (wired into `npm run lint`)
- `agent_eval` 23/23, brain quality eval, product readiness gate
- lint / ruff / typecheck / bundle budget / OpenAPI drift / i18n literals / docs gates
