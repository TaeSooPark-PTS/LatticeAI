//! The dial that decides whether a turn may reach a cloud provider.
//!
//! Port of `latticeai/core/network_boundary.py` plus the **read** half of
//! `services/network_boundary_service.py` and `services/hybrid_policy.py`
//! (`services/mode_store.py` is their shared storage). Chat only ever reads
//! these files; `PATCH /api/network/boundary` and the policy writer are
//! `lattice-platform`'s routes (WP-R9), and two writers of one JSON file is
//! exactly the failure the shared store exists to prevent.
//!
//! Precedence is the Python one and it is **not** the obvious one: the
//! *workspace* entry wins over the *user* entry, which wins over `default`.
//! A file that cannot be read degrades to defaults rather than to permission —
//! `local_only`, no auto-commit — because an unreadable policy is not consent.

use std::collections::BTreeSet;
use std::path::Path;

use serde_json::{Map, Value};

use crate::pyvalue::{text as py_text, truthy};

/// `NetworkBoundaryMode`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NetworkMode {
    /// Nothing leaves this machine.
    LocalOnly,
    /// Minimal related nodes may be sent to a cloud LLM.
    CloudAllowed,
}

impl NetworkMode {
    /// The wire value (`mode`, `X-Network-Mode`).
    pub fn as_str(self) -> &'static str {
        match self {
            Self::LocalOnly => "local_only",
            Self::CloudAllowed => "cloud_allowed",
        }
    }
}

/// `DEFAULT_NETWORK_MODE`.
pub const DEFAULT_NETWORK_MODE: NetworkMode = NetworkMode::LocalOnly;

/// The env var that moves the process default.
pub const NETWORK_MODE_ENV: &str = "LATTICEAI_NETWORK_MODE";

/// `network_boundary.json` — the dial's file, under the data dir.
pub const NETWORK_BOUNDARY_FILE: &str = "network_boundary.json";
/// `hybrid_policy.json` — what may happen to what comes back.
pub const HYBRID_POLICY_FILE: &str = "hybrid_policy.json";

/// `normalize_network_mode` — every alias, unknown input falling to local_only.
pub fn normalize_network_mode(value: &str) -> NetworkMode {
    match value.trim().to_lowercase().as_str() {
        "cloud_allowed" | "cloud" | "cloud-allowed" | "hybrid" | "online" => {
            NetworkMode::CloudAllowed
        }
        _ => DEFAULT_NETWORK_MODE,
    }
}

/// `HARD_BLOCK_NODE_TYPES` — the circuit breaker no policy can widen.
pub const HARD_BLOCK_NODE_TYPES: [&str; 6] = [
    "ApiKey",
    "Credential",
    "Password",
    "PrivateKey",
    "Secret",
    "Token",
];

/// `HARD_BLOCK_METADATA_FLAGS` — truthy on a node means it never leaves.
pub const HARD_BLOCK_METADATA_FLAGS: [&str; 4] =
    ["do_not_share", "local_only", "private", "sensitive"];

/// `is_node_blocked_for_cloud` — the reason, or `None` when it may go.
pub fn blocked_reason(node: &Value) -> Option<String> {
    let node_type = node.get("type").and_then(Value::as_str).unwrap_or("");
    if HARD_BLOCK_NODE_TYPES.contains(&node_type) {
        return Some(format!(
            "node type '{node_type}' is blocked from cloud payloads"
        ));
    }
    let meta = node.get("metadata").and_then(Value::as_object)?;
    // Python iterates a frozenset, whose order is unspecified; the sorted array
    // here makes the *reported* flag deterministic when a node carries two.
    for flag in HARD_BLOCK_METADATA_FLAGS {
        if meta.get(flag).is_some_and(truthy) {
            return Some(format!(
                "node flagged '{flag}' is blocked from cloud payloads"
            ));
        }
    }
    None
}

