//! Replay `permissions.py` records from `admin.json`.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
mod workspace_support;

use workspace_support::{load_http, Install, ADMIN_FIXTURE};

#[tokio::test]
async fn permissions_replay_the_python_oracle() {
    let install = Install::start().await;
    let doc = load_http(ADMIN_FIXTURE);
    install.replay_family(&doc, "permissions.py").await;
}
