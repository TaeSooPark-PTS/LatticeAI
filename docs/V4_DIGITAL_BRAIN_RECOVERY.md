# V4 Digital Brain — Transformation Program Recovery File

> **Purpose**: This file makes the v4.0.0 transformation program recoverable by any
> session (Claude, Codex, other models, or a human developer) without repeating
> completed analysis. **Update this file before ending any phase and before any
> likely session/context/usage limit.**
>
> Last updated: 2026-06-11 (session 2, after Phase A interruption + recovery)

---

## 1. Program Charter (from the user's v4.0.0 directive)

- Transform Lattice AI v3.6.0 into the **final-form Digital Brain Platform** (v4.0.0).
- Philosophy: models are temporary, knowledge is durable; user owns knowledge/memory/
  context; local-first, privacy-first, digital sovereignty.
- **Preserve capabilities** (may redesign, must not remove): local-first, Knowledge
  Graph (first-class, visible), graph visualization, search, model recommendation/
  installation, environment analysis, workflow/pipeline, multi-agent, personal +
  organization workspace, provenance, import/export, backup/restore.
- **Never fake functionality. No placeholders. No demo-only features.** If a
  capability can't be fully realized, build real architecture/interfaces/contracts.
- Git: work on `feat/v4-digital-brain` only; commit verified work frequently; push to
  remote feature branch; **no merge to main, no production release, no final tags** —
  prepare a release candidate and stop for review.
- Quality gates: lint, typecheck, tests, build, release-artifact validation, version
  refs updated, docs updated.
- Deliverables (13): product review, identity review, architecture review, UX review,
  data-model review, brain architecture proposal, implementation plan, implementation,
  validation results, risks/tradeoffs, remaining gaps, RC summary, commit history.

## 2. Current Phase

**Phase A (Repository Audit) — partially complete, being resumed.**

- 8-dimension parallel audit workflow ran (run ID `wf_d690b8d1-60c`, task `wsspwl45x`).
- 2 of 8 auditors completed; 6 failed on a session usage limit (resets 12am
  Asia/Seoul — now past). The 6 failed dimensions are being re-run.

## 3. Completed Work

1. **Baseline established (main @ 5889195, v3.6.0)**
   - Tests: `.venv/bin/python -m pytest tests/` → **455 unit pass, 9 integration
     fail**. The 9 failures are *pre-existing* `httpx.ConnectError`s — they need a
     live server. **Unit tests (`tests/unit`) are the validation gate.**
   - `.venv` Python is 3.14.5. `pyproject.toml` requires >=3.11 (avoid PEP 701
     f-strings nesting same quotes — 3.11 compat; CI runs 3.11).
   - Code inventory: `latticeai/` package ~15,007 lines (28 core modules, 16
     services, 27 API routers + `server_app.py` at 1,554 lines). Legacy root
     modules ~6,720 lines incl. `knowledge_graph.py` **4,633 lines**,
     `kg_schema.py` 521, `llm_router.py` 775, `mcp_registry.py` 791.
   - Frontend: `/app` v3 SPA (`static/v3/`, 22 views, token-native) is primary;
     legacy static HTML pages (`static/*.html`) still shipped in parallel.
   - Repo root clutter: ~30 `ltcai-*.tgz` tarballs, `ltcai-0.3.1/` extracted copy,
     logs, `chat_history.json`, 15MB pptx — most likely untracked; verify with
     `git ls-files` before cleaning.
2. **Branch created**: `feat/v4-digital-brain` (from main @ 5889195). No commits yet
   besides this recovery file.
3. **Phase A audits completed (2 of 8)** — full JSON in
   `/tmp/v4_audit_agent-workflow-runtime.json` and
   `/tmp/v4_audit_workspace-enterprise.json` (also summarized in §4 below; tmp files
   may not survive reboot — §4 is the durable record).

## 4. Findings (completed audit dimensions)

### 4.1 Agent & Workflow Runtime — VERDICT: one real runtime, two demo-grade ones

