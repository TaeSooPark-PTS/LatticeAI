//! Replay `automation_intelligence.py` records from `review_proposals.json`.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
mod review_queue_harness;

use review_queue_harness::Install;

#[tokio::test]
async fn automation_replays_the_python_oracle() {
    let install = Install::start().await;
    install.replay_family("automation_intelligence.py").await;
}