/// `mode_store._read` — the three buckets, defaults on any unreadable file.
fn read_store(path: &Path) -> Map<String, Value> {
    let mut data = std::fs::read_to_string(path)
        .ok()
        .and_then(|text| serde_json::from_str::<Value>(&text).ok())
        .and_then(|value| value.as_object().cloned())
        .unwrap_or_default();
    for key in ["users", "workspaces"] {
        data.entry(key.to_string())
            .or_insert_with(|| Value::Object(Map::new()));
    }
    data
}

fn scoped<'a>(
    data: &'a Map<String, Value>,
    bucket: &str,
    key: Option<&str>,
    lower: bool,
) -> Option<&'a Value> {
    let key = key.filter(|value| !value.is_empty())?;
    let key = if lower {
        key.to_lowercase()
    } else {
        key.to_string()
    };
    data.get(bucket)
        .and_then(Value::as_object)
        .and_then(|entries| entries.get(&key))
        .filter(|entry| truthy(entry))
}

/// `NetworkBoundaryService.resolve` over the file at `data_dir`.
///
/// `env_default` is `_env_default_mode()`: the process default before any file
/// says otherwise.
pub fn resolve_network_mode(
    data_dir: &Path,
    env_default: NetworkMode,
    user_email: Option<&str>,
    workspace_id: Option<&str>,
) -> NetworkMode {
    let data = read_store(&data_dir.join(NETWORK_BOUNDARY_FILE));
    let as_mode = |value: &Value| normalize_network_mode(&py_text(value));
    if let Some(entry) = scoped(&data, "workspaces", workspace_id, false) {
        return as_mode(entry);
    }
    if let Some(entry) = scoped(&data, "users", user_email, true) {
        return as_mode(entry);
    }
    match data.get("default").filter(|value| truthy(value)) {
        Some(entry) => as_mode(entry),
        None => env_default,
    }
}

/// `_env_default_mode()`.
pub fn env_default_mode() -> NetworkMode {
    match std::env::var(NETWORK_MODE_ENV) {
        Ok(value) => normalize_network_mode(&value),
        Err(_) => DEFAULT_NETWORK_MODE,
    }
}

/// `chat_hybrid.resolve_request_network_mode` — the per-request override wins.
pub fn request_network_mode(
    request_mode: Option<&str>,
    data_dir: &Path,
    user_email: Option<&str>,
    workspace_id: Option<&str>,
) -> NetworkMode {
    match request_mode.filter(|mode| !mode.is_empty()) {
        Some(mode) => normalize_network_mode(mode),
        None => resolve_network_mode(data_dir, env_default_mode(), user_email, workspace_id),
    }
}

/// The resolved hybrid policy — what may happen to what the cloud returns.
#[derive(Debug, Clone, PartialEq)]
pub struct HybridPolicy {
    /// Node types this scope refuses to send, hard blocks unioned in.
    pub blocked_node_types: BTreeSet<String>,
    /// Metadata flags this scope refuses to send, hard blocks unioned in.
    pub blocked_metadata_flags: BTreeSet<String>,
    /// Whether cloud-derived knowledge may be written instead of staged.
    pub auto_commit: bool,
    /// Whether multimodal cloud calls are allowed when cloud mode is on.
    pub allow_multimodal: bool,
    /// Confidence floor for heuristic extraction.
    pub min_extraction_confidence: f64,
}

impl Default for HybridPolicy {
    fn default() -> Self {
        Self {
            blocked_node_types: HARD_BLOCK_NODE_TYPES
                .iter()
                .map(|t| t.to_string())
                .collect(),
            blocked_metadata_flags: HARD_BLOCK_METADATA_FLAGS
                .iter()
                .map(|f| f.to_string())
                .collect(),
            auto_commit: false,
            allow_multimodal: false,
            min_extraction_confidence: 0.55,
        }
    }
}

fn merge_policy(policy: &mut Map<String, Value>, patch: Option<&Value>) {
    if let Some(Value::Object(entries)) = patch {
        for (key, value) in entries {
            policy.insert(key.clone(), value.clone());
        }
    }
}

