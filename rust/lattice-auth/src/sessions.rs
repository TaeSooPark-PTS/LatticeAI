//! The session store — `sessions.json`, sliding-window TTL, hashed at rest.
//!
//! Port of `latticeai/core/sessions.py` (v4). The file is
//! `<data_dir>/sessions.json`, the same path Python uses, so live sessions
//! survive the front door moving to Rust.
//!
//! Three properties are the contract:
//!
//! * **keys are `sha256(token)`**, so a process that can read the file cannot
//!   hijack a session with what it finds there. A pre-v4 file whose keys are
//!   raw tokens is re-keyed transparently on load;
//! * **entries are positional** — `[subject, created_at, email]`, with older
//!   1- and 2-element rows still readable, so the reader indexes defensively
//!   exactly where Python's `_entry_*` helpers do;
//! * **the TTL slides**: a session older than the refresh threshold is stamped
//!   forward on read (and persisted), but only then, so a chatty client does
//!   not rewrite the file on every request.

use std::path::{Path, PathBuf};
use std::sync::Mutex;

use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::clock::Clock;
use crate::pyjson::{dumps_indent2, OrderedMap};

/// 24 hours, `SESSION_TTL`.
pub const SESSION_TTL: f64 = 60.0 * 60.0 * 24.0;
/// 15 minutes, `SESSION_REFRESH_THRESHOLD`: only persist a bump past this.
pub const SESSION_REFRESH_THRESHOLD: f64 = 60.0 * 15.0;

/// `secrets.token_urlsafe(32)` — 32 random bytes, base64url, no padding.
pub fn new_token() -> String {
    let mut bytes = [0u8; 32];
    getrandom::fill(&mut bytes).expect("the OS RNG is required to issue a session");
    URL_SAFE_NO_PAD.encode(bytes)
}

