//! Review-item persistence ports and standalone `workspace_os.json` store for
//! [`super`] (`proposals`).

use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use serde_json::{json, Value};
use sha2::{Digest, Sha256};

/// The file the review items live in (`lattice_core::db::state_files::WORKSPACE_OS`).
const WORKSPACE_OS_FILE: &str = "workspace_os.json";
/// The workspace a review item belongs to when the caller names none.
const DEFAULT_WORKSPACE_ID: &str = "personal";

/// One review item to create — `ReviewQueueService.create`'s arguments.
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

/// Where a staged proposal is persisted.
///
/// `create` answers the **stored item**, id included, because the loop puts
/// that id on the transcript and in the audit trail: "제안으로 저장했습니다"
/// with no id is a claim the user cannot check.
pub trait ProposalStore: std::fmt::Debug + Send + Sync {
    /// Append one review item; `Err` is a staging failure, never a panic.
    fn create(&self, item: &NewReviewItem) -> Result<Value, String>;
}

/// The one owner of `workspace_os.json`, when this process has one.
///
/// The same inversion [`ProposalStore`] already uses, one level down. A
/// `JsonProposalStore` is *correct standalone* and *wrong in the product*: it
/// appends to the JSON file alone, while `lattice_platform`'s
/// `WorkspaceOsStore` reads the `workspace_os_state` SQLite row first and
/// rewrites the file from it. So a proposal staged through the JSON store in a
/// process that also runs the Review Center is invisible to it and is
/// overwritten by its next save.
///
/// Injecting the right store at every construction site means every future
/// construction site has to remember (there are five today, and
/// `LoopDeps::new`'s default is reached whenever a `LoopConfig` leaves
/// `proposals: None`). Installing the owner once makes it true by
/// construction instead: with a writer installed, [`JsonProposalStore`] stops
/// being a second writer and becomes a *shape* — it still builds the review
/// item, and the owner performs the write.
pub trait DocumentWriter: Send + Sync {
    /// Load, apply `body`, save — under the owner's write lock.
    fn mutate(&self, body: &mut dyn FnMut(&mut Value)) -> Result<(), String>;
}

static DOCUMENT_WRITER: std::sync::OnceLock<Arc<dyn DocumentWriter>> = std::sync::OnceLock::new();

/// Name the process's `workspace_os.json` owner. The first call wins.
///
/// A later call is refused (`false`) rather than swapping the document under a
/// run that is mid-stage, the same rule
/// `lattice_retrieval::memory_api::wsos::install_state_writer` follows.
pub fn install_document_writer(writer: Arc<dyn DocumentWriter>) -> bool {
    DOCUMENT_WRITER.set(writer).is_ok()
}

/// The installed owner, if the host wired one.
pub fn document_writer() -> Option<&'static Arc<dyn DocumentWriter>> {
    DOCUMENT_WRITER.get()
}

/// The standalone store: `<data_dir>/workspace_os.json`.
///
/// Byte-compatible with `lattice_platform::governance::review_queue` — see the module
/// docs, and [`DocumentWriter`] for what happens when the Review Center is in
/// this process too.
#[derive(Debug, Clone)]
pub struct JsonProposalStore {
    data_dir: PathBuf,
    /// One writer at a time. Appending is read-modify-write over a whole
    /// document, so two runs staging at once would otherwise keep whichever
    /// wrote last and silently drop the other's proposal. Shared across clones;
    /// it does not cover a second *process* (nor did Python's store), and it is
    /// unused when a [`DocumentWriter`] is installed — that lock is the
    /// document owner's, and is the only one the platform respects.
    lock: Arc<Mutex<()>>,
}

impl JsonProposalStore {
    /// A store over an explicit data directory.
    pub fn new(data_dir: impl Into<PathBuf>) -> Self {
        Self {
            data_dir: data_dir.into(),
            lock: Arc::new(Mutex::new(())),
        }
    }

    /// `LATTICEAI_DATA_DIR`, or `$HOME/.ltcai` — [`crate::kernel::runs::default_runs_dir`]'s rule.
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

    /// The document this store appends to.
    pub fn state_path(&self) -> PathBuf {
        self.data_dir.join(WORKSPACE_OS_FILE)
    }
}

impl ProposalStore for JsonProposalStore {
    fn create(&self, item: &NewReviewItem) -> Result<Value, String> {
        if item.title.trim().is_empty() {
            // `WorkspaceReviewItems.create_review_item`'s own ValueError.
            return Err("title is required".into());
        }
        if let Some(writer) = document_writer() {
            // The owner holds its lock across the load and the save, so the id
            // scan and the append below cannot be raced by a review action.
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

/// Every review-item id already in the document — the collision set.
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

/// One review item, exactly as `lattice_platform::governance::review_queue` builds it.
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

/// `review-{_json_hash([title, source, kind, user_email, now, seq?])[:16]}`.
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

/// The document `GovernanceState::open` starts from when there is no file.
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

/// `save_workspace_os`: stamp the version and `updated_at`, then replace.
///
/// The SQLite mirror `lattice-platform` also writes is **not** written here —
/// this crate has no SQLite dependency, and a store that mirrored half of the
/// pair would be worse than one that mirrors none. It is the reason the
/// in-process Review Center must inject its own store; the module docs say so.
fn write_state(path: &Path, state: &mut Value) -> Result<(), String> {
    if let Some(object) = state.as_object_mut() {
        object.insert("version".into(), json!(crate::VERSION));
        object.insert("updated_at".into(), json!(now_iso()));
    }
    let text = serde_json::to_string_pretty(state).map_err(|error| error.to_string())?;
    atomic_write(path, &format!("{text}\n"))
}

/// Temp file + rename, as `lattice_auth::atomic::write_text` (its owner).
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

/// `now_iso()` — naive, second resolution, no offset (the store's format).
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

/// Howard Hinnant's `civil_from_days`, as `lattice_platform::governance::review_queue` uses.
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
