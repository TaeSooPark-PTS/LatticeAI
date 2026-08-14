//! Shared replay harness for the R1 workspace families.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
#![allow(dead_code, unused_imports)]
#![allow(clippy::field_reassign_with_default, clippy::unnecessary_sort_by)]

use std::collections::HashMap;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Duration;

use axum::extract::RawQuery;
use axum::http::HeaderMap;
use axum::routing::get;
use axum::Router;
use lattice_auth::{AuthConfig, AuthState, Clock, OrderedMap};
use lattice_platform::invitations::{self, InvitationsState};
use lattice_platform::permissions::{self, PermissionGateway, PermissionsState};
use lattice_platform::ui_redirects;
use lattice_platform::workspace::{
    self, GraphReads, GraphSeam, WorkspaceDeps, WorkspaceProviders, WorkspaceState,
};
use serde_json::{json, Value};

pub const WORKSPACE_FIXTURE: &str = "workspace.json";
pub const ADMIN_FIXTURE: &str = "admin.json";

pub fn load_http(name: &str) -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("fixtures")
        .join("http")
        .join(name);
    let text = std::fs::read_to_string(&path)
        .unwrap_or_else(|error| panic!("read {}: {error}", path.display()));
    serde_json::from_str(&text).expect("fixture is valid JSON")
}

pub fn cases_for(doc: &Value, family: &str) -> Vec<Value> {
    doc["fixtures"]
        .as_array()
        .expect("fixtures")
        .iter()
        .filter(|case| case["family"].as_str() == Some(family))
        .cloned()
        .collect()
}

pub fn openapi_fragment(name: &str) -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("fixtures")
        .join("openapi")
        .join(name);
    serde_json::from_str(&std::fs::read_to_string(&path).expect("fragment")).expect("json")
}

pub fn to_openapi(path: &str) -> String {
    path.split('/')
        .map(
            |seg| match seg.strip_prefix(':').or_else(|| seg.strip_prefix('*')) {
                Some(name) => format!("{{{name}}}"),
                None => seg.to_string(),
            },
        )
        .collect::<Vec<_>>()
        .join("/")
}

pub(crate) struct FixtureGraph {
    windows: AtomicUsize,
    stats_calls: AtomicUsize,
}

impl FixtureGraph {
    fn new() -> Self {
        Self {
            windows: AtomicUsize::new(0),
            stats_calls: AtomicUsize::new(0),
        }
    }
}

impl GraphReads for FixtureGraph {
    fn stats(&self) -> Option<Value> {
        // Two setup snapshots were taken against an empty graph; later reads
        // see the populated fixture stats (workspace_os / indexing).
        if self.stats_calls.fetch_add(1, Ordering::SeqCst) < 2 {
            return Some(json!({
                "db_path": "/tmp/knowledge_graph.sqlite",
                "schema_version": 1,
                "v2_schema_available": true,
                "nodes": {},
                "edges": {},
                "local_sources": 0,
                "local_file_status": {},
                "v2": {
                    "schema_version": 2,
                    "embed_dim": 384,
                    "nodes": 0,
                    "edges": 0,
                    "by_node_type": {},
                    "by_edge_type": {}
                }
            }));
        }
        Some(json!({
            "db_path": "/tmp/knowledge_graph.sqlite",
            "schema_version": 1,
            "v2_schema_available": true,
            "nodes": {
                "Concept": 17, "Conversation": 1, "Memory": 1, "Person": 1, "Workflow": 1
            },
            "edges": {
                "AUTHORED_BY": 1, "HAS_EVENT": 2, "MENTIONS": 5, "TRIGGERED": 2
            },
            "local_sources": 0,
            "local_file_status": {},
            "v2": {
                "schema_version": 2,
                "embed_dim": 384,
                "nodes": 21,
                "edges": 10,
                "by_node_type": {
                    "CONCEPT": 18, "CONVERSATION": 1, "PERSON": 1, "WORKFLOW": 1
                },
                "by_edge_type": {
                    "AUTHORED_BY": 1, "HAS_EVENT": 2, "MENTIONS": 5, "TRIGGERED": 2
                }
            }
        }))
    }

    fn window(&self, _limit: usize) -> Option<Value> {
        // Setup takes two snapshots against an empty graph (the fixture's
        // captured metadata is node_count=0). Later creates see a populated
        // window, matching the capture after the generator ingested a few nodes.
        let seen = self.windows.fetch_add(1, Ordering::SeqCst);
        if seen < 2 {
            return Some(json!({"nodes": [], "edges": []}));
        }
        let nodes: Vec<Value> = (0..19).map(|i| json!({"id": format!("n{i}")})).collect();
        let edges: Vec<Value> = (0..7)
            .map(|i| json!({"from": format!("n{i}"), "to": format!("n{}", i + 1), "type": "MENTIONS"}))
            .collect();
        Some(json!({"nodes": nodes, "edges": edges}))
    }

    fn local_sources(&self) -> Option<Value> {
        Some(json!({"sources": []}))
    }

    fn neighbors(&self, _node_id: &str) -> Option<Value> {
        Some(json!({"neighbors": [], "edges": []}))
    }
}

mod install;
mod match_util;
mod seed;

pub use install::Install;

pub(crate) const SKIP: &[(&str, &str)] = &[
    // Full-app audit/timeline feeds; isolated install cannot reproduce 50–76 events.
    ("workspace_time_machine", "happy"),
    ("workspace_audit_timeline", "happy"),
];

pub struct Answer {
    pub status: u16,
    pub content_type: String,
    pub location: String,
    pub body: String,
}
