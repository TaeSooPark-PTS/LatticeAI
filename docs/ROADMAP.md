# Lattice AI gap roadmap

> **Status: reference** — point-in-time inventory, redated at v12.0.0. Not a
> canonical current-release document; version gates do not bind it.

As of **2026-08-29**, against the 12.1.0 — Fast Path tree. The rows below
were gathered against 11.9.0 and re-checked when 12.0.0 shipped; what that
release closed is marked closed rather than deleted, so the ledger still
shows what was owed.

The owner's v12.0.0 priority was complexity management. The crate maps that
landed with that work are
[`rust/lattice-agent/ARCHITECTURE.md`](../rust/lattice-agent/ARCHITECTURE.md)
and
[`rust/lattice-platform/ARCHITECTURE.md`](../rust/lattice-platform/ARCHITECTURE.md);
the contributor path is [DEVELOPMENT.md](DEVELOPMENT.md). This page is the
honest leftover list, gathered from
[FEATURE_STATUS.md](../FEATURE_STATUS.md) Known Limitations and the §정직한
고지 sections of
[RELEASE_NOTES_v11.9.0.md](../RELEASE_NOTES_v11.9.0.md) and
[RELEASE_NOTES_v12.0.0.md](../RELEASE_NOTES_v12.0.0.md).

Four items those ledgers named **closed in 12.0.0**. They are kept below so
they are not re-opened as work. Everything else is still open.

Difficulty: **S** one focused change · **M** a vertical slice · **L** a
program (credentials, a model harness, or an index format).

## Closed in 12.0.0 — do not treat as open

| 무엇 | 어떻게 닫혔나 | 11.9.0 장부에 적혀 있던 말 |
| --- | --- | --- |
| Restore-then-read in a long-lived process | **Closed.** The store carries a generation epoch: a restore bumps it, connections opened under the old generation are stale at their next checkout, and the next read sees restored bytes. No restart. | 복원 뒤 커넥션이 재활용되기 전까지 복원 전 바이트를 줄 수 있다. |
| brew / pip setup items | **Closed, without turning into automation.** `/setup/install` runs brew/pip/uv for an item the request names *and* the server-derived allowlist contains; anything else is refused by name, and the default path is still manual. | 설계상 수동. |
| `POST /mcp` outside the OpenAPI product contract | **Closed.** Declared in the mount table as one JSON-RPC envelope operation, so it generates into the spec; natively mounted, so it is never proxied to the worker. | 계약 밖은 설계. |
| Pointer tools undeclared | **Closed.** `pip install "ltcai[pointer]"` is a named extra. A stock install still answers `pointer_tools=false` — the gap was the undeclared capability, not the default. | 재고 설치에서는 unavailable. |

Two more moved a long way without closing, and their rows below say so
rather than being deleted: the small-model harness (P1) and the HNSW index
(P2).

## P1 — default path is wrong or the headline lies

| 무엇 | 왜 남았나 | 난이도 | 우선순위 | 제안 경로 |
| --- | --- | --- | --- | --- |
| **Small-model agent-run content quality** | **Half closed in 12.0.0.** The mechanical half is done: the `guided` profile stops asking for JSON, the dial is chosen by a measured probe, a run that produces nothing demotes itself downward mid-run, and a 0.5B model reached `DONE` in 3.9s with a real file. The half still open is the *writing* — a weak summary fails the critic and the run ends `FAILED` / `NEEDS_REVIEW`. The cross-model matrix is improving from 11 of 18. | L | **P1** — still the voice the product ships on 8GB, and still the reason a first run can end in a review state. | Keep measuring with the loop's own parser, not a second harness. New answers go under new fixture keys (`agent_loop` `*_extended`); do not rewrite frozen rows. The remaining lever is content, not format: better plan→content prompting and a critic that says *what* was thin, per-model hacks still forbidden. |
| **Real-embedding default rollout** | **Narrowed in 12.0.0, not closed.** Auto-detection now adopts a real downloaded embedding model when one is present, the hash model is labelled `fallback` rather than presented as semantic, and vector identity is filtered by `(model, dim)` so two spaces cannot mix. What is still true: on a machine that has downloaded nothing, the default remains `lattice-local-hash-v1` (blake2 bag-of-features), and "semantic" recall is then a hash of tokens. | M | **P1** — recall is the product. Hash as a labelled fallback is fine; hash as the silent default is not. | Make the first-run flow offer the embedding model the way it offers a chat model, and keep the switch honest: drain the backlog through `lattice-jobs` so a change of embedder is a reindex, and report `stale_embedder` until the queue is empty. |
| **Worker-side batch embed** | **Closed in 12.1.0 on the ingest-time door**: one `/worker/embed` body is the document vector followed by every chunk, extract runs beside it, and `write_vectors_with` files those vectors. The drain planner stays the backlog path. Folder/watch overlap up to four files. What remains for a real-embed default is auto-detection on first run, not a second embedder. | M | **P1 → closed for the ingest door; the real-embed default row above is still open.** | Keep. Do not open a second embedder inside `lattice-core`. |

