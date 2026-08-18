//! Durable persistence for paused agent runs.
//!
//! A port of `latticeai.core.run_store`. The `awaiting_approval` loop parks a
//! run until a human says yes, and a restart between pause and resume used to
//! turn every outstanding approval into a 404. One JSON file per run fixes that
//! across restarts and across processes.
//!
//! The design constraints are the Python ones, and each is load-bearing:
//!
//! * **Atomic replace on write** — a half-written record must never be a
//!   resumable one.
//! * **Fail-open on save, fail-closed on resume.** A persistence error never
//!   breaks the pause response; a load error on resume is "run not found".
//! * **Tokens are never stored in plaintext.** Only the SHA-256 digest is
//!   written, and resume compares digests.
//! * **Wall-clock expiry**, because a monotonic deadline does not survive a
//!   restart.
//!
//! ## Where the files live, and why not next to Python's
//!
//! `LATTICEAI_AGENT_RUNS_DIR` when set, otherwise `<data dir>/rust_agent_runs`
//! — deliberately **not** `<data dir>/agent_runs`, which is Python's. The two
//! stores share a record schema but not a runtime: a run paused by the Python
//! loop carries a context that only the Python loop can resume (its prompts,
//! its ports, its in-flight `AgentRequest`), and the same is true in reverse.
//! Sharing the directory would let either side list, hand out and consume the
//! other's in-flight runs, and the failure mode is silent — a resume that
//! restores a context the runtime cannot honour. Separate directories make the
//! two stores exactly as independent as the two runtimes are.

use std::path::{Path, PathBuf};

use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::kernel::state::AgentRunContext;
use crate::kernel::trace::{epoch_now, utc_iso_seconds};

/// Environment override for the store directory.
pub const RUNS_DIR_ENV: &str = "LATTICEAI_AGENT_RUNS_DIR";
/// Subdirectory under the data dir. See the module docs for why it is not
/// `agent_runs`.
pub const RUNS_DIR_NAME: &str = "rust_agent_runs";
/// How long an approval token is good for.
pub const APPROVAL_TTL_SECONDS: f64 = 600.0;
/// How long an *expired* record is kept so resume can answer 410 with a replan
/// hint instead of a bare 404.
pub const RETENTION_SECONDS: f64 = 86_400.0;
/// The record schema this store writes and reads.
pub const RECORD_VERSION: u64 = 1;

/// `secrets.token_urlsafe` — `bytes` random bytes, base64url, unpadded.
pub fn token_urlsafe(bytes: usize) -> std::io::Result<String> {
    let mut buffer = vec![0u8; bytes];
    getrandom::fill(&mut buffer)
        .map_err(|err| std::io::Error::other(format!("no system randomness: {err}")))?;
    Ok(URL_SAFE_NO_PAD.encode(&buffer))
}

/// Stable digest used both at save and at resume comparison time.
pub fn hash_approval_token(token: &str) -> String {
    let digest = Sha256::digest(token.as_bytes());
    digest.iter().map(|byte| format!("{byte:02x}")).collect()
}

/// Constant-time comparison, so a resume cannot be brute-forced by timing.
pub fn token_matches(stored_hash: &str, supplied: &str) -> bool {
    if supplied.is_empty() || stored_hash.is_empty() {
        return false;
    }
    let candidate = hash_approval_token(supplied);
    if candidate.len() != stored_hash.len() {
        return false;
    }
    candidate
        .bytes()
        .zip(stored_hash.bytes())
        .fold(0u8, |difference, (left, right)| difference | (left ^ right))
        == 0
}

/// `run_id` comes from `token_urlsafe` — validated before any path use so a
/// crafted id can never traverse out of the store directory.
fn valid_run_id(run_id: &str) -> bool {
    let length = run_id.len();
    (8..=64).contains(&length)
        && run_id
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_' || byte == b'-')
}

