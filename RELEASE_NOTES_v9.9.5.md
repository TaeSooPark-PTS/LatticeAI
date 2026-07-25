# Lattice AI 9.9.5 — Closed Gaps

**Release date:** 2026-07-26

9.9.5 finishes the seven residual gaps left after 9.9.4 Durable Loops and
completes the knowledge-graph read-surface decomposition that was mid-flight.

## Highlights

### 1. One durable approval path (L1)

Legacy `human_in_loop` no longer uses a separate in-memory `_pending` map.
Pauses go through the same durable `AgentRunStore` as `awaiting_approval`
(`legacy_context=True`). Wire compatibility is preserved:

- response: `status=waiting_approval` + `context_id` (= `run_id`)
- resume: token-less `context_id` still works for legacy pauses only
- modern pauses still require `run_id` + `approval_token`

### 2. Rollback: none | git | snapshot (L7)

Before file-create actions the runtime snapshots workspace state (bounded
content). On rollback:

1. try git when governance says `rollback=git`
2. else restore the pre-write snapshot (or delete a file the run created)
3. else report `mode=none` honestly

### 3. Critic artifact checklist (L4)

Verify prompts include a deterministic list of written paths with
sanitize/repair honesty flags. Auto-repaired scaffolds cannot pass as
fulfillment without the critic noticing.

### 4. Mid-run workspace awareness (L5)

Executor prompts list files this run already wrote so multi-step work sees
its own outputs.

### 5. Optional cross-encoder rerank

```bash
LATTICEAI_CROSS_ENCODER_RERANK=1
# optional:
LATTICEAI_CROSS_ENCODER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
```

Default is identity (no download, no latency). Failures never break search.
Hybrid responses add a `rerank: {mode, model, detail}` block.

### 6. Surface parity — VS Code & Telegram approvals

| Surface | What shipped |
| --- | --- |
| VS Code | `ltcai.listApprovals` / `approveAgent` / `rejectAgent`; token cache from pause responses; paste fallback |
| Telegram | Handles `waiting_approval` and `awaiting_approval`; token-first `/agent/resume` |

### 7. Sidecar Playwright nightly E2E (Wave 3.2)

```bash
npm run test:e2e:sidecar
```

Starts an isolated FastAPI sidecar and runs `tests/e2e/sidecar-first-value.spec.js`.
Scheduled via `.github/workflows/e2e-sidecar.yml`.

### Refactor

`KnowledgeGraphReadsMixin` (`lattice_brain/graph/retrieval_reads.py`) owns
list/neighbor/traverse/stats reads; `KnowledgeGraphRetrievalMixin` keeps the
search surface. Store MRO wires both.

## Artifacts

- `dist/ltcai-9.9.5-py3-none-any.whl`
- `dist/ltcai-9.9.5.tar.gz`
- `ltcai-9.9.5.tgz`
- `dist/ltcai-9.9.5.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_9.9.5_aarch64.dmg`

## Honest Limitations

- Cross-encoder weights are not vendored; the option fails open to identity
  when `sentence_transformers` or the model is unavailable.
- VS Code approval tokens are not re-issued by `GET /agent/approvals`
  (security). Runs started outside the extension need a pasted token or the
  web UI.
- Review Center remains web/desktop-only (documented SURFACE_PARITY gap).

## Upgrade notes

No migration. Existing durable approval files gain an optional
`legacy_context` field (default false).
