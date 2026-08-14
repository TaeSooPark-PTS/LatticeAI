//! Replay `garden` records from `memory_brain.json`.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
mod common;

#[tokio::test]
async fn garden_replays_the_python_oracle() {
    let install = common::brain::Install::start().await;
    install.replay_family("garden").await;
}
