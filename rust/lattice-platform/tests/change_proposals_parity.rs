//! Replay `change_proposals.py` records from `review_proposals.json`.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
mod review_queue_harness;

use review_queue_harness::Install;

#[tokio::test]
async fn change_proposals_replays_the_python_oracle() {
    let install = Install::start().await;
    install.replay_family("change_proposals.py").await;
}
