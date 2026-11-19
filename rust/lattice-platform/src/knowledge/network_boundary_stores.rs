//! Mode catalog, `BoundaryStore`, `PolicyStore`, and `TokenBudget` for [`super`].

use std::path::PathBuf;

use lattice_auth::OrderedMap;
use serde_json::{json, Value};

use super::{HARD_BLOCK_METADATA_FLAGS, HARD_BLOCK_NODE_TYPES};

pub(super) fn network_mode_catalog() -> Vec<OrderedMap> {
    let mut local = OrderedMap::new();
    local.insert("id", json!("local_only"));
    local.insert("label", json!("Local only"));
    local.insert("label_ko", json!("로컬만"));
    local.insert(
        "summary",
        json!("Nothing leaves this machine. Answers use local models and the local Brain only."),
    );
    local.insert(
        "summary_ko",
        json!("이 컴퓨터를 벗어나지 않습니다. 로컬 모델과 로컬 Brain만 사용합니다."),
    );
    local.insert("risk", json!("low"));
    local.insert("requires_ack", json!(false));

    let mut cloud = OrderedMap::new();
    cloud.insert("id", json!("cloud_allowed"));
    cloud.insert("label", json!("Cloud streaming allowed"));
    cloud.insert("label_ko", json!("클라우드 스트리밍 허용"));
    cloud.insert(
        "summary",
        json!("Minimal related Knowledge Graph nodes may be sent to a cloud LLM. The streamed answer is written back into the local Brain with provenance."),
    );
    cloud.insert(
        "summary_ko",
        json!("관련된 최소 Knowledge Graph 노드만 클라우드 LLM으로 전송될 수 있습니다. 스트리밍 답변은 provenance와 함께 로컬 Brain에 다시 기록됩니다."),
    );
    cloud.insert("risk", json!("medium"));
    cloud.insert("requires_ack", json!(true));
    cloud.insert(
        "warning",
        json!("Cloud mode sends a compact summary of selected local nodes to an external provider. Sensitive nodes remain blocked. You can switch back to Local only at any time."),
    );
    cloud.insert(
        "warning_ko",
        json!("클라우드 모드는 선택된 로컬 노드의 압축 요약을 외부 제공자에게 전송합니다. 민감 노드는 계속 차단됩니다. 언제든지 로컬만으로 되돌릴 수 있습니다."),
    );
    vec![local, cloud]
}

fn normalize_mode(value: &str) -> &'static str {
    match value.trim().to_ascii_lowercase().as_str() {
        "cloud_allowed" | "cloud" | "cloud-allowed" | "hybrid" | "online" => "cloud_allowed",
        _ => "local_only",
    }
}

fn mode_contract(mode: &str) -> OrderedMap {
    let mode = normalize_mode(mode);
    let catalog = network_mode_catalog();
    let entry = catalog
        .iter()
        .find(|m| m.get("id").and_then(Value::as_str) == Some(mode))
        .cloned()
        .unwrap_or_else(|| catalog[0].clone());
    let mut map = OrderedMap::new();
    map.insert("mode", json!(mode));
    map.insert("label", entry.get("label").cloned().unwrap_or(Value::Null));
    map.insert(
        "label_ko",
        entry.get("label_ko").cloned().unwrap_or(Value::Null),
    );
    map.insert("risk", entry.get("risk").cloned().unwrap_or(Value::Null));
    map.insert(
        "requires_ack",
        entry.get("requires_ack").cloned().unwrap_or(json!(false)),
    );
    map.insert("allows_cloud", json!(mode == "cloud_allowed"));
    map.insert("hard_block_node_types", json!(HARD_BLOCK_NODE_TYPES));
    map.insert(
        "hard_block_metadata_flags",
        json!(HARD_BLOCK_METADATA_FLAGS),
    );
    map
}

/// `network_boundary.json`.
pub struct BoundaryStore {
    path: PathBuf,
}

impl BoundaryStore {
    pub fn open(path: PathBuf) -> Self {
        Self { path }
    }

    fn read(&self) -> Value {
        std::fs::read_to_string(&self.path)
            .ok()
            .and_then(|t| serde_json::from_str(&t).ok())
            .unwrap_or_else(|| json!({"default":"local_only","users":{},"workspaces":{}}))
    }

    fn write(&self, data: &Value) {
        if let Some(parent) = self.path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        if let Ok(text) = serde_json::to_string_pretty(data) {
            lattice_auth::atomic::write_text(&self.path, &text);
        }
    }

