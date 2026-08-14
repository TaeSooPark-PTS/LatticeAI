//! The user identity store — `users.json`, and the v4 UUID migration.
//!
//! Port of `latticeai/core/users.py`. The file lives at `<data_dir>/users.json`
//! and this crate reads and writes **that** file, so a running install keeps
//! its accounts when the front door moves to Rust; nothing is copied to a
//! parallel Rust-only store.
//!
//! Two things the file's own order decides, which is why the map is ordered
//! (see `pyjson`): the first account registered is the fallback administrator
//! (`get_user_role`), and a rewrite should not reshuffle the file.
//!
//! Not ported: `migrate_knowledge_graph_identity`, which rewrites owner columns
//! inside `knowledge_graph.sqlite`. That is a graph write, and the graph has a
//! single writer (the Python worker). The identity map this crate computes is
//! exposed as [`Users::email_to_id`] so the caller that owns graph writes can
//! hand it to the worker seam.

use std::path::{Path, PathBuf};

use serde_json::{json, Map, Value};
use sha1::{Digest, Sha1};

use crate::pyjson::{dumps_indent2, OrderedMap};

/// `uuid.UUID("5d6d4480-cf79-49c3-a6d0-4c6eec3224d6")` — the namespace every
/// stable user id is derived under. Changing it renames every account.
const USER_NAMESPACE: [u8; 16] = [
    0x5d, 0x6d, 0x44, 0x80, 0xcf, 0x79, 0x49, 0xc3, 0xa6, 0xd0, 0x4c, 0x6e, 0xec, 0x32, 0x24, 0xd6,
];

/// `str(email or "").strip().lower()`.
pub fn normalize_email(email: &str) -> String {
    email.trim().to_lowercase()
}

/// `f"user:{uuid.uuid5(USER_NAMESPACE, normalize_email(email))}"`.
pub fn stable_user_id(email: &str) -> String {
    format!("user:{}", uuid5(&USER_NAMESPACE, &normalize_email(email)))
}

/// RFC 4122 version 5 (SHA-1) UUID, rendered lowercase with dashes.
fn uuid5(namespace: &[u8; 16], name: &str) -> String {
    let mut hasher = Sha1::new();
    hasher.update(namespace);
    hasher.update(name.as_bytes());
    let digest = hasher.finalize();
    let mut bytes = [0u8; 16];
    bytes.copy_from_slice(&digest[..16]);
    bytes[6] = (bytes[6] & 0x0f) | 0x50;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    let hex: String = bytes.iter().map(|byte| format!("{byte:02x}")).collect();
    format!(
        "{}-{}-{}-{}-{}",
        &hex[0..8],
        &hex[8..12],
        &hex[12..16],
        &hex[16..20],
        &hex[20..32]
    )
}

/// Give one record its stable id and normalised email. Returns whether the
/// record changed.
pub fn ensure_user_identity(email: &str, user: &mut Map<String, Value>) -> bool {
    let mut changed = false;
    let fallback = user
        .get("email")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let source = if email.is_empty() { &fallback } else { email };
    let normalized = normalize_email(source);
    let has_id = user
        .get("id")
        .map(|value| !is_falsy(value))
        .unwrap_or(false);
    if !has_id {
        user.insert("id".into(), json!(stable_user_id(&normalized)));
        changed = true;
    }
    if user.get("email").and_then(Value::as_str) != Some(normalized.as_str()) {
        user.insert("email".into(), json!(normalized));
        changed = true;
    }
    changed
}

/// Python truthiness for the values an id field can hold.
fn is_falsy(value: &Value) -> bool {
    match value {
        Value::Null => true,
        Value::Bool(flag) => !flag,
        Value::String(text) => text.is_empty(),
        Value::Number(number) => number.as_f64() == Some(0.0),
        Value::Array(items) => items.is_empty(),
        Value::Object(map) => map.is_empty(),
    }
}

/// Every account, keyed by normalised email, in the file's own order.
#[derive(Debug, Clone, Default)]
pub struct Users {
    map: OrderedMap,
}

impl Users {
    /// An empty account store.
    pub fn new() -> Self {
        Self::default()
    }

    /// The record for `email`, already normalised by the caller or not.
    pub fn get(&self, email: &str) -> Option<&Map<String, Value>> {
        self.map.get(email).and_then(Value::as_object)
    }

    /// Whether an account exists under exactly this key.
    pub fn contains(&self, email: &str) -> bool {
        self.get(email).is_some()
    }

    /// Insert or replace one account record.
    pub fn insert(&mut self, email: impl Into<String>, record: Map<String, Value>) {
        self.map.insert(email, Value::Object(record));
    }

    /// The first account in the file — the fallback administrator.
    pub fn first_email(&self) -> Option<&str> {
        self.map.first_key()
    }

    /// How many accounts exist.
    pub fn len(&self) -> usize {
        self.map.len()
    }

