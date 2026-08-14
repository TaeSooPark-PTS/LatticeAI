//! Replay `brain_intelligence` records from `memory_brain.json`.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
mod common;

#[tokio::test]
async fn brain_replays_the_python_oracle() {
    let install = common::brain::Install::start().await;
    install.replay_family("brain_intelligence").await;
}
