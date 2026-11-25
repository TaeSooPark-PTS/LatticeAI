use std::collections::BTreeSet;
use std::path::Path;

use serde_json::{Map, Value};

use crate::pyvalue::{text as py_text, truthy};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NetworkMode {
    LocalOnly,
    CloudAllowed,
}

impl NetworkMode {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::LocalOnly => "local_only",
            Self::CloudAllowed => "cloud_allowed",
        }
    }
}

pub const DEFAULT_NETWORK_MODE: NetworkMode = NetworkMode::LocalOnly;

pub const NETWORK_MODE_ENV: &str = "LATTICEAI_NETWORK_MODE";

pub const NETWORK_BOUNDARY_FILE: &str = "network_boundary.json";
pub const HYBRID_POLICY_FILE: &str = "hybrid_policy.json";

pub fn normalize_network_mode(value: &str) -> NetworkMode {
    match value.trim().to_lowercase().as_str() {
        "cloud_allowed" | "cloud" | "cloud-allowed" | "hybrid" | "online" => {
            NetworkMode::CloudAllowed
        }
        _ => DEFAULT_NETWORK_MODE,
    }
}

pub const HARD_BLOCK_NODE_TYPES: [&str; 6] = [
    "ApiKey",
    "Credential",
    "Password",
    "PrivateKey",
    "Secret",
    "Token",
];

pub const HARD_BLOCK_METADATA_FLAGS: [&str; 4] =
    ["do_not_share", "local_only", "private", "sensitive"];

pub fn blocked_reason(node: &Value) -> Option<String> {
    let node_type = node.get("type").and_then(Value::as_str).unwrap_or("");
    if HARD_BLOCK_NODE_TYPES.contains(&node_type) {
        return Some(format!(
            "node type '{node_type}' is blocked from cloud payloads"
        ));
    }
    let meta = node.get("metadata").and_then(Value::as_object)?;
    for flag in HARD_BLOCK_METADATA_FLAGS {
        if meta.get(flag).is_some_and(truthy) {
            return Some(format!(
                "node flagged '{flag}' is blocked from cloud payloads"
            ));
        }
    }
    for uri_key in ["source_uri", "file_path"] {
        if let Some(uri) = meta.get(uri_key).and_then(Value::as_str) {
            let base = uri
                .rsplit(['/', '\\'])
                .next()
                .unwrap_or(uri)
                .trim()
                .to_lowercase();
            let is_template = base.ends_with(".example")
                || base.ends_with(".sample")
                || base.ends_with(".template");
            let is_secret_file = !is_template
                && (base == ".env"
                    || base.starts_with(".env.")
                    || base.ends_with(".pem")
                    || base.ends_with(".key")
                    || base.ends_with(".p12")
                    || base.ends_with(".pfx")
                    || base.starts_with("id_rsa")
                    || base.starts_with("id_ed25519")
                    || base == "credentials.json");
            if is_secret_file {
                return Some(format!(
                    "sensitive source '{base}' is blocked from cloud payloads"
                ));
            }
        }
    }
    None
}

pub fn blocked_reason_for_policy(node: &Value, policy: &HybridPolicy) -> Option<String> {
    if let Some(reason) = blocked_reason(node) {
        return Some(reason);
    }
    let node_type = node.get("type").and_then(Value::as_str).unwrap_or("");
    if !node_type.is_empty() && policy.blocked_node_types.contains(node_type) {
        return Some(format!(
            "node type '{node_type}' is blocked from cloud payloads"
        ));
    }
    let meta = node.get("metadata").and_then(Value::as_object)?;
    for flag in &policy.blocked_metadata_flags {
        if meta.get(flag.as_str()).is_some_and(truthy) {
            return Some(format!(
                "node flagged '{flag}' is blocked from cloud payloads"
            ));
        }
    }
    None
}

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

pub fn env_default_mode() -> NetworkMode {
    match std::env::var(NETWORK_MODE_ENV) {
        Ok(value) => normalize_network_mode(&value),
        Err(_) => DEFAULT_NETWORK_MODE,
    }
}

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