    pub fn resolve(&self, user_email: Option<&str>, workspace_id: Option<&str>) -> String {
        let data = self.read();
        if let Some(ws) = workspace_id {
            if let Some(mode) = data
                .get("workspaces")
                .and_then(|w| w.get(ws))
                .and_then(Value::as_str)
            {
                return normalize_mode(mode).to_string();
            }
        }
        if let Some(email) = user_email {
            if let Some(mode) = data
                .get("users")
                .and_then(|u| u.get(email.to_ascii_lowercase()))
                .and_then(Value::as_str)
            {
                return normalize_mode(mode).to_string();
            }
        }
        normalize_mode(
            data.get("default")
                .and_then(Value::as_str)
                .unwrap_or("local_only"),
        )
        .to_string()
    }

    pub fn get(&self, user_email: Option<&str>, workspace_id: Option<&str>) -> OrderedMap {
        let mode = self.resolve(user_email, workspace_id);
        let mut contract = mode_contract(&mode);
        contract.insert("catalog", json!(network_mode_catalog()));
        let mut scope = OrderedMap::new();
        scope.insert("user_email", json!(user_email));
        scope.insert("workspace_id", json!(workspace_id));
        contract.insert("scope", json!(scope));
        contract
    }

    pub fn set_mode(
        &self,
        mode: &str,
        user_email: Option<&str>,
        workspace_id: Option<&str>,
        acknowledge_risk: bool,
    ) -> Result<OrderedMap, String> {
        let mode = normalize_mode(mode);
        if mode == "cloud_allowed" && !acknowledge_risk {
            return Err(
                "cloud_allowed mode requires acknowledge_risk=true (minimal related Knowledge Graph nodes may leave this machine)"
                    .into(),
            );
        }
        let mut data = self.read();
        if let Some(obj) = data.as_object_mut() {
            if let Some(ws) = workspace_id {
                let workspaces = obj.entry("workspaces").or_insert_with(|| json!({}));
                if let Some(map) = workspaces.as_object_mut() {
                    map.insert(ws.to_string(), json!(mode));
                }
            } else if let Some(email) = user_email {
                let users = obj.entry("users").or_insert_with(|| json!({}));
                if let Some(map) = users.as_object_mut() {
                    map.insert(email.to_ascii_lowercase(), json!(mode));
                }
            } else {
                obj.insert("default".into(), json!(mode));
            }
        }
        self.write(&data);
        Ok(self.get(user_email, workspace_id))
    }
}

/// `hybrid_policy.json`.
pub struct PolicyStore {
    path: PathBuf,
}

impl PolicyStore {
    pub fn open(path: PathBuf) -> Self {
        Self { path }
    }

    fn default_policy() -> OrderedMap {
        let mut map = OrderedMap::new();
        map.insert("blocked_node_types", json!(Vec::<String>::new()));
        map.insert("blocked_metadata_flags", json!(HARD_BLOCK_METADATA_FLAGS));
        map.insert("auto_commit", json!(false));
        map.insert("allow_multimodal", json!(false));
        map.insert("min_extraction_confidence", json!(0.55));
        map
    }

    fn read(&self) -> Value {
        std::fs::read_to_string(&self.path)
            .ok()
            .and_then(|t| serde_json::from_str(&t).ok())
            .unwrap_or_else(|| {
                json!({"default": serde_json::to_value(Self::default_policy()).unwrap_or(json!({})), "users":{}, "workspaces":{}})
            })
    }

    fn write(&self, data: &Value) {
        if let Some(parent) = self.path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        if let Ok(text) = serde_json::to_string_pretty(data) {
            lattice_auth::atomic::write_text(&self.path, &text);
        }
    }

