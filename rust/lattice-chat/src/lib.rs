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

pub const VERSION: &str = env!("CARGO_PKG_VERSION");