/// The directory this process stores paused runs in.
pub fn default_runs_dir() -> PathBuf {
    let configured = std::env::var(RUNS_DIR_ENV).unwrap_or_default();
    if !configured.trim().is_empty() {
        return PathBuf::from(configured.trim());
    }
    let data_dir = std::env::var("LATTICEAI_DATA_DIR").unwrap_or_default();
    let base = if data_dir.trim().is_empty() {
        std::env::var_os("HOME")
            .map(PathBuf::from)
            .filter(|home| !home.as_os_str().is_empty())
            .map_or_else(|| PathBuf::from(".ltcai"), |home| home.join(".ltcai"))
    } else {
        PathBuf::from(data_dir.trim())
    };
    base.join(RUNS_DIR_NAME)
}

/// One-JSON-file-per-run persistence for paused approval runs.
#[derive(Debug, Clone)]
pub struct AgentRunStore {
    root: PathBuf,
}

/// Everything a pause needs to persist besides the context itself.
#[derive(Debug, Clone)]
pub struct PausedRun<'a> {
    pub run_id: &'a str,
    pub user: &'a str,
    pub language_hint: &'a str,
    pub token: &'a str,
    pub expires_epoch: f64,
    pub expires_at: &'a str,
    pub legacy_context: bool,
    pub request: Value,
}

impl AgentRunStore {
    pub fn new(root: impl Into<PathBuf>) -> Self {
        Self { root: root.into() }
    }