## P2 — real, but the product still works without them

| 무엇 | 왜 남았나 | 난이도 | 우선순위 | 제안 경로 |
| --- | --- | --- | --- | --- |
| **HNSW whole-rebuild** | **Closed in 12.0.0; the default is deliberately unchanged.** The sidecar appends incrementally instead of being invalidated by every write, and the index is now used in real search: `hnsw+rescore` fetches `k * 8` candidate ids (cap 200) from `POST /worker/vector/query` and rescores exactly those rows natively, so recall is approximate and ordering is exact. Failures fall back to the exact scan carrying their reason. | L | **P2 → resolved for scale; the default stays `brute`.** | Nothing to build. The remaining decision is when to flip the default, and that needs recall@10 re-measured against the exact scan on a Brain that has been ingesting continuously — not a synthetic corpus. |
| **Unsigned DMG** | `Lattice AI_12.0.0_aarch64.dmg` is ad-hoc signed (= unsigned). First launch needs the Gatekeeper bypass. `npm run release:validate` checks names and presence, not a Developer ID. | M | **P2** — first-run tax on desktop, not a Brain bug. Needs an Apple identity. | Developer ID Application + notarization in the release path. Exact artifact name stays `dist/…` / `src-tauri/target/release/bundle/dmg/Lattice AI_X.Y.Z_aarch64.dmg`. Credentials stay off the tree. |
| **`api_key` cloud path is mock-only** | Contract-tested against a mock server. Never live-called — no billing budget. Live E2E is `cli_oauth` (`agy` / `grok`) at zero API billing. | S | **P2** — the supported cloud path already works. This is the paid-key twin. | One live contract test against a real OpenAI-compatible endpoint once a budget exists. Do not call the mock path "verified" in FEATURE_STATUS. Keep `local_only` winning on the request. |
| **Vault-watch full-resync** | **Largely closed in 12.0.0**: the ingest fingerprint (size + mtime + content sha256) is on the watch path, so an unchanged note is not re-read, re-chunked or re-embedded. The pass itself still runs once through the bridge, because link edges need node ids only a completed ingest has. Cap is still 2,000 notes/run (`truncated: true`), still off by default. | M | **P2** — the wasteful part is gone; the shape of the pass is unchanged. | Attach links from the ids that already landed so the bridge does not need a whole pass at all. Keep the 2,000 cap and the `truncated` flag. Inline `#tags` stay out of scope. Watch still never deletes — cleanup is the confirmed prune door. |
| **Self-Model has no screen** | Writes were restored in 11.7.0. `GET/POST/DELETE /api/memory/self-model*` and Review Center proposals work. `/app` has no profile view — "Brain이 나에 대해 뭐라고 생각하나" is an API call. | M | **P2** — the data path is real; the reader cannot see it. | A panel under `frontend/src/features/brain/` + `frontend/src/i18n/brain/`, reading the existing API. No new writer. Vitest 100% `all: true`. |

## P3 — honesty leftovers that should not grow

| 무엇 | 왜 남았나 | 난이도 | 우선순위 | 제안 경로 |
| --- | --- | --- | --- | --- |
| **API 오류 영어 리터럴** | FEATURE_STATUS carried a 10.9.0 inventory (17 of 50 *Python* routers) until 12.0.0 corrected it. After 11.6.0 the product routers are Rust. The live ratchet (`scripts/check_server_i18n.mjs`) locks **4** worker routers and reports **2** unclaimed (`health`, `search`). The larger leftover is native Rust `detail` strings that never entered `latticeai/core/messages.py`. | M | **P3** — the everyday SPA path is Korean. This is the remaining API/error English. | Finish the two worker routers (add them to `LOCALIZED`). Then a Rust-side catalog + a ratchet over `lattice-platform` / `lattice-auth` refusals. The 10.9.0 sentence is gone from FEATURE_STATUS as of 12.0.0; do not reintroduce it. |
| **Image evidence does not keep the original** | An `Image` citation shows the 96px inline `data:` thumbnail stored at ingest, capped at 24 KB. Serving the real file would need a new static route over the user's disk or a reuse of `/local/serve` (every read passes an explicit approval). | M | **P3** — the thumbnail is an honest preview, not a fake original. Missing the file is a proof gap, not a crash. | Approval-gated `/local/serve` (or a content-addressed blob route that still requires consent). Keep the `data:` thumbnail as the no-approval preview. Do not add an ungated static mount. |

