# v10.8.0 — Within Reach (2026-08-04)

> **Status: historical** — point-in-time release note.

10.7.0 rearranged all twelve screens. 10.8.0 is about the things that were
already there but out of reach: a button below the fold, a message in a
language you did not choose, a file a small model nearly produced, an index
that re-read everything to discover nothing had changed.

---

## The first three screens fit on the screen

The onboarding shell rendered the Brain organism at hero size — 390px — above
**every** step of the flow, not just the welcome one. On a 1440×900 display
that meant:

| Screen | Where the thing you were asked to do began |
| --- | --- |
| Login | y ≈ 720, ~400px below the fold |
| Recommended models | below the fold; the model card needed a scroll |
| Install progress | below the fold; the buttons were off screen |

The first interaction the product asked a new person for was a scroll.

`.ritual-brain` now carries `data-scale`: `hero` on the welcome step, `mark`
(104px) on every step after it — the same organism, so the identity carries
through the flow, sized to what the screen is about. The welcome screen itself
went from 1175px of content to 770px, so **`Brain 지금 깨우기` is visible
without scrolling** on a laptop.

Two more layout defects fixed along the way:

- **The recommended-models column sat off-centre.** `.ritual-recommend` is a
  34rem column inside a 54rem shell with no auto margin, so it hugged the left
  edge with ~180px of dead space down the right.
- **The install screen's four stages were a stacked list** in a 966px card —
  about a fifth of the card carrying content. They are a horizontal rail now,
  which also says "four steps, you are on the second" at a glance.

## The install screen printed a raw translation key

`InstallScreen` asked for `flow.install.stage.${stage}` with a human sentence
as `defaultValue`. `t()` never read `defaultValue` — it treated it as an
interpolation value — so the three stages with no copy (`idle`, `install`,
`done`) rendered the literal text **`flow.install.stage.idle`** to the person
installing their first model. `t()` reads the option now, the missing copy was
written, and `frontend/src/i18n.test.ts` fails on any `InstallStage` without
an entry in both languages.

## One language per screen — including the ones the server writes

Every user-facing API message was a literal at the raise site, written in
whichever language the endpoint's author was thinking in:

```
latticeai/api/auth.py     detail="사용자를 찾을 수 없습니다."
latticeai/api/browser.py  detail="Knowledge Graph ingestion is disabled."
```

Whichever language you read, half the product answered in the other one.

`latticeai/core/messages.py` is a catalog with both languages and one
resolution rule: the language comes from the request — `X-Lattice-Language`
first (the choice made *in the product*), then `Accept-Language` (what the
browser was installed with), never from the call site. `auth.py`, `admin.py`
and `browser.py` are migrated; the web app sends the header on every request.

`tests/unit/test_server_messages.py` holds the line: a message present in one
language and missing from the other fails, an English entry containing Korean
fails, a migrated router that reverts to a hardcoded literal fails, and a
typo'd key fails before it can render as itself.

**The browser extension had the same problem inside a single popup** — the
capture status was English ("Added to Knowledge Graph"), the grounding badge
and approvals line were Korean. It has a catalog too, resolved from the
browser's UI language, and it sends `X-Lattice-Language` so the server's half
of the conversation matches.

## A weak local model's near-miss is no longer thrown away

Three fixes to the file-generation pipeline, all of which matter most on the
1–4B models Lattice AI is built to run:

- **Repair got handed the wrong candidate.** When every attempt failed
  validation, the *longest* rejected reply went to deterministic repair — so a
  900-character apology beat a 300-character HTML document that only needed
  its `</html>`. Repair can finish the document; it can only bury the apology.
  Candidates are scored by how close they are to being a file now, with length
  breaking ties inside a tier.
- **A repeated reply burned the retry.** Handed the same corrective feedback,
  a small model frequently replays its answer verbatim. That is now detected,
  recorded in the trace, and buys **one** extra attempt — with a prompt that
  names the repetition — instead of a third identical round trip.
- **Prose file types had no validation at all.** `.md`, `.txt` and friends fell
  through to `return True, "ok"`, so a model that answered "Sure! Here is the
  document you asked for:" and stopped had its sentence saved as the file. The
  check is deliberately conservative: only a short reply that both opens
  conversationally and never grows into content is rejected.

The multi-agent runtime's plan parser got the same treatment. It sliced from
the first `{` to the last `}`, which fails whenever the model wrote anything
after its object. It scans for balanced spans now (string-aware), strips
`<think>` scratchpads, and repairs trailing commas — recovery only, never
invention: when nothing parses the run still fails loudly with the raw output
preserved.

## Re-indexing costs what changed

`rebuild_vector_index(full=False)` materialised the full text of every node and
chunk in memory before deciding what to skip, then asked one `SELECT` per item
whether it had changed — a round trip per item, almost all of which answer
"unchanged". It streams now, and reads the `item_id → text_hash` map in a
single query.

The behaviour is asserted rather than assumed:
`test_incremental_rebuild_embeds_nothing_when_nothing_changed` counts the
embedder's calls and requires zero.

## More ground under the frontend

| | 10.7.0 | 10.8.0 |
| --- | --- | --- |
| Statements | 47.35% | 52.26% |
| Branches | 42.45% | 46.29% |
| Tests | 424 | 504 |

New suites for files that had none: `App.tsx` (the shell that decides what
every user sees — 0% before), `features/admin/AdminConsole.tsx` (0%),
`lib/folderPicker.ts` (2.7%), and the rest of `api/client.ts`.

Also new: `frontend/src/styles/mediaQueryOverride.test.ts`, a guard for the
bug that produced this release's worst-looking defect — a media query that
re-declares `display: flex` without restating `flex-direction`, so the
suggestion chips on the Brain home inherited a column axis and every "pill"
stretched to 952px to hold four characters.

## Decomposition

`latticeai/core/workspace_os.py` 1128 → 945 lines. Indexing, relationships,
onboarding and computer-memory each moved to a manager of their own, composed
the same way the existing ones are.

---

## Verification

- Python: 2219 passed, 11 skipped · `ruff` clean · `mypy` clean (276 files)
- Frontend: 504 passed · `tsc --noEmit` clean · bundle budget, i18n literal and
  namespace gates green
- Extensions: browser 22 passed, VS Code 19 passed

## Honest limitations

- Coverage is 52%, not 80%. The largest remaining gaps are `BrainConversation`,
  `useBrainChat` and `IngestionPanels` — the streaming chat path, which needs a
  fake SSE harness rather than more render tests.
- Server-side i18n covers `auth.py`, `admin.py` and `browser.py`. The other
  routers still raise hardcoded English; the catalog and the gate are in place
  for them, but the migration is not finished.
- The welcome screen fits in 770px against a 747px viewport — the closing
  footnote line is still a few pixels below the fold at that height.
- The extension resolves its language from the browser, not from the web app's
  own setting: the popup is a separate origin and cannot read it.

## Artifacts

- `dist/ltcai-10.8.0-py3-none-any.whl`
- `dist/ltcai-10.8.0.tar.gz`
- `ltcai-10.8.0.tgz`
- `dist/ltcai-10.8.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_10.8.0_aarch64.dmg`
