# Lattice AI v3.5.0 — Foundation Stabilization & Verification

**Release type:** stabilization / verification. The last major hardening release
before the platform turns toward Knowledge-Graph-First (v3.6.0) and the Digital
Brain Platform (v4.0). It does **not** add product surface area — it closes
runtime hook bypasses, hardens authentication, splits the largest module, makes
the syntax gate self-maintaining, and removes the last translucent UI surfaces.

Every claim below is backed by automated tests and a live server boot. Nothing
here is "done because the code exists."

Local-first and Enterprise-disabled posture are unchanged.

---

## 1. Security hardening — OIDC + proxy trust

**OIDC ID tokens are now verified, not just decoded.** The SSO callback
previously base64-decoded the `id_token` payload and trusted its `email` claim
with **no signature, issuer, audience, expiry, or nonce check** — a forged token
could log in as anyone. v3.5.0 adds a self-contained verifier
(`latticeai/core/oidc.py`, RSA/JWKS, no new heavy dependency beyond
`cryptography`) that is **fail-closed**:

- RSA signature verified against the provider JWKS (`RS256/384/512`); `alg:none`
  and symmetric `HS*` tokens are rejected outright (the classic bypasses).
- `iss`, `aud` (+ `azp` when multi-audience), `exp`, `iat`/`nbf` validated with
  small clock leeway.
- A per-login **`nonce`** is issued at `/auth/sso/login` and required to match in
  the returned token; `state` is still enforced (CSRF).

**Forwarded headers can no longer spoof the rate-limit key.** `client_ip`
previously trusted `X-Forwarded-For` / `CF-Connecting-IP` unconditionally, so
anyone could rotate the header to reset per-IP login/registration limits.
v3.5.0 honours forwarded headers **only** when the direct peer is a configured
trusted proxy (`LATTICEAI_TRUSTED_PROXIES`, CIDRs allowed); otherwise the peer
address is used. Default is empty (local-first): forwarded headers are ignored.

*Tests:* `tests/unit/test_oidc.py` (15 cases — valid + every rejection path) and
`tests/unit/test_proxy_trust.py` (11 cases, including a rate-limit-bypass proof).

## 2. Runtime hook coverage — closing the bypasses

Real tool/agent executions that skipped the unified `pre_tool → execute →
post_tool` lifecycle now run through `dispatch_tool`:

- HTTP `/tools/read_file`, `/tools/edit_file`, `/tools/grep` — these needed
  keyword args, so they bypassed `_tool_response`. `_tool_response` is now
  kwargs-aware and they route through it.
- `/tools/clear_history` — now wrapped (lifecycle + existing audit event).
- The **computer-use agent loop** (`/cu/agent`) — every step's `execute_tool`
  call, plus the Chrome shortcut and `/cu/status` · `/cu/screenshot`, now fire
  `pre_tool`/`post_tool` (a blocking `pre_tool` returns 403 / a tool_error SSE).
- The **skill-eval** harness (`/agent/eval`) — `execute_tool` is now dispatched.

The full execution-path coverage table is in
[`docs/RUNTIME_HOOK_COVERAGE_v3.5.0.md`](docs/RUNTIME_HOOK_COVERAGE_v3.5.0.md).
Local approval gates and filesystem sandboxing are preserved.

*Tests:* `tests/unit/test_runtime_coverage.py` (computer-use lifecycle + 403 gate
+ kwargs payload) on top of the existing `test_hooks_dispatch.py`.

## 3. `tools.py` → `tools/` package

The 1,525-line `tools.py` is split into a package with focused submodules —
`computer`, `filesystem`, `documents`, `local_files`, `knowledge`, `network`,
`commands` — plus the shared sandbox base, constants, and tool registry in
`tools/__init__.py`. The flat import surface is **100% preserved**: `import
tools` and `from tools import <anything>` behave exactly as before (verified by
46/46 registered tools and the existing `test_tools.py` / `test_tool_registry.py`
suites, including the `AGENT_ROOT` monkeypatch path). No circular imports; the
wheel ships the full package.

## 4. CI — discover-based syntax gate

The hand-maintained `py_compile` enumeration (which still listed the now-removed
`tools.py`) is replaced by `scripts/check_python.py`: it walks the repo, excludes
virtualenv/build/cache/generated/vendored dirs, and compiles everything else —
**144 modules**, auto-including any future file. Wired into both
`.github/workflows/ci.yml` and `npm run check:python`.

## 5. UI — solid, crisp surfaces (glassmorphism removed)

The single translucent surface in the active v3 SPA (the command-palette scrim's
`backdrop-filter: blur`) is removed; 19 further `backdrop-filter: blur` surfaces
in the served legacy `/account` · `/admin` stylesheets are neutralized to solid.
The active v3 CSS now contains **zero** blur surfaces (assets rebuilt + hashed).

---

## Validation (final code state)

- `npm run lint` — **64/64** v3 modules pass.
- `npm run check:python` — **144 modules** compile.
- `pytest tests/unit` — **419 passed** (+29 new: oidc 15, proxy 11 net, runtime 4 — see notes).
- `pytest tests/integration` — **9 passed** against a live server (`/health` → `3.5.0`).
- Playwright `tests/visual/v3.spec.js` — **13 passed** (SPA boots, all views render).
- `python -m build` — sdist + wheel OK; `tools/` package included in the wheel.

## Known limitations (honest)

- OIDC verification supports asymmetric RSA tokens (the OIDC norm). Providers that
  sign ID tokens with EC (`ES*`) or symmetric keys are not supported and are
  rejected fail-closed; add the alg explicitly if such a provider is required.
- The legacy `/account` · `/admin` pages had their blur **removed**, but their
  surfaces are not otherwise restyled (they are not in the v3 SPA view set).
- Memory-service maintenance endpoints (prune/compact/rebuild) are service
  operations with their own audit trail, not registry tools, so they intentionally
  do not fire `pre_tool`/`post_tool` (documented in the coverage table).

## Registry publishing

Build artifacts (npm tarball, PyPI sdist/wheel) are produced. **Publishing to
npm, PyPI, the VS Code Marketplace, Open VSX, Docker Hub, and Vercel is
intentionally NOT done** — the project owner publishes manually.