## Other leftovers — named, not dropped

These stay on the honesty ledger through 12.0.0. They are not P1. Do not
quietly delete the row.

| 무엇 | 왜 남았나 | 난이도 | 우선순위 | 제안 경로 |
| --- | --- | --- | --- | --- |
| Self-Model extraction is a phrase table; no `refiner` is wired | First-person ko/en patterns only. Survivable because every candidate is a proposal. | M | P3 | Optional model `refiner` behind a flag; keep proposal-first. |
| Self-Model `open_keys` accepts `pending` only | Python also accepted `snoozed`. Port narrowed on purpose. | S | P3 | Add `snoozed` only with a fixture row, not a silent widening. |
| `delete_node` leaves the `PART_OF` edge | Same as Python. 12.0.0's prune door sidesteps it by using `delete_document_tree` (both directions, zero dangling); the primitive itself is unchanged. | S | P3 | Decide whether to cascade; do not "fix" it in one crate only. |
| `POST /knowledge-graph/ingest` is text-only | Contract, not a missing parser. Binary ingest is `/upload/document` → `/worker/parse`. | — | — | Keep. Document, do not "open it up". |
| Review mutation is two store cycles | One writer, two transactions. | M | P3 | Collapse only if a single txn can carry both without a second `workspace_os.json` writer. |
| Review events silent without an installed owner | Standalone retrieval process has no owner to attribute. | S | P3 | Fail closed to "unsigned event", never invent an owner. |
| Long download is not cancellable | Closing the tab leaves the pull running on its worker thread. | M | P3 | Cooperative cancel on `POST /models/load` / `prepare-model`. |
| Quantized vector backend: no memory win, ~2.2× slower | int8 codes are 8× smaller; peak RSS barely moves because SQLite rows dominate. | L | P3 | Hold as the representation a disk-resident index would need. Do not recommend it. |
| No vision or speech model ships; video needs `ffmpeg` | Runtime refusals, honestly labelled. | — | — | Keep. Nothing is fabricated to fill those fields. |
| Image/video observation functions have no HTTP door | 11.8.0 deleted the only route; it wrapped a native ingest that was never built. | M | P3 | Do not restore a door nobody calls. Ingest-time observation stays in Brain Core under unit test. |
| Text→image fusion needs a shared-space vision model | Off by default; the response says so when the tower is missing. | M | P3 | Keep the honesty. Do not return text-only ranking as if fusion ran. |
| Interop bridges read exports, never a vendor API | Notion zip, local git, local `.eml`/`.ics`. No IMAP, no Notion API. | L | P3 | Stay "point me at what you exported". |
| Reorganization is one topic level and has no one-click undo | `topics/<주제>/`; no delete path; putting files back is a new proposal. | M | P3 | A reverse proposal at apply-time if undo is wanted. |
| Browser extension language follows the browser | Separate origin; cannot read `lattice.language`. | M | P3 | Server-side language preference, then both clients read it. |
| No real-model CI runner | `agent-smoke.yml` was deleted because hosted runners have no MLX and the job failed open. | L | P3 | Restore only on a runner that actually loads a model. A fail-open job is worse than none. |
| Conversation artifact ledger is process-local | Minutes, not days. After restart, retrieval covers it. | S | P3 | Persist only if a test shows a user-visible hole after reboot. |

## Design boundaries — not a close-me

These were removed or scoped on purpose. A roadmap item that "restores
Telegram" or "turns hash embeddings back into the only story" is a product
decision, not a bug.

- Telegram bridge and SSO/OIDC login/callback — removed in 11.6.0 with the
  platform code that became the worker. Password login is native; the SSO
  *configuration* surface remains.
- Python coverage gate is a **line floor of 90** (11.8.0). Measured coverage
  is much higher; the enforced claim is the floor.
- Frozen HTTP / agent / retrieval goldens keep keys for surfaces that no
  longer exist. That is the point of a frozen record. Do not rewrite them to
  match today's tree.

## How to use this

Close a row by changing the code, then moving the row into a release note
— not by editing this file ahead of the work. 12.0.0 shipped, so the four
rows it closed are marked closed above and the rows it moved say how far;
FEATURE_STATUS and the release notes carry the same statements. This
reference page is redated at each release, not silently rewritten to look
current.
