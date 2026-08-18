//! **Model ops** — which model runs on this machine, and getting the machine
//! ready to run it.
//!
//! Two modules that only make sense together. [`models_catalog`] answers *what
//! is available and what suits this host*: the catalog, the recommendations,
//! `GET /engines`, and the stacked `/mode` + `/runtime_features` handler, over
//! a [`models_catalog::HostProbe`] that reports what the hardware actually is.
//! [`setup`] answers *get me from nothing to working*: the first-run
//! environment scan, the SSE install stream, the demo corpus a new user has
//! something to look at, and the auth / permission links onboarding hands out.
//! [`setup`] reads the catalog's probe and recommendations directly — the scan
//! and the recommendation are the same question asked at two moments.
//!
//! ## What belongs here
//!
//! * A fact about this host: GPU, RAM, WSL, installed tooling, static assets
//!   present.
//! * A model's catalog entry, or the rule that ranks one above another for a
//!   given probe result.
//! * A step in first-run onboarding, its plan, or its verification check.
//! * The demo corpus: its documents, its suggested questions, and its removal.
//!
//! ## What must never go here
//!
//! * **Model *execution*.** `GET /models`, `POST /models/load`,
//!   `DELETE /models/unload/{id}` and the two `prepare-model` routes are
//!   KEEP_WORKER: they touch the in-process MLX runtime, which lives in the
//!   Python interpreter that holds it. This domain describes and recommends; it
//!   never loads.
//! * **`GET /status`'s neighbours.** Reporting the *loaded* model is worker
//!   state, and lives with the models family precisely so a static-file module
//!   never has to import an ML runtime to answer it.
//! * **A graph write done directly.** The demo corpus deletes through
//!   `GraphWriter::delete_document_tree`, like every other writer in the crate.
//! * **A second host probe.** One [`models_catalog::HostProbe`] is the machine's
//!   description; a module that re-derives "do we have a GPU" will eventually
//!   disagree with the one the recommendation used.
//!
//! ## Invariants
//!
//! 1. **`/setup/scan` is client-critical.** It is the first request a new
//!    install makes and the one that decides what the user is shown. It must
//!    answer — degraded, honest and shaped — rather than fail.
//! 2. **`/setup/install` is SSE with no Accept negotiation.** The Python route
//!    never negotiated, the client never sends `Accept`, and adding
//!    negotiation would break every existing installer mid-stream.
//! 3. **Demo content is labelled at the source.** Everything the demo corpus
//!    writes carries [`setup::DEMO_URI_PREFIX`] and
//!    [`setup::DEMO_METADATA_FLAG`], which is what makes removal exact instead
//!    of a heuristic sweep over the user's real documents.
//! 4. **A recommendation is derived, never stored.** It is a function of the
//!    probe and the catalog, computed per request, so a hardware change or a
//!    catalog update is reflected immediately and no stale advice survives.

pub mod models_catalog;
pub mod setup;