**Real (keep/extend):**
- `latticeai/core/agent.py` — genuine single-agent LLM state machine
  (PLAN→EXECUTE→VERIFY→ROLLBACK), real tool execution via `DEFAULT_TOOL_REGISTRY`
  (`tools/__init__.py:247-256`), destructive-action blocking, loop detection, git
  rollback, human-in-the-loop plan approval (`latticeai/api/chat.py:714-727`).
- Hooks platform is real as of v3.4+ (v3.3.0 gap closed): execution engine in
  `latticeai/core/hooks.py:498-713`, 7 built-ins bound at startup
  (`server_app.py:1327`), subprocess user hooks, fail-closed `pre_*` gates,
  persisted run log (`hooks_runs.json`), fired from agent/workflow/tool/ingestion.
- `dispatch_tool` (`hooks.py:187-233`) is the single shared tool lifecycle seam.
- `WorkflowEngine` (`core/workflow_engine.py`) is a clean, tested interpreter
  (validation, cycle guard, eval-free conditions) — the *engine* is fine.
- Tool governance single ownership point: `core/tool_registry.py`.

**Critical problems:**
- **Multi-Agent Runtime is deterministic theater**: production always uses
  `default_role_runner` (`platform_runtime.py:211-216`); planner emits canned
  3-step plan (`multi_agent.py:339-343`), self-approves, executor does no work,
  reviewer rubber-stamps — yet persists fake plans/handoffs/reviews into the
  workspace store **and the Knowledge Graph** (fabricated provenance).
- **Workflow runs execute nothing**: `platform_runtime._tool_node_runner` (:79-97)
  returns `{recorded: true}` instead of calling `execute_tool`; skill/plugin
  runners are existence checks. Runs finish "ok" having done zero work.
- Custom agents in `AgentRegistry` are metadata-only — orchestrator filters to 5
  hardcoded `AGENT_ROLES` (`multi_agent.py:476`); registration is a UI illusion.
- No async execution/cancellation/scheduling; `stop()` can't cancel; only
  'manual' trigger exists.
- Tool approval is audit-only (`agent.py:176-194` always auto-approves);
  per-tool human gate doesn't exist despite governance vocabulary.
- Two parallel agent systems with colliding names (`core/agent.py` vs
  `core/multi_agent.py`+`services/agent_runtime.py`).

**Key opportunities (= v4 work):** back orchestrator roles with the real
single-agent runtime + LLM router; make workflow tool nodes call `dispatch_tool`
with real governance (pause-for-approval state); async durable runs + SSE events +
real cancellation; trigger system (cron/interval + KG-event triggers via existing
hooks, e.g. "on document ingested, run workflow"); unify agent systems so registry
entries carry executable config (model/prompt/tool allowlist); route agent
learnings through `services/ingestion.py` with provenance; label simulation runs
honestly (`mode` field) until/unless execution is real.

### 4.2 Workspace, Identity & Enterprise — VERDICT: solid auth, illusory isolation

**Real (keep):** `core/oidc.py` (fail-closed OIDC verifier, anti-downgrade),
SSO nonce binding (`api/auth.py:137-201`), honest open-core enterprise seam
(`core/enterprise.py` — everything reports `enabled=False`), `core/security.py`
(scrypt, trusted-proxy XFF, constant-time compares), `PermissionGateway`
(path+action+user+hash+TTL consent), workspace role enforcement in store with
tests, non-destructive workspace migration.

**Critical problems:**
- **The actual "brain" is machine-global, not workspace-scoped**:
  `workspace_service.py:39` `SHARED_GLOBAL_AREAS = ('graph', 'skills')`;
  KG store constructed once per machine (`server_app.py:296+`); chat history
  global; portability export is admin-only machine-global. Personal vs
  Organization workspace isolation only covers auxiliary JSON records.
