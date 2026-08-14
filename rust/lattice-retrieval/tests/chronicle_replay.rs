//! Replay `chronicle` records from `memory_brain.json`.
//!
//! Day / as-of routes substitute `@today` with the replayer's own date
//! (frozen-clock fixtures pin order, not the calendar day).

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
mod common;

#[tokio::test]
async fn chronicle_replays_the_python_oracle() {
    let install = common::brain::Install::start().await;
    install.replay_family("chronicle").await;
}