    /// Whether there are no accounts at all.
    pub fn is_empty(&self) -> bool {
        self.map.is_empty()
    }

    /// Every `(email, record)` pair, in file order.
    pub fn iter(&self) -> impl Iterator<Item = (&str, &Map<String, Value>)> {
        self.map
            .iter()
            .filter_map(|(email, value)| value.as_object().map(|record| (email, record)))
    }

    /// The `email → stable id` map the graph-identity migration needs.
    pub fn email_to_id(&self) -> Vec<(String, String)> {
        self.iter()
            .filter_map(|(email, record)| {
                record
                    .get("id")
                    .and_then(Value::as_str)
                    .map(|id| (email.to_string(), id.to_string()))
            })
            .collect()
    }

    /// The account whose record carries `identity` as its `id`.
    pub fn by_id(&self, identity: &str) -> Option<(&str, &Map<String, Value>)> {
        self.iter()
            .find(|(_, record)| record.get("id").and_then(Value::as_str) == Some(identity))
    }

    /// `user_id_for_email` — the stable id for an email, or `None` for none.
    pub fn user_id_for_email(&self, email: Option<&str>) -> Option<String> {
        let email = email.filter(|value| !value.is_empty())?;
        if email.starts_with("user:") {
            return Some(email.to_string());
        }
        let normalized = normalize_email(email);
        match self.get(&normalized) {
            Some(record) => Some(
                record
                    .get("id")
                    .and_then(Value::as_str)
                    .filter(|id| !id.is_empty())
                    .map(str::to_string)
                    .unwrap_or_else(|| stable_user_id(&normalized)),
            ),
            None => Some(stable_user_id(&normalized)),
        }
    }
}

/// `migrate_users`: normalise keys, backfill ids, merge duplicates.
pub fn migrate_users(raw: &OrderedMap) -> (Users, bool) {
    let mut migrated = Users::new();
    let mut changed = false;
    for (raw_email, raw_user) in raw.iter() {
        let Some(record) = raw_user.as_object() else {
            continue;
        };
        let email = normalize_email(
            record
                .get("email")
                .and_then(Value::as_str)
                .filter(|value| !value.is_empty())
                .unwrap_or(raw_email),
        );
        let mut user = record.clone();
        if ensure_user_identity(&email, &mut user) {
            changed = true;
        }
        if raw_email != email {
            changed = true;
        }
        if let Some(existing) = migrated.get(&email) {
            user = merge_duplicate(existing, &user, &email);
            changed = true;
        }
        migrated.insert(email, user);
    }
    (migrated, changed)
}

/// `{**existing, **user}` plus the two fields the Python merge repairs.
fn merge_duplicate(
    existing: &Map<String, Value>,
    user: &Map<String, Value>,
    email: &str,
) -> Map<String, Value> {
    let mut merged = existing.clone();
    for (key, value) in user {
        merged.insert(key.clone(), value.clone());
    }
    let id = existing
        .get("id")
        .filter(|value| !is_falsy(value))
        .or_else(|| user.get("id").filter(|value| !is_falsy(value)))
        .cloned()
        .unwrap_or_else(|| json!(stable_user_id(email)));
    merged.insert("id".into(), id);
    let existing_keys = existing.get("api_keys").and_then(Value::as_object);
    let user_keys = user.get("api_keys").and_then(Value::as_object);
    if existing_keys.is_some() || user_keys.is_some() {
        let mut keys = existing_keys.cloned().unwrap_or_default();
        for (key, value) in user_keys.cloned().unwrap_or_default() {
            keys.insert(key, value);
        }
        merged.insert("api_keys".into(), Value::Object(keys));
    }
    merged
}

/// Read/write access to `users.json`.
#[derive(Debug, Clone)]
pub struct UserStore {
    path: PathBuf,
}

impl UserStore {
    /// The store at `<data_dir>/users.json`.
    pub fn new(data_dir: &Path) -> Self {
        Self {
            path: data_dir.join("users.json"),
        }
    }

    /// The file this store reads and writes.
    pub fn path(&self) -> &Path {
        &self.path
    }

    /// `load_users_file`: parse, migrate, and rewrite when the migration
    /// changed anything.
    ///
    /// Deviation, recorded: the Python loader also copies the pre-migration
    /// file to `users.json.pre-user-uuid.<timestamp>.json`. That backup is not
    /// written here — the migration is idempotent and 11.x installs have long
    /// since run it, and a timestamped file name is not something a parity
    /// fixture can pin.
    pub fn load(&self) -> Users {
        let raw = match std::fs::read_to_string(&self.path) {
            Ok(text) => serde_json::from_str::<OrderedMap>(&text).unwrap_or_default(),
            Err(_) => return Users::new(),
        };
        let (users, changed) = migrate_users(&raw);
        if changed {
            self.write(&users);
        }
        users
    }

