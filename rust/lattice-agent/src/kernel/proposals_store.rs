use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use serde_json::{json, Value};
use sha2::{Digest, Sha256};

const WORKSPACE_OS_FILE: &str = "workspace_os.json";
const DEFAULT_WORKSPACE_ID: &str = "personal";

#[derive(Debug, Clone)]
pub struct NewReviewItem {
    pub title: String,
    pub summary: String,
    pub source: String,
    pub kind: String,
    pub payload: Value,
    pub provenance: Value,
    pub user_email: Option<String>,
    pub workspace_id: Option<String>,
}

pub trait ProposalStore: std::fmt::Debug + Send + Sync {
    fn create(&self, item: &NewReviewItem) -> Result<Value, String>;
}

pub trait DocumentWriter: Send + Sync {
    fn mutate(&self, body: &mut dyn FnMut(&mut Value)) -> Result<(), String>;
}

static DOCUMENT_WRITER: std::sync::OnceLock<Arc<dyn DocumentWriter>> = std::sync::OnceLock::new();

pub fn install_document_writer(writer: Arc<dyn DocumentWriter>) -> bool {
    DOCUMENT_WRITER.set(writer).is_ok()
}

pub fn document_writer() -> Option<&'static Arc<dyn DocumentWriter>> {
    DOCUMENT_WRITER.get()
}

#[derive(Debug, Clone)]
pub struct JsonProposalStore {
    data_dir: PathBuf,
    lock: Arc<Mutex<()>>,
}

impl JsonProposalStore {
    pub fn new(data_dir: impl Into<PathBuf>) -> Self {
        Self {
            data_dir: data_dir.into(),
            lock: Arc::new(Mutex::new(())),
        }
    }

    pub fn from_env() -> Self {
        let configured = std::env::var("LATTICEAI_DATA_DIR").unwrap_or_default();
        if !configured.trim().is_empty() {
            return Self::new(PathBuf::from(configured.trim()));
        }
        let base = std::env::var_os("HOME")
            .map(PathBuf::from)
            .filter(|home| !home.as_os_str().is_empty())
            .map_or_else(|| PathBuf::from(".ltcai"), |home| home.join(".ltcai"));
        Self::new(base)
    }

    pub fn state_path(&self) -> PathBuf {
        self.data_dir.join(WORKSPACE_OS_FILE)
    }
}

impl ProposalStore for JsonProposalStore {
    fn create(&self, item: &NewReviewItem) -> Result<Value, String> {
        if item.title.trim().is_empty() {
            return Err("title is required".into());
        }
        if let Some(writer) = document_writer() {
            let mut stored: Option<Value> = None;
            writer.mutate(&mut |state: &mut Value| {
                let built = build_review_item(item, &existing_ids(state));
                let Some(object) = state.as_object_mut() else {
                    return;
                };
                let rows = object.entry("review_items").or_insert_with(|| json!([]));
                if let Some(list) = rows.as_array_mut() {
                    list.push(built.clone());
                    stored = Some(built);
                }
            })?;
            return stored.ok_or_else(|| "workspace_os.json review_items is not a list".into());
        }
        let path = self.state_path();
        let _guard = self
            .lock
            .lock()
            .map_err(|_| "proposal store lock poisoned")?;
        let mut state = read_state(&path);
        let stored = build_review_item(item, &existing_ids(&state));
        let rows = state
            .as_object_mut()
            .ok_or("workspace_os.json is not an object")?
            .entry("review_items")
            .or_insert_with(|| json!([]));
        rows.as_array_mut()
            .ok_or("workspace_os.json review_items is not a list")?
            .push(stored.clone());
        write_state(&path, &mut state)?;
        Ok(stored)
    }
}

fn existing_ids(state: &Value) -> Vec<String> {
    state
        .get("review_items")
        .and_then(Value::as_array)
        .map(|rows| {
            rows.iter()
                .filter_map(|row| row.get("id").and_then(Value::as_str).map(str::to_string))
                .collect()
        })
        .unwrap_or_default()
}