fn string_set(value: Option<&Value>, hard: &[&str]) -> BTreeSet<String> {
    let mut set: BTreeSet<String> = value
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(|item| item.as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default();
    set.extend(hard.iter().map(|entry| entry.to_string()));
    set
}

/// `HybridPolicyService.resolve` — default, then user, then workspace.
pub fn resolve_hybrid_policy(
    data_dir: &Path,
    user_email: Option<&str>,
    workspace_id: Option<&str>,
) -> HybridPolicy {
    let data = read_store(&data_dir.join(HYBRID_POLICY_FILE));
    let mut policy: Map<String, Value> = data
        .get("default")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    merge_policy(&mut policy, scoped(&data, "users", user_email, true));
    merge_policy(
        &mut policy,
        scoped(&data, "workspaces", workspace_id, false),
    );
    HybridPolicy {
        blocked_node_types: string_set(policy.get("blocked_node_types"), &HARD_BLOCK_NODE_TYPES),
        blocked_metadata_flags: string_set(
            policy.get("blocked_metadata_flags"),
            &HARD_BLOCK_METADATA_FLAGS,
        ),
        auto_commit: policy.get("auto_commit").is_some_and(truthy),
        allow_multimodal: policy.get("allow_multimodal").is_some_and(truthy),
        // `float(...)` with a TypeError/ValueError fallback: an unusable value
        // is the default, never a refusal.
        min_extraction_confidence: match policy.get("min_extraction_confidence") {
            Some(Value::Number(number)) => number.as_f64().unwrap_or(0.55),
            Some(Value::String(text)) => text.trim().parse::<f64>().unwrap_or(0.55),
            _ => 0.55,
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn dir_with(name: &str, payload: Value) -> tempfile::TempDir {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join(name), payload.to_string()).unwrap();
        dir
    }

    #[test]
    fn every_alias_normalises_and_the_unknown_falls_closed() {
        for cloud in [
            "cloud_allowed",
            "cloud",
            "Cloud-Allowed",
            " hybrid ",
            "online",
        ] {
            assert_eq!(normalize_network_mode(cloud), NetworkMode::CloudAllowed);
        }
        for local in ["local_only", "local", "offline", "", "nonsense"] {
            assert_eq!(normalize_network_mode(local), NetworkMode::LocalOnly);
        }
        assert_eq!(NetworkMode::CloudAllowed.as_str(), "cloud_allowed");
        assert_eq!(NetworkMode::LocalOnly.as_str(), "local_only");
    }

    #[test]
    fn the_workspace_entry_wins_over_the_user_entry() {
        let dir = dir_with(
            NETWORK_BOUNDARY_FILE,
            json!({
                "default": "local_only",
                "users": {"owner@x": "cloud_allowed"},
                "workspaces": {"team": "local_only"},
            }),
        );
        let path = dir.path();
        assert_eq!(
            resolve_network_mode(path, NetworkMode::LocalOnly, Some("Owner@X"), None),
            NetworkMode::CloudAllowed,
            "the user key is lower-cased"
        );
        assert_eq!(
            resolve_network_mode(path, NetworkMode::LocalOnly, Some("owner@x"), Some("team")),
            NetworkMode::LocalOnly,
            "the workspace entry wins"
        );
        assert_eq!(
            resolve_network_mode(path, NetworkMode::CloudAllowed, None, None),
            NetworkMode::LocalOnly,
            "the file's default beats the env default"
        );
    }

    #[test]
    fn an_absent_or_corrupt_file_uses_the_env_default() {
        let dir = tempfile::tempdir().unwrap();
        assert_eq!(
            resolve_network_mode(dir.path(), NetworkMode::CloudAllowed, None, None),
            NetworkMode::CloudAllowed
        );
        std::fs::write(dir.path().join(NETWORK_BOUNDARY_FILE), "{ truncated").unwrap();
        assert_eq!(
            resolve_network_mode(dir.path(), NetworkMode::CloudAllowed, None, None),
            NetworkMode::CloudAllowed
        );
        std::fs::write(dir.path().join(NETWORK_BOUNDARY_FILE), "[]").unwrap();
        assert_eq!(
            resolve_network_mode(dir.path(), NetworkMode::LocalOnly, None, None),
            NetworkMode::LocalOnly
        );
    }

    #[test]
    fn a_request_override_wins_over_the_persisted_dial() {
        let dir = dir_with(NETWORK_BOUNDARY_FILE, json!({"default": "cloud_allowed"}));
        assert_eq!(
            request_network_mode(Some("local_only"), dir.path(), None, None),
            NetworkMode::LocalOnly
        );
        assert_eq!(
            request_network_mode(Some(""), dir.path(), None, None),
            NetworkMode::CloudAllowed,
            "an empty override is no override"
        );
        assert_eq!(
            request_network_mode(None, dir.path(), None, None),
            NetworkMode::CloudAllowed
        );
    }

    #[test]
    fn hard_blocks_fire_on_type_and_on_a_truthy_flag() {
        assert_eq!(
            blocked_reason(&json!({"type": "Secret"})).as_deref(),
            Some("node type 'Secret' is blocked from cloud payloads")
        );
        assert_eq!(
            blocked_reason(&json!({"type": "Note", "metadata": {"sensitive": true}})).as_deref(),
            Some("node flagged 'sensitive' is blocked from cloud payloads")
        );
        assert_eq!(
            blocked_reason(&json!({"type": "Note", "metadata": {"sensitive": false}})),
            None
        );
        assert_eq!(blocked_reason(&json!({"type": "Note"})), None);
        assert_eq!(blocked_reason(&json!({"metadata": "not an object"})), None);
    }

    #[test]
    fn the_policy_merges_by_scope_and_always_unions_the_hard_blocks() {
        let dir = dir_with(
            HYBRID_POLICY_FILE,
            json!({
                "default": {"auto_commit": false, "blocked_node_types": ["Journal"]},
                "users": {"owner@x": {"auto_commit": true, "min_extraction_confidence": "0.9"}},
                "workspaces": {"team": {"auto_commit": false, "allow_multimodal": true}},
            }),
        );
        let user = resolve_hybrid_policy(dir.path(), Some("owner@x"), None);
        assert!(user.auto_commit);
        assert!((user.min_extraction_confidence - 0.9).abs() < f64::EPSILON);
        assert!(user.blocked_node_types.contains("Journal"));
        assert!(
            user.blocked_node_types.contains("Secret"),
            "hard blocks union in"
        );
        assert!(user.blocked_metadata_flags.contains("do_not_share"));

        let team = resolve_hybrid_policy(dir.path(), Some("owner@x"), Some("team"));
        assert!(!team.auto_commit, "the workspace patch wins");
        assert!(team.allow_multimodal);
    }

    #[test]
    fn an_unreadable_policy_is_the_default_rather_than_permission() {
        let dir = tempfile::tempdir().unwrap();
        assert_eq!(
            resolve_hybrid_policy(dir.path(), None, None),
            HybridPolicy::default()
        );
        let dir = dir_with(
            HYBRID_POLICY_FILE,
            json!({"default": {"min_extraction_confidence": "x"}}),
        );
        let policy = resolve_hybrid_policy(dir.path(), None, None);
        assert!((policy.min_extraction_confidence - 0.55).abs() < f64::EPSILON);
        assert!(!policy.auto_commit);
        assert!(format!("{policy:?}").contains("auto_commit"));
    }

    #[test]
    fn the_env_default_is_read_when_no_file_says_otherwise() {
        // Reading the process env, not setting it: a set() here would race
        // every other test in this binary.
        let expected = match std::env::var(NETWORK_MODE_ENV) {
            Ok(value) => normalize_network_mode(&value),
            Err(_) => NetworkMode::LocalOnly,
        };
        assert_eq!(env_default_mode(), expected);
    }
}
