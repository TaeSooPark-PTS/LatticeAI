//! **Knowledge boundary** — how knowledge gets into the graph and out of it,
//! and what is allowed to leave.
//!
//! Four modules, one subject seen from four sides. [`portability`] is the
//! product's promise that the brain is the user's: export, import, backup ZIP,
//! archive inspect / verify / restore, and the Postgres migration path.
//! [`network`] is the same bytes moving between two of the user's own devices,
//! authenticated by Ed25519 over `device_identity.key` rather than by a
//! session. [`network_boundary`] is the dial that decides what may cross at
//! all — per-node sensitivity and the hybrid local/remote policy. [`voice`] is
//! the smallest inbound edge: a memo captured here, transcribed by the worker's
//! `POST /worker/asr`, and kept.
//!
//! The domain exists because those four are the crate's answer to "can this
//! leave the machine?", and answering it in four unrelated places is how a
//! boundary springs a leak.
//!
//! ## What belongs here
//!
//! * A route that moves graph content across a trust boundary — to a file, to
//!   another device, to a remote model.
//! * An archive format, its integrity check, or its restore path.
//! * A sensitivity classification, or the policy that reads one.
//! * A capture surface: bytes in from outside, on their way to the graph.
//!
//! ## What must never go here
//!
//! * **A graph write done directly.** Imports and sensitivity changes call
//!   [`lattice_core::graph_write::GraphWriter`]; the graph has exactly one
//!   writer and this domain is a caller of it, never a second one.
//! * **Retrieval.** Searching the graph is `lattice-retrieval`. This domain
//!   moves the corpus, it does not query it.
//! * **Transcription, embedding, or any other model compute.** [`voice`] stores
//!   the memo and asks the worker for the words. A codec or a model loaded here
//!   would put an ML runtime inside a boundary module — the exact wart the
//!   static-files port refused to inherit.
//! * **A capability probe answered from memory.** v11.8.0 deleted
//!   `GET /api/capture/voice/status` precisely because a probe that no surface
//!   calls drifts from the truth it claims to report.
//!
//! ## Invariants
//!
//! 1. **Fail-closed at the boundary.** Content whose sensitivity cannot be
//!    established does not cross. A verification that cannot complete is a
//!    refusal, not a pass.
//! 2. **A restore is atomic or it did not happen.** A half-applied archive is
//!    worse than a failed one, because the user believes it worked.
//! 3. **Peer identity is a key, not a session.** `POST /network/receive`
//!    authenticates by Ed25519 signature over the same raw 32-byte keys
//!    `lattice_brain.graph.identity` uses. Pairing and push are owner actions;
//!    receiving is not, and must never be widened into one.
//! 4. **On-disk formats are the Python formats.** Every file here is one a live
//!    install already has. Changing a shape is a migration, and there is no
//!    conversion step to hide it in.

pub mod network;
pub mod network_boundary;
pub mod portability;
pub mod voice;