fn build_review_item(item: &NewReviewItem, existing: &[String]) -> Value {
    let now = now_iso();
    let user_email = item
        .user_email
        .clone()
        .map_or(Value::Null, |email| json!(email));
    let mut item_id = review_id(item, &user_email, &now, None);
    let mut seq = 0u32;
    while existing.iter().any(|id| id == &item_id) {
        seq += 1;
        item_id = review_id(item, &user_email, &now, Some(seq));
    }
    json!({
        "id": item_id,
        "status": "pending",
        "title": item.title,
        "summary": item.summary,
        "source": item.source,
        "kind": item.kind,
        "payload": item.payload,
        "provenance": item.provenance,
        "snoozed_until": null,
        "user_email": user_email,
        "workspace_id": item.workspace_id.as_deref().unwrap_or(DEFAULT_WORKSPACE_ID),
        "created_at": now,
        "updated_at": now,
    })
}

fn review_id(item: &NewReviewItem, user_email: &Value, now: &str, seq: Option<u32>) -> String {
    let mut parts = vec![
        json!(item.title),
        json!(item.source),
        json!(item.kind),
        user_email.clone(),
        json!(now),
    ];
    if let Some(seq) = seq {
        parts.push(json!(seq));
    }
    let payload = serde_json::to_string(&Value::Array(parts)).unwrap_or_else(|_| "null".into());
    let digest: String = Sha256::digest(payload.as_bytes())
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect();
    format!("review-{}", &digest[..16])
}

fn read_state(path: &Path) -> Value {
    if let Ok(text) = std::fs::read_to_string(path) {
        if let Ok(value) = serde_json::from_str::<Value>(&text) {
            if value.is_object() {
                return value;
            }
        }
    }
    default_workspace_os()
}

fn default_workspace_os() -> Value {
    let now = now_iso();
    json!({
        "version": crate::VERSION,
        "identity": "AI Workspace OS",
        "created_at": now,
        "updated_at": now,
        "active_workspace": DEFAULT_WORKSPACE_ID,
        "workspaces": {
            "personal": {
                "workspace_id": DEFAULT_WORKSPACE_ID,
                "id": DEFAULT_WORKSPACE_ID,
                "name": "Personal Workspace",
                "type": "personal",
                "owner_user_id": null,
                "members": [],
                "status": "active",
                "created_at": now,
                "updated_at": now
            }
        },
        "review_items": [],
        "workflows": [],
        "workflow_runs": [],
        "agent_runs": [],
        "timeline": []
    })
}

fn write_state(path: &Path, state: &mut Value) -> Result<(), String> {
    if let Some(object) = state.as_object_mut() {
        object.insert("version".into(), json!(crate::VERSION));
        object.insert("updated_at".into(), json!(now_iso()));
    }
    let text = serde_json::to_string_pretty(state).map_err(|error| error.to_string())?;
    atomic_write(path, &format!("{text}\n"))
}

fn atomic_write(path: &Path, text: &str) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    let mut temp = path.as_os_str().to_os_string();
    temp.push(".tmp");
    let temp = PathBuf::from(temp);
    if let Err(error) = std::fs::write(&temp, text) {
        let _ = std::fs::remove_file(&temp);
        return Err(error.to_string());
    }
    std::fs::rename(&temp, path).map_err(|error| {
        let _ = std::fs::remove_file(&temp);
        error.to_string()
    })
}

fn now_iso() -> String {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let days = (secs / 86_400) as i64;
    let rest = secs % 86_400;
    let (hour, min, sec) = (rest / 3600, (rest % 3600) / 60, rest % 60);
    let (year, month, day) = civil_from_days(days);
    format!("{year:04}-{month:02}-{day:02}T{hour:02}:{min:02}:{sec:02}")
}

fn civil_from_days(mut days: i64) -> (i32, u32, u32) {
    days += 719_468;
    let era = if days >= 0 { days } else { days - 146_096 } / 146_097;
    let doe = (days - era * 146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
    let year = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = if month <= 2 { year + 1 } else { year };
    (year as i32, month as u32, day as u32)
}
