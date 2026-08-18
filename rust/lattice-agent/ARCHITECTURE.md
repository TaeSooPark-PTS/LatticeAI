# `lattice-agent` — the domain map

Six groups, one rule: **the kernel decides, the surface carries.** Every module
lives in the group that names what it is for, and every group's `mod.rs` states
what belongs in it, what must never go in it, and its invariants. Read those
first — this page is the index, they are the contract.

```
surface/   HTTP in, HTTP out — and the client out to the Python compute worker
   │       router · looproutes · runbody · worker
   │  calls, never decides
kernel/    the agent loop, and every decision that can refuse
   │       agentloop/ (mod · execution/ · guided/ · planning · verification ·
   │                   fallback · gates · recovery · harness)
   │       policy · mode · permission · breaker · governor · proposals
   │       state · transcript · trace · plan · profile · probe · runs
   ├── parse/    untrusted model text in, typed values out
   │             action · channel · inference · pyjson · pyliteral · pyshlex · pystr
   ├── content/  the bytes a run is about to write
   │             sanitize/ (extract · validate · repair · salvage · python · text) · pydiff
   └── tools/    what a tool does, and the ground it may do it on
                 catalog · host · sandbox · command · exec · authorize · documents
                 args · files · shell · vault · desktop · render · scaffold · local

prompts/   the words the model is given when the caller supplies none
           mod (the three roles) · guided (the decomposed turns)
```

Arrows point down only. `kernel` never imports `surface`; `parse`, `content`
and `tools` never import `kernel`. If a change needs an arrow pointing back up,
the argument list is wrong, not the layering.

## Kernel vs surface

The kernel is what would still be worth having with no HTTP server attached: the
state machine, the gates, the run's own record. The surface is transport on both
edges — the axum routes callers reach us through, and the `worker` client this
process reaches Python compute through. A handler that answers "is this allowed"
itself has forked `kernel::permission`; a kernel module that knows how a caller
phrased something has been handed the wrong argument.

## The small-model harness (v12.0.0)

The product runs on whatever model fits the machine, and a 0.5B model is not a
smaller 70B — it is a model that cannot hold a tool-call contract at all. Three
pieces answer that, and none of them names a model:

**Three dials, one of which stops asking for JSON.** `kernel/profile.rs` adds
`guided` beside `standard` and `compact`. Under `guided`, `kernel/agentloop/
guided.rs` decomposes a step into micro-turns: *"choose an action — one
number"* against the run's real numbered catalog, then one turn per required
argument (`path` on one line; `content` as free-form text, with no JSON
escaping anywhere near it). **The harness assembles the action struct**, and
hands it to `Runtime::perform_action` — the same tail every mode runs, so the
gate chain, the loop guard, the pre-write snapshot and `sanitize_write_content`
are identical. Verification gets the same treatment: a closed `PASS`/`FAIL`
question plus one line of reason, judged by the same evidence and coverage
gates a JSON verdict passes. Nothing here is a shortcut past governance, only
past the JSON.

**Structure by construction on the seam.** `Completion::prefix` forces the
completion to *begin* with given characters — `compact` sends
`{"thoughts": "`, so a preamble, a markdown fence or a `<|channel|>` frame
stops being something the model can emit rather than something the repair chain
undoes. The worker prefills it into the prompt after the generation marker and
guarantees the returned text starts with it. A token-level JSON grammar was
considered and **not** shipped: it needs a tokenizer-aware incremental parser
and a per-token Python callback on the single MLX executor, and `guided` already
gives the same guarantee at zero per-token cost by not asking for JSON at all.

**A measurement, not a guess.** `kernel/probe.rs` asks the model two fixed
questions once — emit this action object (whose content carries a newline and a
quote, the two characters that break weak models inside a JSON string), and read
this three-row menu — scores the first with the loop's *own* parser, and picks
the dial: clean → `standard`, repaired → `compact`, unparseable → `guided`. The
verdict is cached per model id **and crate version** under the data directory.
`LATTICEAI_AGENT_PROFILE` still pins any dial and outranks everything;
`LATTICEAI_AGENT_PROBE=0` switches measuring off. The size regex survives only
as the prior for when no measurement is possible. Probing is a **port**
(`LoopDeps::probe`), off in `LoopDeps::new` and on at the wire, so no harness or
frozen trajectory silently pays for two completions and a write under `$HOME`.

**Self-correction, downward only.** A probe asks a toy question once; a real
task is harder. A run that was measured `standard`/`compact` and then spent its
whole format budget producing nothing — copying the worked example, or emitting
nothing parseable — **demotes itself to `guided` and finishes there**
(`execution::Runtime::demote_to_guided`). Three conditions guard it: the dial
must have been *measured* (a caller that injected one keeps it), it must not
already be `guided`, and the run must have no execution evidence yet. It never
promotes.

**When the model stops steering, the plan does.** The escape hatch
(`kernel/agentloop/fallback.rs`) has two halves and they run in that order:
`direct_plan_path` dispatches the plan's own **non-write** steps — plus, once,
a `skill.` row the *request itself named* — through `Runtime::perform_action`,
the same tail every dial runs; then `direct_file_path` writes the plan's files
with those results in front of the content turn. Nothing here is invented: every
name and argument comes from the plan this run already produced, a name the
catalog does not carry is skipped, and a step that already succeeded is not
re-run. It exists because the file half alone could only write: a plan of *read
the README, then summarise it* reached the write with no README in sight and a
live model fabricated the summary, and a plan of one `list_dir` or one
`mcp.grep` produced nothing at all. A run that is failing the same way twice —
the same action, the same arguments, the same tool error — takes the same hatch
(`guided::REPEAT_FAILURE_LIMIT`, enforced on both dials since v12.0.0) rather
than spending its whole step budget on a refusal it has already had.

