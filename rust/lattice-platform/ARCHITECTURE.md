# `lattice-platform` — the domain map

Seven domains, one rule: **this crate is the product's surface.** It offers
things; it does not decide whether they are allowed, and it does not own what is
true. Every module lives in the domain that names what it is *for*, and every
domain's `mod.rs` states what belongs in it, what must never go in it, and its
invariants. Read those first — this page is the index, they are the contract.

```
shell/         the browser's first contact — the SPA shell and every 308
               static_ui · ui_redirects

workspaceos/   the place a person works, and who may be in it
               workspace/ · invitations · permissions · permission_mode
               features · project_sessions · realtime

governance/    work the product does on its own, and the gate in front of it
               review_queue/ · change_proposals · automation/
               workflow_designer/ · hooks/

toolsurface/   every capability the product can reach, and its catalogs
               mcp/ · tools/ · plugins · marketplace · agents/
               agent_registry · computer_use

knowledge/     how graph content crosses a trust boundary
               portability/ · network · network_boundary · voice

modelops/      which model runs here, and getting the machine ready for it
               models_catalog/ · setup/

adminops/      operating the install
               admin/ · security_dashboard/ · funnel_metrics
```

Arrows point **down**, out of this crate: into `lattice-auth` (who is this),
`lattice-agent` (may it run, and what does it do), `lattice-core` (what is true)
and the Python worker over `WorkerSeamClient` (compute). A domain never reaches
sideways into a sibling's internals. Four couplings do cross a domain line, and
each is named in the relevant `mod.rs`: `governance` writes through
`workspaceos`'s single store, `toolsurface` speaks `mcp`'s HTTP vocabulary,
`modelops`' setup reads its own catalog's host probe, and `adminops`' two
readers share `admin`'s one audit writer.

## Product surface vs kernel vs truth

| crate | question | example |
|---|---|---|
| **`lattice-platform`** | what does the product **offer**? | `POST /api/knowledge-graph/export`, the Review Center, `/setup/scan` |
| `lattice-agent` (kernel) | **may** this run, and what does it **do**? | `block_reason_for_tool`, the loop's gates, the native `write_file` |
| `lattice-core` | what is **true**? | the graph, its one writer, the state files |

A handler here that re-derives "is this allowed" has forked
`lattice_agent::kernel::permission`. A module here that opens the graph directly
has become a second writer. Both are the failure this boundary exists to
prevent. Compute — inference, the document parser/generator matrix, ASR — stays
with the Python worker.

## Where do I add…

**…a new route family?** A new module in the domain that names its subject, with
`MOUNTED: &[(&str, &str)]` listing its (method, path) pairs and a
`router(state) -> Router` factory; then mount the factory in `lattice-host`'s
`gateway/product.rs`. One module owns exactly one family, so two work packages
never edit the same file. `lattice-host` asserts the union of every `MOUNTED`
has no duplicates *before* the router is built, so a double-mount is a named
assertion rather than a panic in a constructor. If the route is a **page
shell**, it is a 308 in `shell/ui_redirects` instead — except `GET /plugins/sdk`,
which carries its own `require_user` and is therefore `toolsurface::plugins`'.

**…a new tool?** `toolsurface/tools/`, in the file named for what it *touches*
(`fs`, `shell`, `knowledge`, `downloads`, `meta`), plus its class in
`tools::governance_for`, which `lattice-host` reads from outside the crate.
Filesystem writes go through `lattice_agent::sandbox::Workspace` so the agent's
`write_file`, `/tools/write_file` and an approved change proposal share one path
policy. A tool that runs *inside an agent turn* is not added here at all — its
handler is `lattice_agent::tools`, and this crate only exposes it over HTTP.

**…a workflow node, a hook, or a review-item type?** `governance/`. Whatever
stages a change carries its base SHA: an approval whose base no longer matches
is a **409**, never a merge and never a silent overwrite.

**…state?** Through the family that already owns the file. `workspace_os.json`
has exactly one writer — `workspaceos::workspace::store::WorkspaceOsStore` — and
`governance::review_queue::GovernanceState` is a facade over the *same* `Arc`,
not a second copy. Two stores over one document is last-writer-wins, the bug
v11.7.0 closed.

**…an audit line?** `adminops::admin::append_audit_event`. One appender, one
file, one format; `lattice-chat` and `lattice-host` call into it from outside
rather than growing writers of their own.

## Compatibility

`src/lib.rs` ends with the compatibility map: every `lattice_platform::…` path
that existed before the v12.0.0 regrouping still resolves, spelled exactly as it
was. `lattice-host` imports **all thirty** family modules by name in one `use`
(`gateway/product.rs`), `lattice-chat` imports `admin`, and this crate's own
integration tests name a dozen more — so the map is also the honest list of what
outside code depends on. Nothing may be dropped from it without a coordinated
change in those crates.

New code inside this crate uses the real path (`crate::adminops::admin`, not
`crate::admin`), so a reader of any file sees which domain it is borrowing from.
The aliases exist for the consumers, not for us.
