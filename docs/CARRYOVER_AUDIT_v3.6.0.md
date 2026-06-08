# v3.5.0 → v3.6.0 Carry-Over Audit

**Date:** 2026-06-09
**Baseline:** v3.5.0 (tag published, GitHub Release live, CI + Visual Smoke green on `main`)
**Purpose:** Classify every open v3.5.0 carry-over risk as **blocking**, **non-blocking**, or
**obsolete** before starting v3.6.0 Knowledge Graph First work.

## Headline

**No carry-over item blocks v3.6.0.** v3.5.0 was a stabilization/verification release that added no
product surface. Every documented limitation is either an intentional, honestly-labeled scope
boundary or a closed issue. KG work can start immediately on the existing local SQLite store.

## Verified baseline state

| Check | Result | Evidence |
|---|---|---|
| `v3.5.0` git tag | exists | `git tag` |
| GitHub Release `v3.5.0` | published, not draft | `gh release view v3.5.0` |
| CI on `main` | success | `gh run list --branch main` (run 27155690240) |
| Visual Smoke on `main` | success | run 27155690270 |
| VSIX reproducibility fix | merged | commit `78deb95` |

## Classified carry-over items

### Blocking
*(none)*

### Non-blocking

| Item | Why it does not block | Evidence |
|---|---|---|
| OIDC is RSA-only (RS256/384/512); ES*/HS*/`alg:none` rejected fail-closed | v3.6.0 KG work does not touch the SSO callback. EC support is additive when a provider needs it. | `latticeai/core/oidc.py:36` |
| Memory/KG maintenance endpoints (`/api/memory/{prune,compact,rebuild,clear}`) sit outside `pre_tool`/`post_tool` | Intentional, documented decision; these ops carry their own audit events. v3.6.0 follows the same convention and adds a coverage row rather than routing maintenance through `dispatch_tool`. | `docs/RUNTIME_HOOK_COVERAGE_v3.5.0.md:47` |
| Knowledge Graph is config-dependent on `LATTICEAI_ENABLE_GRAPH`, backed by a large `knowledge_graph.py` | This is the surface v3.6.0 builds on. Size/coupling is refactor context, addressed additively (new service modules, not a rewrite). | `FEATURE_STATUS.md:257-263` |
| Memory project/graph/vector tiers PARTIAL; prune/clear API-only (no UI) | KG-adjacent tiers v3.6.0 completes; honestly labeled, not broken. v3.6.0 adds the ingestion/export/provenance UI. | `FEATURE_STATUS.md:280-286` |
| Hybrid-search "fusion" explainer renders illustrative bars | Cosmetic placeholder; orthogonal to KG-first ingestion work. | `FEATURE_STATUS.md:249-251` |
| Chat grounding chips set state but `ChatRequest` drops them | Wiring grounding into generation is a natural KG-first feature; additive, not a blocker. | `FEATURE_STATUS.md:131-138` |
| CI action versions inconsistent (`ci.yml` `checkout@v4` vs `release.yml` `@v5`) | None are currently deprecated. Aligning is hygiene; addressed opportunistically. | `.github/workflows/ci.yml` |
| Live MCP tool calls + VLM inference PARTIAL (env/model dependent) | Honestly badged; orthogonal to KG-first work. | `FEATURE_STATUS.md:169-178,392` |

### Obsolete

| Item | Why it is closed |
|---|---|
| "Hooks registered but not executing" (v3.3.0 issue) | v3.4.1 added real runners for all built-ins; v3.5.0 closed the last tool-path bypasses at 100% coverage. |
| Legacy `/account` `/admin` glassmorphism | Blur removed in v3.5.0 (`account.css:120`, `admin.css:48`). Remaining "not restyled" note is a deliberate scope boundary — these pages are outside the v3 SPA view set. |
| Vercel deployment returns HTTP 500 | Settled posture: Vercel is **landing/download/demo only, never runtime**. Lattice AI is local-first; the KG runs on local SQLite. Do not present any Vercel URL as a product surface. |
| CI syntax-gate staleness | Fixed in v3.5.0 by `scripts/check_python.py` (discover-based via `rglob`); new v3.6.0 modules are covered automatically with zero maintenance. |

## v3.6.0 posture decisions taken from this audit

1. **Build additively on the existing store.** Mirror the proven `tools.py → tools/` decomposition
   pattern: preserve the import surface, add focused modules (`services/ingestion.py`,
   `services/kg_portability.py`, `api/browser.py`, `api/portability.py`) rather than rewriting
   `knowledge_graph.py`.
2. **Route new ingestion paths through `dispatch_tool`** so `pre_tool`/`post_tool` fire — this is the
   one v3.5.0 gap (no ingest path fired hooks). Maintenance ops stay audit-only by the documented
   convention.
3. **Keep Vercel landing-only and OIDC RSA-only** — settled, out of v3.6.0 scope.
4. **Leave legacy `/account` `/admin` pages alone** — out of the local-first KG scope.