#[derive(Debug, Clone, PartialEq)]
pub struct HybridPolicy {
    pub blocked_node_types: BTreeSet<String>,
    pub blocked_metadata_flags: BTreeSet<String>,
    pub auto_commit: bool,
    pub allow_multimodal: bool,
    pub min_extraction_confidence: f64,
    pub escalation: Escalation,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Escalation {
    Always,
    Auto,
    Manual,
}

impl Escalation {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Always => "always",
            Self::Auto => "auto",
            Self::Manual => "manual",
        }
    }

    pub fn parse(value: &str) -> Self {
        match value.trim().to_lowercase().as_str() {
            "always" | "all" | "every" => Self::Always,
            "manual" | "explicit" => Self::Manual,
            _ => Self::Auto,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EscalationReason {
    Always,
    NoLocalModel,
    WeakLocalContext,
    ExplicitRequest,
}

impl EscalationReason {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Always => "always",
            Self::NoLocalModel => "no_local_model",
            Self::WeakLocalContext => "weak_local_context",
            Self::ExplicitRequest => "explicit_request",
        }
    }
}

pub const WEAK_CONTEXT_NODE_THRESHOLD: i64 = 2;

pub fn is_explicit_cloud_request(message: &str) -> bool {
    let trimmed = message.trim_start();
    trimmed == "/cloud"
        || trimmed.starts_with("/cloud ")
        || trimmed.starts_with("/cloud\t")
        || trimmed.starts_with("/cloud\n")
        || trimmed.starts_with("클라우드:")
}

pub fn strip_cloud_prefix(message: &str) -> &str {
    let trimmed = message.trim_start();
    for prefix in ["/cloud ", "/cloud\t", "/cloud\n", "클라우드:"] {
        if let Some(rest) = trimmed.strip_prefix(prefix) {
            return rest.trim_start();
        }
    }
    if trimmed == "/cloud" {
        return "";
    }
    message
}