/// The at-rest key for a token. A missing token hashes the empty string.
pub fn hash_token(token: &str) -> String {
    let digest = Sha256::digest(token.as_bytes());
    digest.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn looks_hashed(key: &str) -> bool {
    key.len() == 64
        && key
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
}

/// The file-backed session table.
#[derive(Debug)]
pub struct SessionStore {
    path: PathBuf,
    ttl: f64,
    refresh_threshold: f64,
    clock: Clock,
    sessions: Mutex<OrderedMap>,
}

impl SessionStore {
    /// Open (or start) the store at `<data_dir>/sessions.json`.
    pub fn new(data_dir: &Path, ttl_seconds: f64, clock: Clock) -> Self {
        let path = data_dir.join("sessions.json");
        let store = Self {
            path,
            ttl: if ttl_seconds > 0.0 {
                ttl_seconds
            } else {
                SESSION_TTL
            },
            refresh_threshold: SESSION_REFRESH_THRESHOLD,
            clock,
            sessions: Mutex::new(OrderedMap::new()),
        };
        store.load();
        store
    }

    /// The file this store reads and writes.
    pub fn path(&self) -> &Path {
        &self.path
    }

    /// `load_sessions`: read, migrate pre-v4 raw-token keys, persist if any
    /// key moved. An unreadable file starts empty rather than failing.
    fn load(&self) {
        let Ok(text) = std::fs::read_to_string(&self.path) else {
            return;
        };
        let Ok(raw) = serde_json::from_str::<OrderedMap>(&text) else {
            return;
        };
        let mut sessions = OrderedMap::new();
        let mut migrated = false;
        for (key, value) in raw.iter() {
            if looks_hashed(key) {
                sessions.insert(key, value.clone());
            } else {
                sessions.insert(hash_token(key), value.clone());
                migrated = true;
            }
        }
        let mut guard = self.sessions.lock().expect("session lock");
        *guard = sessions;
        if migrated {
            self.persist(&guard);
        }
    }

    fn persist(&self, sessions: &OrderedMap) {
        if let Ok(text) = dumps_indent2(sessions) {
            crate::atomic::write_text(&self.path, &text);
        }
    }

    /// Issue a session for `subject`, recording `email` alongside it.
    pub fn create(&self, subject: &str, email: Option<&str>) -> String {
        let token = new_token();
        let entry = json!([subject, self.clock.now(), email.unwrap_or(subject),]);
        let mut guard = self.sessions.lock().expect("session lock");
        guard.insert(hash_token(&token), entry);
        self.persist(&guard);
        token
    }

    /// The email this token authenticates, if it is still live.
    pub fn get_email(&self, token: &str) -> Option<String> {
        self.entry(token).and_then(|entry| entry_email(&entry))
    }

    /// The stable user id this token authenticates, if it is still live.
    pub fn get_subject(&self, token: &str) -> Option<String> {
        self.entry(token).and_then(|entry| entry_subject(&entry))
    }

    /// Forget one session.
    pub fn invalidate(&self, token: &str) {
        let mut guard = self.sessions.lock().expect("session lock");
        guard.remove(&hash_token(token));
        self.persist(&guard);
    }

    /// How many sessions are live in memory (test/observability seam).
    pub fn len(&self) -> usize {
        self.sessions.lock().expect("session lock").len()
    }

    /// Whether the table is empty.
    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// `_get_entry`: expire, slide, or return as-is.
    fn entry(&self, token: &str) -> Option<Value> {
        let now = self.clock.now();
        let key = hash_token(token);
        let mut guard = self.sessions.lock().expect("session lock");
        let entry = guard.get(&key)?.clone();
        let created_at = entry_created_at(&entry);
        if now - created_at > self.ttl {
            guard.remove(&key);
            self.persist(&guard);
            return None;
        }
        if now - created_at > self.refresh_threshold {
            let refreshed = json!([entry_subject(&entry), now, entry_email(&entry),]);
            guard.insert(key, refreshed.clone());
            self.persist(&guard);
            return Some(refreshed);
        }
        Some(entry)
    }
}

/// `_entry_subject`: element 0, or `None` for an empty row.
fn entry_subject(entry: &Value) -> Option<String> {
    let items = entry.as_array()?;
    items.first().and_then(Value::as_str).map(str::to_string)
}

/// `_entry_email`: element 2 when it is truthy, else element 0.
fn entry_email(entry: &Value) -> Option<String> {
    let items = entry.as_array()?;
    if items.len() >= 3 {
        if let Some(email) = items[2].as_str().filter(|value| !value.is_empty()) {
            return Some(email.to_string());
        }
    }
    entry_subject(entry)
}

/// `_entry_created_at`: element 1 as a float, else 0.
fn entry_created_at(entry: &Value) -> f64 {
    entry
        .as_array()
        .and_then(|items| items.get(1))
        .and_then(Value::as_f64)
        .unwrap_or(0.0)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn store(dir: &Path, clock: Clock) -> SessionStore {
        SessionStore::new(dir, SESSION_TTL, clock)
    }

    #[test]
    fn a_new_token_looks_like_the_python_one() {
        let token = new_token();
        assert_eq!(token.len(), 43);
        assert!(!token.contains('='));
    }

    #[test]
    fn tokens_are_hashed_at_rest() {
        let dir = tempfile::tempdir().unwrap();
        let store = store(dir.path(), Clock::frozen(1_000.0));
        let token = store.create("user:one", Some("a@b.com"));
        let text = std::fs::read_to_string(store.path()).unwrap();
        assert!(!text.contains(&token));
        assert!(text.contains(&hash_token(&token)));
        assert_eq!(store.get_email(&token).as_deref(), Some("a@b.com"));
        assert_eq!(store.get_subject(&token).as_deref(), Some("user:one"));
        assert_eq!(store.len(), 1);
    }

    #[test]
    fn an_expired_session_is_dropped_from_the_file() {
        let dir = tempfile::tempdir().unwrap();
        let clock = Clock::frozen(1_000.0);
        let store = store(dir.path(), clock.clone());
        let token = store.create("user:one", None);
        clock.advance(SESSION_TTL + 1.0);
        assert_eq!(store.get_email(&token), None);
        assert!(store.is_empty());
    }

    #[test]
    fn the_ttl_slides_only_past_the_refresh_threshold() {
        let dir = tempfile::tempdir().unwrap();
        let clock = Clock::frozen(1_000.0);
        let store = store(dir.path(), clock.clone());
        let token = store.create("user:one", Some("a@b.com"));

        clock.advance(SESSION_REFRESH_THRESHOLD - 1.0);
        store.get_email(&token);
        let before: OrderedMap =
            serde_json::from_str(&std::fs::read_to_string(store.path()).unwrap()).unwrap();
        assert_eq!(before.get(&hash_token(&token)).unwrap()[1], json!(1_000.0));

        clock.advance(2.0);
        assert_eq!(store.get_email(&token).as_deref(), Some("a@b.com"));
        let after: OrderedMap =
            serde_json::from_str(&std::fs::read_to_string(store.path()).unwrap()).unwrap();
        assert_eq!(
            after.get(&hash_token(&token)).unwrap()[1],
            json!(1_000.0 + SESSION_REFRESH_THRESHOLD + 1.0)
        );
    }

    #[test]
    fn a_pre_v4_file_is_rekeyed_on_load() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("sessions.json");
        std::fs::write(
            &path,
            r#"{"raw-token-value":["user:one",1000.0,"a@b.com"]}"#,
        )
        .unwrap();
        let store = store(dir.path(), Clock::frozen(1_000.0));
        assert_eq!(
            store.get_email("raw-token-value").as_deref(),
            Some("a@b.com")
        );
        let text = std::fs::read_to_string(&path).unwrap();
        assert!(!text.contains("raw-token-value"));
        assert!(text.contains(&hash_token("raw-token-value")));
    }

    #[test]
    fn short_legacy_rows_still_resolve() {
        let dir = tempfile::tempdir().unwrap();
        let key = hash_token("t1");
        std::fs::write(
            dir.path().join("sessions.json"),
            format!(r#"{{"{key}":["a@b.com"]}}"#),
        )
        .unwrap();
        // A one-element row has no `created_at`, so it reads as epoch 0 and
        // the email falls back to element 0 — the pre-v4 shape.
        let store = store(dir.path(), Clock::frozen(1_000.0));
        assert_eq!(store.get_email("t1").as_deref(), Some("a@b.com"));
        assert_eq!(store.get_subject("t1").as_deref(), Some("a@b.com"));

        // A zero TTL means "use the default" (`int(ttl or SESSION_TTL)`), not
        // "expire everything".
        let store = SessionStore::new(dir.path(), 0.0, Clock::frozen(10.0));
        assert_eq!(store.get_email("t1").as_deref(), Some("a@b.com"));

        // Past the 24h default it is gone. (The reads above slid the stamp
        // forward, which is why the clock has to move well past one TTL.)
        let store = SessionStore::new(dir.path(), SESSION_TTL, Clock::frozen(SESSION_TTL * 3.0));
        assert_eq!(store.get_email("t1"), None);
    }

    #[test]
    fn an_empty_third_element_falls_back_to_the_subject() {
        let entry = json!(["user:one", 5.0, ""]);
        assert_eq!(entry_email(&entry).as_deref(), Some("user:one"));
        assert_eq!(entry_created_at(&entry), 5.0);
        assert_eq!(entry_subject(&json!([])), None);
        assert_eq!(entry_email(&json!("nope")), None);
        assert_eq!(entry_created_at(&json!(["x"])), 0.0);
    }

    #[test]
    fn invalidate_removes_the_row() {
        let dir = tempfile::tempdir().unwrap();
        let store = store(dir.path(), Clock::frozen(1_000.0));
        let token = store.create("user:one", None);
        store.invalidate(&token);
        assert!(store.is_empty());
        store.invalidate("never-existed");
        assert_eq!(std::fs::read_to_string(store.path()).unwrap(), "{}");
    }

    #[test]
    fn an_unreadable_file_starts_empty() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("sessions.json"), "not json").unwrap();
        assert!(store(dir.path(), Clock::frozen(1.0)).is_empty());
    }

    #[test]
    fn hashed_key_detection_rejects_the_wrong_shapes() {
        assert!(looks_hashed(&hash_token("x")));
        assert!(!looks_hashed("short"));
        assert!(!looks_hashed(&"A".repeat(64)));
        assert!(!looks_hashed(&"z".repeat(64)));
    }
}