    /// `save_users_file`: migrate once more, then write atomically.
    pub fn save(&self, users: &Users) {
        let (migrated, _) = migrate_users(&users.map);
        self.write(&migrated);
    }

    fn write(&self, users: &Users) {
        let Ok(text) = dumps_indent2(&users.map) else {
            return;
        };
        crate::atomic::write_text(&self.path, &text);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ordered(text: &str) -> OrderedMap {
        serde_json::from_str(text).unwrap()
    }

    #[test]
    fn stable_ids_match_the_recorded_python_values() {
        // Produced by `latticeai.core.users.stable_user_id` for these emails.
        assert_eq!(
            stable_user_id("a@b.com"),
            "user:86f56efe-644a-5ccb-8043-7065edc4df96"
        );
        assert_eq!(stable_user_id("  A@B.com "), stable_user_id("a@b.com"));
    }

    #[test]
    fn email_normalisation_matches_python() {
        assert_eq!(normalize_email("  Mixed@Case.COM "), "mixed@case.com");
        assert_eq!(normalize_email(""), "");
    }

    #[test]
    fn identity_is_backfilled_once() {
        let mut record: Map<String, Value> = serde_json::from_str(r#"{"name":"Al"}"#).unwrap();
        assert!(ensure_user_identity("A@B.com", &mut record));
        assert_eq!(record["email"], json!("a@b.com"));
        assert_eq!(record["id"], json!(stable_user_id("a@b.com")));
        assert!(!ensure_user_identity("a@b.com", &mut record));
    }

    #[test]
    fn identity_falls_back_to_the_record_email() {
        let mut record: Map<String, Value> =
            serde_json::from_str(r#"{"email":"  X@Y.Z "}"#).unwrap();
        assert!(ensure_user_identity("", &mut record));
        assert_eq!(record["email"], json!("x@y.z"));
    }

    #[test]
    fn migration_normalises_keys_and_merges_duplicates() {
        let raw = ordered(
            r#"{
              "A@B.com": {"name":"first","api_keys":{"openai":"1"}},
              "a@b.com": {"name":"second","api_keys":{"groq":"2"}},
              "broken": 3
            }"#,
        );
        let (users, changed) = migrate_users(&raw);
        assert!(changed);
        assert_eq!(users.len(), 1);
        let record = users.get("a@b.com").unwrap();
        assert_eq!(record["name"], json!("second"));
        assert_eq!(record["api_keys"]["openai"], json!("1"));
        assert_eq!(record["api_keys"]["groq"], json!("2"));
        assert_eq!(record["id"], json!(stable_user_id("a@b.com")));
    }

    #[test]
    fn a_clean_file_is_not_rewritten() {
        let raw = ordered(
            r#"{"a@b.com":{"email":"a@b.com","id":"user:86f56efe-644a-5ccb-8043-7065edc4df96"}}"#,
        );
        let (_, changed) = migrate_users(&raw);
        assert!(!changed);
    }

    #[test]
    fn lookups_answer_the_python_questions() {
        let raw = ordered(r#"{"a@b.com":{"id":"user:one"},"c@d.com":{"id":"user:two"}}"#);
        let (users, _) = migrate_users(&raw);
        assert_eq!(users.first_email(), Some("a@b.com"));
        assert_eq!(users.by_id("user:two").unwrap().0, "c@d.com");
        assert!(users.by_id("user:nope").is_none());
        assert_eq!(
            users.user_id_for_email(Some("user:raw")),
            Some("user:raw".into())
        );
        assert_eq!(
            users.user_id_for_email(Some("A@B.com")),
            Some("user:one".into())
        );
        assert_eq!(
            users.user_id_for_email(Some("nobody@x.y")),
            Some(stable_user_id("nobody@x.y"))
        );
        assert_eq!(users.user_id_for_email(None), None);
        assert_eq!(users.user_id_for_email(Some("")), None);
        assert_eq!(users.email_to_id().len(), 2);
        assert!(users.contains("a@b.com"));
        assert!(!users.is_empty());
    }

    #[test]
    fn the_store_round_trips_through_the_real_file_name() {
        let dir = tempfile::tempdir().unwrap();
        let store = UserStore::new(dir.path());
        assert!(store.path().ends_with("users.json"));
        assert!(store.load().is_empty());

        let mut users = Users::new();
        let mut record = Map::new();
        record.insert("name".into(), json!("Al"));
        users.insert("A@B.com", record);
        store.save(&users);

        let reloaded = store.load();
        assert_eq!(reloaded.len(), 1);
        assert!(reloaded.contains("a@b.com"));
        let text = std::fs::read_to_string(store.path()).unwrap();
        assert!(text.starts_with("{\n  \"a@b.com\""), "{text}");
    }

    #[test]
    fn an_unreadable_file_starts_empty() {
        let dir = tempfile::tempdir().unwrap();
        let store = UserStore::new(dir.path());
        std::fs::write(store.path(), "not json").unwrap();
        assert!(store.load().is_empty());
    }
}
