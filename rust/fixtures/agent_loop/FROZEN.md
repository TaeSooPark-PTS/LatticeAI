# FROZEN — last generating tree: commit fc65e60

These agent-loop goldens were produced by
`scripts/generate_agent_loop_fixtures.py` against
`latticeai.core.agent.SingleAgentRuntime`. WP-P1 deleted the Python loop.
The fixtures stay; Rust `lattice-agent` tests keep asserting them.
Do not regenerate.

## What v11.9.0 added, and why it is kept apart

v11.9.0 taught the loop to drive a ~2B local model. Three of those changes
answer a question Python answered differently, so **nothing recorded here was
rewritten**: the new answers live under new keys, and the tests that read them
say in their own names that they are not parity.

| key | file | what it is |
|---|---|---|
| `agent_profiles_extended` | `helpers.json` | 6 rows. `e`-prefixed effective sizes (`gemma-4-e2b`) and the active-parameter markers (`a4b`, `A3B`) that must **not** read as sizes. Python answered `standard` for every row. |
| `extract_action_details_extended` | `helpers.json` | 20 rows (was 17). The three new parse rungs — `tag_strip`, `balanced`, `truncated_close` — plus the two rows that decline to fire, the three channel-frame rows, one channel-framed critic verdict that still carries `action`, and three verify5 live tapes (`labeled`: thought-only `Action:`/`Args:`, `` `tool` for `path` ``, `first step (`tool`)`). |
| `extract_verdict_details` | `helpers.json` | 4 rows. VERIFY-only parse: a critic object that names `verdict` (and may forget `"action": "verdict"`), including a channel frame and a truncated object. Not a Python record. |
| `trajectories_compact.json` | own file | 1 case. A `compact`-profile end-to-end run. **Not a Python record at all** — there is no Python loop left to record one — it is a native regression pin, and it says so in its own `schema`. |

Three rows of `extract_action_details` now answer differently from the record:
`broken_unclosed`, `double_object` and `slice_suffix`. All three were refusals
in Python and all three are real ~2B failure modes (a token limit that cut the
object short, two objects in one reply, a trailing sentence). They are **not
edited in place** — `tests/agent_loop.rs` names them in `RECOVERED_PAST_PYTHON`,
skips them in the parity test, and asserts the new answers from
`extract_action_details_extended`. A fourth key joining that list is a
behaviour change nobody planned, which is the point of writing them down.

The last-rung token verdict (`token_verdict` in the verify trace) is a
runtime recovery, not a frozen mapping: `verification.json` `cases` is
untouched. New parse answers live under `extract_verdict_details`.

Every other row, in every file, is byte-identical to what the generator emitted.
`git diff` on the v11.9.0 commit shows insertions only.
