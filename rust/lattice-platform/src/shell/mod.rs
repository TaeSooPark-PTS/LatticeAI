//! **Shell** — the bytes a browser gets, and where an old bookmark lands.
//!
//! The only domain in this crate that serves *files* rather than JSON.
//! [`static_ui`] is the SPA shell, the two `StaticFiles` mounts, the manifest,
//! the service worker, the favicon, the invite gate's HMAC cookie and the CPU
//! half of `/local/sysinfo`. [`ui_redirects`] is the map of retired legacy
//! pages — `GET /agents`, `GET /workspace`, `GET /graph` and the rest — to
//! 308s into the SPA's hash router.
//!
//! They belong together because they are the same promise from two directions:
//! **every URL the product ever had still answers, and answers with the app.**
//! Python scattered the redirect helper across a dozen `api/*.py` files;
//! gathering them here is why a redirect can no longer move house without its
//! callers and leave a bookmark on a 404 that nobody notices until a user does.
//!
//! ## What belongs here
//!
//! * A file the browser fetches before any API call: the shell, an asset mount,
//!   the manifest, the worker, an icon.
//! * A header the product's security posture depends on — the CSP, the no-store
//!   trio, `Service-Worker-Allowed`.
//! * A retired page path and the hash route it now means.
//! * The gate a browser passes *before* the shell is served.
//!
//! ## What must never go here
//!
//! * **An SPA path fallback.** The client router is hash-based
//!   (`/app#/knowledge-graph`), so a deep link never arrives as a path. Python
//!   has no `/app/{path}` route; adding one would invent a surface the product
//!   does not have and mask real 404s while doing it.
//! * **`GET /plugins/sdk`.** It looks like a page and is not: it carries its
//!   own `require_user`, so [`crate::toolsurface::plugins`] owns it and
//!   `lattice-host` lists it in `REDIRECTS_OWNED_ELSEWHERE`. Mounting it in
//!   both places panics the router at startup.
//! * **Anything that loads a model.** The GPU half of `/local/sysinfo` comes
//!   from MLX over the worker seam. A static-files module importing an ML
//!   runtime was the Python original's one wart and this port does not inherit
//!   it.
//! * **Rendering.** The shell is vite output served as a plain file — no
//!   templating, no injected values. Serving it is a read.
//!
//! ## Invariants
//!
//! 1. **The contract is headers, and headers were captured rather than
//!    reasoned about.** `scripts/gen_static_fixtures.py` drives the real Python
//!    router and writes `rust/fixtures/http/static_ui.json`;
//!    `tests/static_ui_parity.rs` replays every case here. A header changed by
//!    hand and not in the fixture is a claim with no evidence.
//! 2. **A miss is JSON, not plain text.** `{"detail":"Not Found"}` and
//!    `{"detail":"Method Not Allowed"}` — including inside the mounts, where a
//!    bare file server would send text and break every client that parses the
//!    body.
//! 3. **`HEAD` is not a free synonym for `GET`.** Only `/favicon.ico` declares
//!    it; the rest answer 405, because the Python routes are `@router.get`.
//! 4. **One owner per path.** A page shell lives here *or* in the family whose
//!    Python module declared it — never both. `lattice-host`'s `MOUNT_TABLE`
//!    asserts the union has no duplicates before the router is built, so the
//!    failure is a named assertion instead of a panic in a constructor.

pub mod static_ui;
pub mod ui_redirects;