- **By-id authz bypasses**: `GET /workspace/snapshots/{id}` (+`/{area}`,
  `/export`, `/compare`) only `require_user` — any authenticated user reads any
  workspace's snapshots (`workspace.py:343-389`). Memory delete lacks ownership
  checks; `/workspace/os` leaks full registry incl. other orgs' member lists
  (`workspace_os.py:433`).
- Single unlocked whole-file `workspace_os.json` (1,959 lines module, 0 locks):
  lost updates under concurrency; silent `[-200:]`/`[-500:]` truncation of
  memories/traces/timeline — contradicts "knowledge is durable".
- Three conflicting role vocabularies (users.json admin|user; workspace
  owner/admin/member/viewer; `_ROLE_CAPS` matrix that **nothing enforces** though
  `admin.py:112-113` claims it's "the real access policy").
- Minor: session tokens stored plaintext; 4-char min password; dead
  `detect_edition()` env branch; dead `_sso_states`; org-creation timeline event
  mis-scoped; SSO lacks PKCE.

**Key opportunities (= v4 work):** partition KG by workspace (prereq for
Personal/Organization Brain) — `~/.ltcai/workspaces/<id>/` or workspace_id
columns, threaded through ingestion/search/portability; close by-id authz gaps
(small!); unify identity (stable user UUIDs, one policy module, real
invitations); per-workspace SQLite for workspace state (kill lost updates +
truncation); federation foundations: device keypair identity (keyring), signed
provenance-stamped export bundles, selective sharing; visibility levels
(private/workspace/org) on memories+nodes; per-user "take your brain with you"
export + encryption at rest; harden edges (hash session tokens, PKCE, password
policy).

### 4.3 Six dimensions NOT yet audited (re-running)

product-identity, backend-architecture, knowledge-data-model, frontend-ux,
memory-context, release-quality. **Do not treat these as audited.**

Known partial facts from inline scouting: `/app` SPA is primary surface
(`api/static_routes.py:112-117`); FEATURE_STATUS.md is the honesty ledger;
ARCHITECTURE.md already frames "Digital Brain Platform / KG-first";
KG v2 write-side backfilled but read-path still legacy (memory note,
verify in audit); version single-source spread across pyproject.toml,
package.json, setup.py (verify).

## 5. Decisions Made

1. `feat/v4-digital-brain` is the working branch; main untouched.
2. Unit tests (455) are the green gate; the 9 integration failures are
   pre-existing and excluded from the gate (re-verify they don't regress further).
3. Phase structure: A audit → B design (Brain Architecture Proposal + impl plan,
   with adversarial design review) → C implementation tracks (disjoint file
   ownership, frequent verified commits) → D validation + RC + final report.
4. Audit failures are re-run as a fresh 6-dimension workflow (not resume) to
   avoid cache ambiguity around failed agents.
5. Recovery discipline: update this file at every phase boundary and before
   any foreseeable limit.

## 6. Remaining Work / Exact Next Actions

1. **[NOW] Re-run the 6 failed audit dimensions** (same prompts as in workflow
   script `v4-audit-wf_d690b8d1-60c.js` under the session workflows/scripts dir;
   prompts are reproducible from §4.3 dimension list + FINDINGS schema).
2. Merge all 8 findings into §4 of this file; mark Phase A complete.
3. **Phase B**: write `docs/V4_BRAIN_ARCHITECTURE.md` (Brain Architecture
   Proposal) + `docs/V4_IMPLEMENTATION_PLAN.md`; run adversarial design review
   (2-3 critic agents); revise; commit.
4. **Phase C**: implement per the plan (queue below), committing after each
   verified track.
5. **Phase D**: full validation, version bump to 4.0.0 (RC), docs, release notes,
   push branch, final 13-deliverable report. STOP — wait for human review.

## 7. Detailed Implementation Queue (provisional until Phase B finalizes)

Derived from completed audits; Phase B will refine/extend after the 6 remaining
audits land. Ordered for dependency + risk:

- **C1. Truth & safety floor (small, do first)**
  - Close workspace by-id authz gaps; strip registry leak from `/workspace/os`.
  - Mark multi-agent/workflow simulation runs with persisted `mode:
    "simulation"`; stop writing fabricated runs into the KG as real provenance.
  - Hash session tokens at rest; real password policy; PKCE on SSO exchange.
- **C2. Brain Core data layer**
  - Workspace-partitioned Knowledge Graph + memory + chat scoping
    (Personal Brain vs Organization Brain become real).
  - Durable workspace state (per-workspace SQLite or locked store); remove
    silent truncation.
  - Memory model: episodic/semantic/experience/decision record types with
    provenance, on the KG substrate.
- **C3. Real Agent Runtime**
  - LLM-backed role runners on top of `core/agent.py` + `llm_router`;
    registry entries become executable (model/prompt/tool allowlist).
  - Per-tool approval gate generalizing the human-in-loop pause.
- **C4. Real Workflow Runtime**
  - Tool/skill nodes execute through `dispatch_tool` under governance with
    pause-for-approval; async runs + cancellation + SSE progress.
  - Trigger foundations: interval/cron + KG-event triggers via hooks.
- **C5. Sovereignty & federation foundations**
  - Per-user/per-workspace brain export (signed bundles, device keypair),
    import with provenance; visibility levels.
- **C6. Identity unification** — user UUIDs, single policy module, invitations.
- **C7. UX/IA re-architecture** — pending frontend-ux audit results.
- **C8. Backend decomposition** — knowledge_graph.py monolith etc., pending
  backend audit results.
- **C9. Release hygiene** — version single-source, root cleanup, lint/typecheck
  story, pending release-quality audit results.

## 8. Planned Phase B Activities

- Synthesize all 8 audits into: Product Review, Identity Review, Architecture
  Review, UX Review, Data Model Review (deliverables 1-5).
- Author **Brain Architecture Proposal**: Brain Core; Memory/Knowledge/
  Relationship/Experience/Decision/Context systems; Agent Runtime; Dynamic
  Workflow Runtime; Personal Brain / Organization Brain / Brain Network /
  Knowledge Exchange / Federation foundations — mapped onto the real existing
  seams (ingestion pipeline, hooks, dispatch_tool, workspace service, KG store).
- Author Implementation Plan with track ownership (disjoint files per track).
- Adversarial review: 2-3 critic agents attack the proposal (feasibility,
  fake-functionality risk, capability-preservation, migration safety); revise.
- Commit both docs.

## 9. Planned Phase C Activities

- Execute queue §7 as sequenced tracks; after each track: run
  `.venv/bin/python -m pytest tests/unit -q` (+ targeted new tests; every new
  feature ships with tests), commit with conventional message, update this file.
- Implementation agents must follow: no placeholder code, no demo data, honest
  labeling, additive migrations with backfill, 3.11-compatible syntax.

## 10. Planned Phase D Activities

- `scripts/validate_release_artifacts.py`, `scripts/lint_v3.mjs`, full pytest,
  `npm`/vsix build as applicable, packaging build.
- Version → 4.0.0 across pyproject.toml/package.json/setup.py/health endpoint
  (verify the single-source mechanism from v3.3.0 audit).
- Update README/ARCHITECTURE/FEATURE_STATUS/CHANGELOG + RELEASE_NOTES_v4.0.0.md.
- Push `feat/v4-digital-brain`; produce final 13-deliverable report; STOP for
  human review (no merge, no tag, no publish).

## 11. Branch Status

- `feat/v4-digital-brain` exists locally, based on main @ 5889195 (v3.6.0).
- Not yet pushed to origin. No implementation commits yet.

## 12. Validation Status

- main baseline: 455 unit pass / 9 pre-existing integration failures
  (ConnectError, need live server). Nothing run on the branch yet beyond this.

## 13. Files Modified (branch vs main)

- `docs/V4_DIGITAL_BRAIN_RECOVERY.md` (this file) — NEW.
- (none else yet)
