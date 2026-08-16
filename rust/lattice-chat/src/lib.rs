//! `lattice-chat` — chat orchestration, native (v11.6.0, plan §설계 결정 6).
//!
//! The whole `POST /chat` pipeline runs here: authentication and CSRF
//! (`lattice-auth`), the fast-path intents that answer before a model is
//! consulted, context assembly through the **existing** native engines
//! (`lattice-retrieval` — there is no second retrieval implementation in this
//! crate), the hybrid cloud lane behind the network-boundary dial, and the SSE
//! frames the clients read. Chat history reads come from `lattice-retrieval`'s
//! history lanes; chat history *writes* are native as of Wave 2.5 §W3a — the
//! whole redact → audit → store → ingest chain runs in [`turn`], on R2's audit
//! helper and W1's graph write engine, and `POST /worker/chat/record-turn` has
//! no caller left in this crate.
//!
//! **Token generation never moves.** The Python AI worker remains the only
//! place a model runs; this crate asks it for tokens over
//! `POST /worker/llm/stream` and re-frames them into the product's stream shape.
//!
//! # The SSE contract, which is not negotiable and not negotiated
//!
//! Three clients read `POST /chat` and they disagree about how they ask for a
//! stream (scout_clients.md §2, §7.3):
//!
//! * the SPA sends `Accept: text/event-stream` **and** `stream: true`;
//! * the VS Code extension sends **only** `stream: true` — no `Accept` header
//!   at all, no cookie, no CSRF token;
//! * the Telegram bridge sniffs the response `content-type`.
//!
//! So the body flag decides, `Accept` is never consulted, and an error answer
//! must never carry `text/event-stream` (both SPA stream helpers check the
//! content type and fall back to JSON parsing). `rust/fixtures/http/chat.json`
//! pins all four `stream × Accept` combinations answering the same JSON 400.
//!
//! # What this crate owns
//!
//! | route | note |
//! |---|---|
//! | `POST /chat` | the pipeline |
//! | `GET /history` | reads |
//! | `GET /history/conversations` | grouped reads |
//! | `GET|DELETE /history/conversations/{id:path}` | greedy — mounted `/*conversation_id` |
//! | `DELETE /history` | trim |
//! | `GET /history/search` | keyword search |
//!
//! `GET /chat` (the 308 to `/app#/chat`) is `static_routes.py`'s and belongs to
//! WP-I4's `static_ui` fragment; the `/agent*` operations that share the `chat`
//! OpenAPI fragment are `lattice-agent`'s existing native loop, mounted by the
//! host. `tests/openapi_contract.rs` states both splits and fails if either
//! moves.

#![allow(clippy::result_large_err)]

pub mod boundary;
pub mod cloud;
pub mod contracts;
pub mod documents;
pub mod filegen;
pub mod graph;
pub mod helpers;
pub mod history;
pub mod intents;
pub mod pipeline;
pub mod pyvalue;
pub mod redact;
pub mod routes;
pub mod sse;
pub mod state;
pub mod stream;
pub mod turn;
pub mod worker;

pub use cloud::{CloudProvider, CloudStatus, EgressAudit, OpenAiCompatibleAdapter, ReviewSink};
pub use contracts::ChatRequest;
pub use routes::{router, MOUNTED};
pub use state::{AuditSink, ChatConfig, ChatState};
pub use turn::{write_chat_turn, RecordedTurn};
pub use worker::{ChatWorker, EmbedReply, ModelSnapshot};

/// Product version, kept in lockstep by `scripts/bump_version.py`.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

/// Placeholder retained from the scaffold so the integrator's smoke keeps working.
pub fn crate_ready() -> bool {
    true
}
