//! Replay `invitations.py` records from `workspace.json`.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
mod workspace_support;

use workspace_support::{load_http, Install, WORKSPACE_FIXTURE};

#[tokio::test]
async fn invitations_replay_the_python_oracle() {
    let install = Install::start().await;
    let doc = load_http(WORKSPACE_FIXTURE);
    install.replay_family(&doc, "invitations.py").await;
}
