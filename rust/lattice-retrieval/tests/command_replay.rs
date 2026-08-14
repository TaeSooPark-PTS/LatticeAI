//! Replay `command_center` records from `memory_brain.json`.
//!
//! Pins the known oracle bug: `/api/command/search`'s knowledge group is
//! always empty because the service reads `payload['results']` while
//! `keyword_search` returns `matches`.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
mod common;

#[tokio::test]
async fn command_replays_the_python_oracle() {
    let install = common::brain::Install::start().await;
    install.replay_family("command_center").await;
}