    /// The store this process uses by default.
    pub fn from_env() -> Self {
        Self::new(default_runs_dir())
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    fn path_for(&self, run_id: &str) -> Option<PathBuf> {
        valid_run_id(run_id).then(|| self.root.join(format!("{run_id}.json")))
    }

    /// Persist a paused run. Best-effort: `false` on any failure.
    pub fn save(&self, run: &PausedRun<'_>, ctx: &AgentRunContext) -> bool {
        let Some(path) = self.path_for(run.run_id) else {
            return false;
        };
        let record = json!({
            "version": RECORD_VERSION,
            "run_id": run.run_id,
            "user": run.user,
            "language_hint": run.language_hint,
            "token_hash": hash_approval_token(run.token),
            "expires_epoch": run.expires_epoch,
            "expires_at": run.expires_at,
            "legacy_context": run.legacy_context,
            "req": run.request,
            "ctx": ctx.serialize(),
        });
        self.write_atomic(&path, &record).is_ok()
    }

    /// `mkstemp` in the store directory, then `os.replace` — a reader either
    /// sees the old record or the whole new one, never a partial write.
    fn write_atomic(&self, path: &Path, record: &Value) -> std::io::Result<()> {
        std::fs::create_dir_all(&self.root)?;
        let name = path
            .file_name()
            .map(|name| name.to_string_lossy().into_owned())
            .unwrap_or_else(|| "run".into());
        let suffix = token_urlsafe(9)?;
        let temporary = self.root.join(format!("{name}.{suffix}.tmp"));
        let body = serde_json::to_vec(record).map_err(std::io::Error::other)?;
        if let Err(error) = std::fs::write(&temporary, &body) {
            let _ = std::fs::remove_file(&temporary);
            return Err(error);
        }
        if let Err(error) = std::fs::rename(&temporary, path) {
            let _ = std::fs::remove_file(&temporary);
            return Err(error);
        }
        Ok(())
    }

    pub fn delete(&self, run_id: &str) {
        if let Some(path) = self.path_for(run_id) {
            let _ = std::fs::remove_file(path);
        }
    }

    /// Raw persisted record, or `None` when missing / corrupt / an invalid id.
    pub fn load(&self, run_id: &str) -> Option<Value> {
        let path = self.path_for(run_id)?;
        let raw = std::fs::read_to_string(path).ok()?;
        let record: Value = serde_json::from_str(&raw).ok()?;
        (record.is_object() && record.get("run_id") == Some(&json!(run_id))).then_some(record)
    }

    /// Unexpired pending runs (optionally for one user), sorted by run id.
    pub fn pending_summaries(&self, user: Option<&str>) -> Vec<Value> {
        let now = epoch_now();
        let mut names: Vec<PathBuf> = match std::fs::read_dir(&self.root) {
            Ok(entries) => entries
                .filter_map(Result::ok)
                .map(|entry| entry.path())
                .filter(|path| path.extension().is_some_and(|ext| ext == "json"))
                .collect(),
            Err(_) => return Vec::new(),
        };
        names.sort();
        let mut summaries = Vec::new();
        for path in names {
            let Some(record) = std::fs::read_to_string(&path)
                .ok()
                .and_then(|raw| serde_json::from_str::<Value>(&raw).ok())
                .filter(Value::is_object)
            else {
                continue;
            };
            if record
                .get("expires_epoch")
                .and_then(Value::as_f64)
                .unwrap_or(0.0)
                <= now
            {
                continue;
            }
            if let Some(user) = user {
                if record.get("user") != Some(&json!(user)) {
                    continue;
                }
            }
            let goal = record["ctx"]["plan"]["goal"].as_str().unwrap_or_default();
            summaries.push(json!({
                "run_id": record.get("run_id").cloned().unwrap_or(Value::Null),
                "user": record.get("user").cloned().unwrap_or(Value::Null),
                "goal": crate::parse::pystr::char_slice(goal, 200),
                "expires_at": record.get("expires_at").cloned().unwrap_or(Value::Null),
            }));
        }
        summaries
    }

    /// Remove long-expired run files; returns how many were removed.
    ///
    /// Recently expired records are *kept* for [`RETENTION_SECONDS`] so a
    /// resume after expiry can still answer 410 with a replan hint.
    pub fn sweep_expired(&self, now_epoch: Option<f64>) -> usize {
        let now = now_epoch.unwrap_or_else(epoch_now);
        let Ok(entries) = std::fs::read_dir(&self.root) else {
            return 0;
        };
        let mut removed = 0;
        for path in entries
            .filter_map(Result::ok)
            .map(|entry| entry.path())
            .filter(|path| path.extension().is_some_and(|ext| ext == "json"))
        {
            // An unreadable record is expired garbage, not a record to keep.
            let expires = std::fs::read_to_string(&path)
                .ok()
                .and_then(|raw| serde_json::from_str::<Value>(&raw).ok())
                .and_then(|record| record.get("expires_epoch").and_then(Value::as_f64))
                .unwrap_or(-RETENTION_SECONDS);
            if expires + RETENTION_SECONDS <= now && std::fs::remove_file(&path).is_ok() {
                removed += 1;
            }
        }
        removed
    }
}

/// The 410 body a resume after expiry answers with.
pub fn approval_expired_payload(message: &str) -> Value {
    json!({
        "error": "approval_expired",
        "message": "Approval token expired. Start a new request.",
        "replan": {"message": message},
    })
}

/// `(expires_epoch, expires_at)` for a pause starting now.
pub fn approval_deadline() -> (f64, String) {
    let expires = epoch_now() + APPROVAL_TTL_SECONDS;
    (
        expires,
        format!("{}+00:00", utc_iso_seconds(expires as i64)),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::kernel::state::AgentState;

    fn store() -> (tempfile::TempDir, AgentRunStore) {
        let dir = tempfile::tempdir().expect("tempdir");
        let store = AgentRunStore::new(dir.path().join("rust_agent_runs"));
        (dir, store)
    }

    fn context() -> AgentRunContext {
        let mut ctx = AgentRunContext::new();
        ctx.state = AgentState::WaitingApproval;
        ctx.plan = json!({"goal": "write the note", "steps": []})
            .as_object()
            .expect("plan")
            .clone();
        ctx
    }

    fn paused<'a>(run_id: &'a str, token: &'a str, expires: f64) -> PausedRun<'a> {
        PausedRun {
            run_id,
            user: "owner@example.com",
            language_hint: "Korean",
            token,
            expires_epoch: expires,
            expires_at: "2026-08-11T00:10:00+00:00",
            legacy_context: false,
            request: json!({"message": "write the note"}),
        }
    }

    #[test]
    fn a_saved_run_round_trips_through_the_record_schema() {
        let (_dir, store) = store();
        let ctx = context();
        assert!(store.save(
            &paused("run-abcdefgh", "secret-token", epoch_now() + 600.0),
            &ctx
        ));
        let record = store.load("run-abcdefgh").expect("record");
        assert_eq!(record["version"], 1);
        assert_eq!(record["run_id"], "run-abcdefgh");
        assert_eq!(record["user"], "owner@example.com");
        assert_eq!(record["language_hint"], "Korean");
        assert_eq!(record["legacy_context"], false);
        assert_eq!(record["req"]["message"], "write the note");
        assert_eq!(record["ctx"]["state"], "WAITING_APPROVAL");
        let restored = AgentRunContext::restore(&record["ctx"]);
        assert_eq!(restored.serialize(), ctx.serialize());
    }

    #[test]
    fn the_token_is_stored_as_a_digest_and_compared_as_one() {
        let (_dir, store) = store();
        store.save(
            &paused("run-abcdefgh", "secret-token", epoch_now() + 600.0),
            &context(),
        );
        let record = store.load("run-abcdefgh").expect("record");
        let stored = record["token_hash"].as_str().expect("hash");
        assert_eq!(stored.len(), 64, "sha256 hex");
        assert!(
            !record.to_string().contains("secret-token"),
            "never plaintext"
        );
        assert!(token_matches(stored, "secret-token"));
        assert!(!token_matches(stored, "secret-token "));
        assert!(!token_matches(stored, ""));
        assert!(!token_matches("", "secret-token"));
    }

    #[test]
    fn the_digest_is_the_one_hashlib_produces() {
        // Both read off `hashlib.sha256(...).hexdigest()`.
        assert_eq!(
            hash_approval_token("secret-token"),
            "930bbdc51b6aed5c2a5678fd6e28dee7a05e8a4b643cfc0b4427c3efb86c0d94"
        );
        assert_eq!(
            hash_approval_token(""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
    }

    #[test]
    fn a_crafted_run_id_can_never_name_a_path() {
        let (_dir, store) = store();
        for bad in [
            "../escape",
            "short",
            "with/slash",
            "a".repeat(65).as_str(),
            "",
        ] {
            assert!(
                !store.save(&paused(bad, "t", epoch_now() + 600.0), &context()),
                "{bad}"
            );
            assert_eq!(store.load(bad), None, "{bad}");
            store.delete(bad);
        }
    }

    #[test]
    fn a_missing_or_corrupt_record_is_not_found_rather_than_an_error() {
        let (_dir, store) = store();
        assert_eq!(store.load("run-abcdefgh"), None);
        std::fs::create_dir_all(store.root()).expect("dir");
        std::fs::write(store.root().join("run-abcdefgh.json"), b"{not json").expect("write");
        assert_eq!(store.load("run-abcdefgh"), None);
        // A record whose id does not match its filename is not that record.
        std::fs::write(
            store.root().join("run-abcdefgh.json"),
            br#"{"run_id": "run-somethingelse"}"#,
        )
        .expect("write");
        assert_eq!(store.load("run-abcdefgh"), None);
    }

    #[test]
    fn pending_summaries_skip_the_expired_and_other_users() {
        let (_dir, store) = store();
        let now = epoch_now();
        store.save(&paused("run-aaaaaaaa", "t1", now + 600.0), &context());
        store.save(&paused("run-bbbbbbbb", "t2", now - 1.0), &context());
        let mut other = paused("run-cccccccc", "t3", now + 600.0);
        other.user = "someone@else.com";
        store.save(&other, &context());

        let mine = store.pending_summaries(Some("owner@example.com"));
        assert_eq!(mine.len(), 1);
        assert_eq!(mine[0]["run_id"], "run-aaaaaaaa");
        assert_eq!(mine[0]["goal"], "write the note");
        assert_eq!(
            store.pending_summaries(None).len(),
            2,
            "both unexpired runs"
        );
    }

    #[test]
    fn a_missing_store_directory_lists_nothing_instead_of_failing() {
        let (_dir, store) = store();
        assert!(store.pending_summaries(None).is_empty());
        assert_eq!(store.sweep_expired(None), 0);
    }

    #[test]
    fn the_sweep_keeps_a_recently_expired_record_so_resume_can_say_410() {
        let (_dir, store) = store();
        let now = epoch_now();
        store.save(&paused("run-recent00", "t", now - 10.0), &context());
        store.save(
            &paused("run-ancient0", "t", now - RETENTION_SECONDS - 10.0),
            &context(),
        );
        assert_eq!(store.sweep_expired(Some(now)), 1);
        assert!(
            store.load("run-recent00").is_some(),
            "still answerable as 410"
        );
        assert!(store.load("run-ancient0").is_none());
    }

    #[test]
    fn an_unreadable_file_is_swept_as_garbage() {
        let (_dir, store) = store();
        std::fs::create_dir_all(store.root()).expect("dir");
        std::fs::write(store.root().join("run-garbage0.json"), b"nonsense").expect("write");
        std::fs::write(store.root().join("not-a-record.txt"), b"nonsense").expect("write");
        assert_eq!(store.sweep_expired(Some(epoch_now())), 1);
        assert!(
            store.root().join("not-a-record.txt").exists(),
            "only .json is ours"
        );
    }

    #[test]
    fn a_write_leaves_no_temporary_behind() {
        let (_dir, store) = store();
        store.save(
            &paused("run-abcdefgh", "t", epoch_now() + 600.0),
            &context(),
        );
        let names: Vec<String> = std::fs::read_dir(store.root())
            .expect("dir")
            .filter_map(Result::ok)
            .map(|entry| entry.file_name().to_string_lossy().into_owned())
            .collect();
        assert_eq!(names, vec!["run-abcdefgh.json"]);
    }

    #[test]
    fn tokens_are_url_safe_and_the_length_python_produces() {
        let run_id = token_urlsafe(16).expect("random");
        let token = token_urlsafe(32).expect("random");
        assert_eq!(run_id.len(), 22, "16 bytes, base64url, unpadded");
        assert_eq!(token.len(), 43);
        assert!(valid_run_id(&run_id));
        assert!(run_id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_'));
        assert_ne!(run_id, token_urlsafe(16).expect("random"), "not a counter");
    }

    #[test]
    fn the_expiry_payload_carries_the_replan_hint() {
        assert_eq!(
            approval_expired_payload("write the note"),
            json!({
                "error": "approval_expired",
                "message": "Approval token expired. Start a new request.",
                "replan": {"message": "write the note"},
            })
        );
        let (epoch, at) = approval_deadline();
        assert!(epoch > epoch_now() + 500.0 && epoch <= epoch_now() + 600.0);
        assert!(at.ends_with("+00:00"), "{at}");
        assert_eq!(at.len(), "2026-08-11T00:10:00+00:00".len());
    }

    #[test]
    fn the_store_directory_is_not_pythons() {
        // Two runtimes must not deserialize each other's in-flight runs.
        let resolved = default_runs_dir();
        assert!(resolved.ends_with(RUNS_DIR_NAME), "{resolved:?}");
        assert!(!resolved.ends_with("agent_runs"));
        assert_eq!(RUNS_DIR_NAME, "rust_agent_runs");
        assert_eq!(APPROVAL_TTL_SECONDS, 600.0);
        assert_eq!(RETENTION_SECONDS, 86_400.0);
    }
}