    pub fn resolve(&self, user_email: Option<&str>, workspace_id: Option<&str>) -> OrderedMap {
        let data = self.read();
        let mut policy = data
            .get("default")
            .cloned()
            .unwrap_or_else(|| serde_json::to_value(Self::default_policy()).unwrap_or(json!({})));
        if let Some(email) = user_email {
            if let Some(user) = data
                .get("users")
                .and_then(|u| u.get(email.to_ascii_lowercase()))
            {
                merge_obj(&mut policy, user);
            }
        }
        if let Some(ws) = workspace_id {
            if let Some(entry) = data.get("workspaces").and_then(|w| w.get(ws)) {
                merge_obj(&mut policy, entry);
            }
        }
        let mut types: Vec<String> = policy
            .get("blocked_node_types")
            .and_then(Value::as_array)
            .map(|a| {
                a.iter()
                    .filter_map(Value::as_str)
                    .map(str::to_string)
                    .collect()
            })
            .unwrap_or_default();
        for t in HARD_BLOCK_NODE_TYPES {
            if !types.iter().any(|x| x == t) {
                types.push((*t).into());
            }
        }
        types.sort();
        let mut flags: Vec<String> = policy
            .get("blocked_metadata_flags")
            .and_then(Value::as_array)
            .map(|a| {
                a.iter()
                    .filter_map(Value::as_str)
                    .map(str::to_string)
                    .collect()
            })
            .unwrap_or_default();
        for f in HARD_BLOCK_METADATA_FLAGS {
            if !flags.iter().any(|x| x == f) {
                flags.push((*f).into());
            }
        }
        flags.sort();
        let mut map = OrderedMap::new();
        map.insert("blocked_node_types", json!(types));
        map.insert("blocked_metadata_flags", json!(flags));
        map.insert(
            "auto_commit",
            json!(policy
                .get("auto_commit")
                .and_then(Value::as_bool)
                .unwrap_or(false)),
        );
        map.insert(
            "allow_multimodal",
            json!(policy
                .get("allow_multimodal")
                .and_then(Value::as_bool)
                .unwrap_or(false)),
        );
        map.insert(
            "min_extraction_confidence",
            json!(policy
                .get("min_extraction_confidence")
                .and_then(Value::as_f64)
                .unwrap_or(0.55)),
        );
        map
    }

    pub fn set_policy(
        &self,
        patch: &serde_json::Map<String, Value>,
        user_email: Option<&str>,
        workspace_id: Option<&str>,
    ) -> OrderedMap {
        let allowed = [
            "blocked_node_types",
            "blocked_metadata_flags",
            "auto_commit",
            "allow_multimodal",
            "min_extraction_confidence",
        ];
        let clean: serde_json::Map<String, Value> = patch
            .iter()
            .filter(|(k, _)| allowed.contains(&k.as_str()))
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect();
        let mut data = self.read();
        if let Some(obj) = data.as_object_mut() {
            if let Some(ws) = workspace_id {
                let bucket = obj.entry("workspaces").or_insert_with(|| json!({}));
                if let Some(map) = bucket.as_object_mut() {
                    let mut current = map.get(ws).cloned().unwrap_or(json!({}));
                    merge_obj(&mut current, &Value::Object(clean));
                    map.insert(ws.to_string(), current);
                }
            } else if let Some(email) = user_email {
                let bucket = obj.entry("users").or_insert_with(|| json!({}));
                if let Some(map) = bucket.as_object_mut() {
                    let key = email.to_ascii_lowercase();
                    let mut current = map.get(&key).cloned().unwrap_or(json!({}));
                    merge_obj(&mut current, &Value::Object(clean));
                    map.insert(key, current);
                }
            } else {
                let mut current = obj.get("default").cloned().unwrap_or(json!({}));
                merge_obj(&mut current, &Value::Object(clean));
                obj.insert("default".into(), current);
            }
        }
        self.write(&data);
        self.resolve(user_email, workspace_id)
    }
}

fn merge_obj(target: &mut Value, patch: &Value) {
    if let (Some(t), Some(p)) = (target.as_object_mut(), patch.as_object()) {
        for (k, v) in p {
            t.insert(k.clone(), v.clone());
        }
    }
}

pub(crate) struct TokenBudget {
    max_tokens_per_turn: u64,
    max_tokens_per_session: u64,
    session_used: u64,
}

impl Default for TokenBudget {
    fn default() -> Self {
        Self {
            max_tokens_per_turn: 2500,
            max_tokens_per_session: 50_000,
            session_used: 0,
        }
    }
}

impl TokenBudget {
    pub(super) fn snapshot(&self) -> OrderedMap {
        let mut map = OrderedMap::new();
        map.insert("max_tokens_per_turn", json!(self.max_tokens_per_turn));
        map.insert("max_tokens_per_session", json!(self.max_tokens_per_session));
        map.insert("session_used", json!(self.session_used));
        map.insert(
            "session_remaining",
            json!(self
                .max_tokens_per_session
                .saturating_sub(self.session_used)),
        );
        map
    }
}