pub fn escalation_reason(
    escalation: Escalation,
    no_local_model: bool,
    matched_nodes: i64,
    message: &str,
) -> Option<EscalationReason> {
    let explicit = is_explicit_cloud_request(message);
    match escalation {
        Escalation::Always => Some(if explicit {
            EscalationReason::ExplicitRequest
        } else if no_local_model {
            EscalationReason::NoLocalModel
        } else {
            EscalationReason::Always
        }),
        Escalation::Auto => {
            if explicit {
                Some(EscalationReason::ExplicitRequest)
            } else if no_local_model {
                Some(EscalationReason::NoLocalModel)
            } else if matched_nodes < WEAK_CONTEXT_NODE_THRESHOLD {
                Some(EscalationReason::WeakLocalContext)
            } else {
                None
            }
        }
        Escalation::Manual => explicit.then_some(EscalationReason::ExplicitRequest),
    }
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
            escalation: Escalation::Auto,
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
        min_extraction_confidence: match policy.get("min_extraction_confidence") {
            Some(Value::Number(number)) => number.as_f64().unwrap_or(0.55),
            Some(Value::String(text)) => text.trim().parse::<f64>().unwrap_or(0.55),
            _ => 0.55,
        },
        escalation: policy
            .get("escalation")
            .and_then(Value::as_str)
            .map(Escalation::parse)
            .or_else(|| {
                data.get("escalation")
                    .and_then(Value::as_str)
                    .map(Escalation::parse)
            })
            .unwrap_or(Escalation::Auto),
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
        let expected = match std::env::var(NETWORK_MODE_ENV) {
            Ok(value) => normalize_network_mode(&value),
            Err(_) => NetworkMode::LocalOnly,
        };
        assert_eq!(env_default_mode(), expected);
    }

    #[test]
    fn escalation_defaults_to_auto_and_parses_every_token() {
        assert_eq!(Escalation::parse(""), Escalation::Auto);
        assert_eq!(Escalation::parse("AUTO"), Escalation::Auto);
        assert_eq!(Escalation::parse("always"), Escalation::Always);
        assert_eq!(Escalation::parse("manual"), Escalation::Manual);
        let dir = tempfile::tempdir().unwrap();
        assert_eq!(
            resolve_hybrid_policy(dir.path(), None, None).escalation,
            Escalation::Auto
        );
        let dir = dir_with(
            HYBRID_POLICY_FILE,
            json!({"default": {"escalation": "manual"}}),
        );
        assert_eq!(
            resolve_hybrid_policy(dir.path(), None, None).escalation,
            Escalation::Manual
        );
        let dir = dir_with(HYBRID_POLICY_FILE, json!({"escalation": "always"}));
        assert_eq!(
            resolve_hybrid_policy(dir.path(), None, None).escalation,
            Escalation::Always
        );
    }

    #[test]
    fn auto_escalates_only_when_the_local_lane_is_thin() {
        assert_eq!(
            escalation_reason(Escalation::Auto, true, 10, "hello"),
            Some(EscalationReason::NoLocalModel)
        );
        assert_eq!(
            escalation_reason(Escalation::Auto, false, 0, "hello"),
            Some(EscalationReason::WeakLocalContext)
        );
        assert_eq!(
            escalation_reason(Escalation::Auto, false, 1, "hello"),
            Some(EscalationReason::WeakLocalContext)
        );
        assert_eq!(escalation_reason(Escalation::Auto, false, 2, "hello"), None);
        assert_eq!(
            escalation_reason(Escalation::Auto, false, 8, "/cloud 오늘의 상식"),
            Some(EscalationReason::ExplicitRequest)
        );
        assert_eq!(
            escalation_reason(Escalation::Auto, false, 8, "클라우드: 한 줄"),
            Some(EscalationReason::ExplicitRequest)
        );
    }

    #[test]
    fn manual_is_prefix_only_and_always_fires_every_turn() {
        assert_eq!(
            escalation_reason(Escalation::Manual, true, 0, "hello"),
            None
        );
        assert_eq!(
            escalation_reason(Escalation::Manual, false, 8, "/cloud x"),
            Some(EscalationReason::ExplicitRequest)
        );
        assert_eq!(
            escalation_reason(Escalation::Always, false, 8, "hello"),
            Some(EscalationReason::Always)
        );
        assert_eq!(
            escalation_reason(Escalation::Always, true, 8, "hello"),
            Some(EscalationReason::NoLocalModel)
        );
    }

    #[test]
    fn the_cloud_prefix_is_stripped_from_the_question() {
        assert_eq!(strip_cloud_prefix("/cloud 오늘의 상식"), "오늘의 상식");
        assert_eq!(strip_cloud_prefix("클라우드: 한 줄"), "한 줄");
        assert_eq!(strip_cloud_prefix("그냥 질문"), "그냥 질문");
        assert!(is_explicit_cloud_request("/cloud"));
        assert!(!is_explicit_cloud_request("/cloudy"));
    }

    #[test]
    fn policy_blocked_types_and_flags_are_enforced_alongside_hard_blocks() {
        let dir = tempfile::tempdir().expect("tempdir");
        std::fs::write(
            dir.path().join(HYBRID_POLICY_FILE),
            serde_json::to_string(&json!({
                "default": {
                    "blocked_node_types": ["Journal"],
                    "blocked_metadata_flags": ["confidential"]
                }
            }))
            .expect("json"),
        )
        .expect("write");
        let policy = resolve_hybrid_policy(dir.path(), None, None);
        assert_eq!(
            blocked_reason_for_policy(&json!({"type": "Journal"}), &policy).as_deref(),
            Some("node type 'Journal' is blocked from cloud payloads")
        );
        assert_eq!(
            blocked_reason_for_policy(
                &json!({"type": "Note", "metadata": {"confidential": true}}),
                &policy
            )
            .as_deref(),
            Some("node flagged 'confidential' is blocked from cloud payloads")
        );
        assert_eq!(
            blocked_reason_for_policy(&json!({"type": "Decision"}), &policy),
            None
        );
    }
}