**Refusing our own text.** Weak models continue the nearest complete shape, and
in an agent turn that is the prompt. The loop refuses to act on its own words:
a `write_file` whose `content` is `prompts::WRITE_EXAMPLE_CONTENT` verbatim is
recorded as `COPIED_EXAMPLE` and written nowhere, a guided answer that opens
with one of our own instruction lines has that line stripped
(`guided::answers::strip_echoed_lines`), and the critic's placeholder reason is
blanked rather than shown. All of these compare against constants this crate
owns, so none of them can ever reject a genuine answer.

**One catalog for three kinds.** `tools/catalog.rs` is the single vocabulary:
native tools from the run's policy table, `mcp.<tool>` from the host's MCP
surface, `skill.<name>` from the skill registry — one numbered menu, one set of
argument signatures, one dispatch decision. `resolve` routes a prefixed name
whose bare form the run governs to the **native** path, which is the stricter of
the two governance chains; only a name the run has no policy for reaches the
host's `ToolCatalog` port. A skill is stated to be guidance rather than an
executable: choosing one returns its `SKILL.md`, which the loop then keeps in
front of the model, and the run still has to pick a real tool. `lattice-host`'s
`gateway/agent_catalog.rs` is the product implementation, reading the very
directory `POST /mcp` scans.

**A skill the request names by name is consulted first.** When the user's own
sentence names an installed skill by its catalog name — `code_review 스킬을
참고해서 …` — the harness performs that one consult deterministically as the
run's first step, on every dial, and then hands control straight back to the
model (`execution::Runtime::consult_named_skill`). The `SKILL.md` lands on the
transcript, where the executor prompt and the guided brief already read it, and
nothing else changes: no tool is chosen for the model, no file is written, and
the model still decides what to do and authors every byte of it. **This is
honouring an explicit user instruction, not the harness doing the choosing** —
naming an installed skill *is* the instruction to read it, the same way a
slash-command is, and the run that ignores it has ignored the user rather than
exercised judgement (a live gemma-4-e2b did exactly that on three attempts out
of three). It fires only when the request names a skill row this run actually
offers, only before anything has executed, and only once per run; a request that
names none leaves the choice entirely to the model, as it always did.

**MCP dispatch (v12.0.0):** `lattice_platform::mcp::dispatch_for_agent` is
public and is the same `check_governance` `POST /mcp` runs. `resolve` still
sends a prefixed name the run already governs through the kernel's stricter
chain; a name the run does *not* govern reaches `PlatformCatalog::execute`,
which calls that symbol. A governance refusal, an unknown tool, or a missing
tool surface is a tool error — never a bypass.

## Where do I add…

**…a new tool?** `tools/`, in the file named for what it *touches* — `files` for
workspace files, `vault` for the brain/Obsidian vault, `shell` for subprocesses,
`desktop` for OS actuation, `render` for document creators, `scaffold` for
project templates, `local` for paths outside the workspace. Then add the name to
`tools::host::MUTATING_TOOLS` (or `RENDER_TOOLS`) and the arm to the dispatcher
in `tools/host.rs`. `is_native` follows from those tables. Every path resolves
through `tools::sandbox::Workspace::resolve`; every write passes
`content::sanitize::sanitize_write_content`. Do **not** add a permission check —
the kernel already said yes.

**…a new parse rung?** `parse/action.rs` (or `parse/channel.rs` for a new
model-family frame). Append it to the ordered chain — cheapest and most literal
first — and **name it in the returned `repairs` list**. A rung that repairs
silently is a bug: it hides exactly the weak-model regression the list exists to
show. Never let a rung supply a tool name, path or argument the model did not
write.

**…a new prompt?** `prompts/mod.rs`, as a constant plus the test that feeds it
through the real parser. A caller-supplied prompt always wins; the built-in only
fills a blank. Tool names come from the run's policy table, never a second list.

**…a new gate or verdict?** `kernel/`, and it fails **closed**: the unknown case
is a refusal, an unreachable verifier is `NEEDS_REVIEW`, a policy-less tool is
its most dangerous plausible class. Order is the contract — the gate chain in
`kernel/agentloop/gates.rs` and the priority chain in
`kernel::permission::block_reason_for_tool` are pinned by the frozen goldens
under `rust/fixtures/agent/`, and reordering them is a behaviour change.

**…a new route?** `surface/`, with its body in `runbody.rs` and its 400 through
`surface::bad_request`. The route translates, calls the kernel, translates back.

## Compatibility

`src/lib.rs` ends with the compatibility map: every `lattice_agent::…` path that
existed before the v12.0.0 regrouping still resolves, spelled exactly as it was
(`lattice_agent::sandbox`, `lattice_agent::agentloop`, `lattice_agent::sanitize`
and the rest are `pub use` re-exports of their new homes). `lattice-host`,
`lattice-platform`, `lattice-chat` and `lattice-core` import those names, so the
map is also the honest list of what outside code depends on — nothing may be
dropped from it without a coordinated change in those crates.

New code inside this crate uses the real path (`crate::kernel::state`, not
`crate::state`); the aliases exist for the consumers, not for us.
