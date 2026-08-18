//! `knowledge_save` / `obsidian_save` — the knowledge-garden writers.
//!
//! These write under `BRAIN_DIR` rather than the agent workspace, so the
//! confinement rule is a different one and it is worth naming: the folder must
//! be one of the five `STRUCTURE` names, and the vault root is a **per-scope**
//! partition derived from the authenticated workspace and user. The loop
//! overwrites `workspace_id`/`user_email` in the arguments before the call
//! (`SCOPED_KNOWLEDGE_TOOLS`), so a model cannot name someone else's partition;
//! the fail-closed check below is what happens when nobody set them at all.

use std::path::{Path, PathBuf};

use serde_json::{json, Map, Value};

use crate::parse::pystr::{char_slice, is_truthy, py_str};
use crate::tools::args;
use crate::tools::files::io_error;
use crate::tools::sandbox::{ToolError, MAX_FILE_BYTES};

/// `STRUCTURE`'s keys — the only folders a note may land in.
pub const KNOWLEDGE_FOLDERS: [&str; 5] =
    ["00_Raw", "10_Wiki", "20_Skills", "30_Projects", "40_Log"];

/// `BRAIN_DIR` — `$LATTICEAI_OBSIDIAN_VAULT_DIR`, then `$LATTICEAI_BRAIN_DIR`,
/// then `~/.ltcai-brain`.
pub fn default_brain_dir() -> PathBuf {
    for name in ["LATTICEAI_OBSIDIAN_VAULT_DIR", "LATTICEAI_BRAIN_DIR"] {
        if let Some(value) = std::env::var_os(name) {
            if !value.is_empty() {
                return PathBuf::from(value);
            }
        }
    }
    let home = std::env::var_os("HOME").unwrap_or_default();
    PathBuf::from(home).join(".ltcai-brain")
}

/// `_scope_digest`: `sha256(f"{kind}\0{value}")[:24]`, prefixed by the kind.
fn scope_digest(kind: &str, value: &str) -> String {
    use sha2::{Digest, Sha256};
    let mut hasher = Sha256::new();
    hasher.update(kind.as_bytes());
    hasher.update([0u8]);
    hasher.update(value.as_bytes());
    let hex = format!("{:x}", hasher.finalize());
    format!("{kind}-{}", &hex[..24])
}

/// The scope pair, or the fail-closed refusal `_knowledge_scope` raises.
fn scope_of(args: &Map<String, Value>) -> Result<(String, String), ToolError> {
    let workspace_id = args::coerced_str(args, "workspace_id", "")
        .trim()
        .to_string();
    let user_email = args::coerced_str(args, "user_email", "")
        .trim()
        .to_lowercase();
    if workspace_id.is_empty() || user_email.is_empty() {
        return Err(ToolError::tool(
            "Knowledge tool execution requires an authenticated workspace and user scope.",
        ));
    }
    Ok((workspace_id, user_email))
}

/// `knowledge_scope_root`: the private partition for one workspace user.
pub fn scope_root(brain_dir: &Path, workspace_id: &str, user_email: &str) -> PathBuf {
    brain_dir
        .join(".lattice-scopes")
        .join(scope_digest("workspace", workspace_id))
        .join(scope_digest("user", user_email))
}

/// `_safe_brain_folder`.
fn safe_folder(args: &Map<String, Value>) -> Result<String, ToolError> {
    let folder = match args.get("folder") {
        None => "00_Raw".to_string(),
        Some(value) => py_str(value),
    };
    if KNOWLEDGE_FOLDERS.contains(&folder.as_str()) {
        Ok(folder)
    } else {
        Err(ToolError::tool(format!(
            "Unknown knowledge folder: {folder}"
        )))
    }
}

/// `safe_title`: the caller's title or the first line, stripped of everything
/// that is not alphanumeric, space, `-` or `_`, then whitespace-joined.
///
/// The characters are **dropped**, not replaced (unlike the document creators'
/// `_safe_filename`, which substitutes `_`), so `"보고서: v2"` becomes
/// `"보고서_v2"` and `"???"` becomes `"note"`.
fn safe_title(title: Option<&str>, content: &str) -> Result<String, ToolError> {
    let base = match title {
        Some(title) if !title.is_empty() => title.to_string(),
        _ => {
            let stripped = content.trim();
            let Some(first) = stripped.lines().next() else {
                // `content.strip().splitlines()[0]` on whitespace-only content
                // raises IndexError, which the seam does not catch. Named as a
                // deviation: an error step rather than a 500.
                return Err(ToolError::tool("list index out of range"));
            };
            let first = char_slice(first, 60);
            if first.is_empty() {
                "note".to_string()
            } else {
                first.to_string()
            }
        }
    };
    let filtered: String = base
        .chars()
        .filter(|character| character.is_alphanumeric() || matches!(character, ' ' | '-' | '_'))
        .collect();
    let joined = filtered.split_whitespace().collect::<Vec<_>>().join("_");
    Ok(if joined.is_empty() {
        "note".to_string()
    } else {
        joined
    })
}

/// `knowledge_save(content, folder, title, *, workspace_id, user_email)`.
pub fn knowledge_save(brain_dir: &Path, args: &Map<String, Value>) -> Result<Value, ToolError> {
    let (result, _) = save_note(brain_dir, args)?;
    Ok(result)
}

/// `obsidian_save` — `knowledge_save` plus the vault's own two fields.
pub fn obsidian_save(brain_dir: &Path, args: &Map<String, Value>) -> Result<Value, ToolError> {
    let (mut result, root) = save_note(brain_dir, args)?;
    result["vault_root"] = json!(root.display().to_string());
    result["obsidian_uri_hint"] = json!(format!(
        "obsidian://open?path={}",
        result["path"].as_str().unwrap_or_default()
    ));
    Ok(result)
}

/// The shared body: both doors write the same note to the same place.
fn save_note(brain_dir: &Path, args: &Map<String, Value>) -> Result<(Value, PathBuf), ToolError> {
    // The handler reads `a["content"]` first, so a missing key is the KeyError
    // before any of the tool's own checks run.
    let content = args::required_str(args, "content")?;
    let (workspace_id, user_email) = scope_of(args)?;
    let folder = safe_folder(args)?;
    if content.is_empty() {
        return Err(ToolError::tool("Knowledge content is required."));
    }
    if content.len() as u64 > MAX_FILE_BYTES {
        return Err(ToolError::tool("Knowledge content is too large."));
    }

    let root = scope_root(brain_dir, &workspace_id, &user_email);
    let target_dir = root.join(&folder);
    std::fs::create_dir_all(&target_dir).map_err(io_error)?;

    let title = args
        .get("title")
        .filter(|value| is_truthy(value))
        .map(py_str);
    let stem = safe_title(title.as_deref(), &content)?;
    let mut target = target_dir.join(format!("{stem}.md"));
    let mut counter = 2;
    while target.exists() {
        target = target_dir.join(format!("{stem}_{counter}.md"));
        counter += 1;
    }
    std::fs::write(&target, &content).map_err(io_error)?;

    Ok((
        json!({
            "folder": folder,
            "filename": target
                .file_name()
                .map(|name| name.to_string_lossy().into_owned())
                .unwrap_or_default(),
            "path": target.display().to_string(),
        }),
        root,
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(value: Value) -> Map<String, Value> {
        let mut map = value.as_object().expect("object").clone();
        map.entry("workspace_id".to_string())
            .or_insert(json!("personal"));
        map.entry("user_email".to_string())
            .or_insert(json!("Owner@Example.com"));
        map
    }

    #[test]
    fn a_note_lands_in_its_scoped_folder_and_names_itself_from_the_first_line() {
        let dir = tempfile::tempdir().expect("tempdir");
        let result = knowledge_save(
            dir.path(),
            &args(json!({"content": "회의 메모: 첫 줄\n\n본문"})),
        )
        .expect("save");
        assert_eq!(result["folder"], "00_Raw");
        assert_eq!(result["filename"], "회의_메모_첫_줄.md");
        let path = result["path"].as_str().expect("path");
        assert!(path.contains("/.lattice-scopes/workspace-"), "{path}");
        assert!(path.contains("/user-"), "{path}");
        assert_eq!(
            std::fs::read_to_string(path).expect("read"),
            "회의 메모: 첫 줄\n\n본문"
        );
    }

    #[test]
    fn the_scope_partition_is_the_python_digest() {
        // Independently computed: sha256("workspace\0personal") and
        // sha256("user\0owner@example.com"), first 24 hex characters.
        assert_eq!(
            scope_digest("workspace", "personal"),
            format!("workspace-{}", &sha256_hex("workspace\0personal")[..24])
        );
        let root = scope_root(Path::new("/brain"), "personal", "owner@example.com");
        assert_eq!(
            root,
            Path::new("/brain")
                .join(".lattice-scopes")
                .join(scope_digest("workspace", "personal"))
                .join(scope_digest("user", "owner@example.com"))
        );
        // The email is lowercased before it is hashed, so one user is one
        // partition however they typed their address.
        let dir = tempfile::tempdir().expect("tempdir");
        let first = knowledge_save(dir.path(), &args(json!({"content": "a"}))).expect("a");
        let mut upper = args(json!({"content": "b"}));
        upper.insert("user_email".into(), json!("OWNER@EXAMPLE.COM"));
        let second = knowledge_save(dir.path(), &upper).expect("b");
        let parent = |value: &Value| {
            PathBuf::from(value["path"].as_str().expect("path"))
                .parent()
                .expect("parent")
                .to_path_buf()
        };
        assert_eq!(parent(&first), parent(&second));
    }

    fn sha256_hex(text: &str) -> String {
        use sha2::{Digest, Sha256};
        format!("{:x}", Sha256::digest(text.as_bytes()))
    }

    #[test]
    fn a_repeated_title_gets_a_counter_rather_than_an_overwrite() {
        let dir = tempfile::tempdir().expect("tempdir");
        let first = knowledge_save(
            dir.path(),
            &args(json!({"content": "note", "title": "일지"})),
        )
        .expect("first");
        let second = knowledge_save(
            dir.path(),
            &args(json!({"content": "other", "title": "일지"})),
        )
        .expect("second");
        assert_eq!(first["filename"], "일지.md");
        assert_eq!(second["filename"], "일지_2.md");
        assert_eq!(
            std::fs::read_to_string(first["path"].as_str().expect("path")).expect("read"),
            "note",
            "the first note is untouched"
        );
    }

    #[test]
    fn obsidian_save_adds_the_vault_root_and_the_uri_hint() {
        let dir = tempfile::tempdir().expect("tempdir");
        let result =
            obsidian_save(dir.path(), &args(json!({"content": "x", "title": "n"}))).expect("save");
        let path = result["path"].as_str().expect("path");
        assert_eq!(
            result["obsidian_uri_hint"],
            json!(format!("obsidian://open?path={path}"))
        );
        assert!(path.starts_with(result["vault_root"].as_str().expect("root")));
    }

    #[test]
    fn the_refusals_are_the_python_messages() {
        let dir = tempfile::tempdir().expect("tempdir");
        assert_eq!(
            knowledge_save(dir.path(), &args(json!({})))
                .expect_err("missing")
                .message,
            "'content'"
        );
        assert_eq!(
            knowledge_save(dir.path(), &args(json!({"content": ""})))
                .expect_err("empty")
                .message,
            "Knowledge content is required."
        );
        assert_eq!(
            knowledge_save(
                dir.path(),
                &args(json!({"content": "x", "folder": "99_Nope"}))
            )
            .expect_err("folder")
            .message,
            "Unknown knowledge folder: 99_Nope"
        );
        assert_eq!(
            knowledge_save(dir.path(), &args(json!({"content": "x", "folder": null})))
                .expect_err("folder")
                .message,
            "Unknown knowledge folder: None"
        );
        let big = "x".repeat(MAX_FILE_BYTES as usize + 1);
        assert_eq!(
            knowledge_save(dir.path(), &args(json!({"content": big})))
                .expect_err("too large")
                .message,
            "Knowledge content is too large."
        );
    }

    #[test]
    fn an_unscoped_call_fails_closed_instead_of_using_the_shared_vault() {
        let dir = tempfile::tempdir().expect("tempdir");
        for scope in [
            json!({"content": "x"}),
            json!({"content": "x", "workspace_id": "personal"}),
            json!({"content": "x", "user_email": "a@b.c"}),
            json!({"content": "x", "workspace_id": "  ", "user_email": "a@b.c"}),
        ] {
            let map = scope.as_object().expect("object").clone();
            assert_eq!(
                knowledge_save(dir.path(), &map)
                    .expect_err("unscoped")
                    .message,
                "Knowledge tool execution requires an authenticated workspace and user scope."
            );
        }
    }

    #[test]
    fn a_title_of_only_punctuation_falls_back_to_note() {
        assert_eq!(safe_title(Some("???"), "body").expect("title"), "note");
        assert_eq!(
            safe_title(None, "  \n  ").expect_err("empty").message,
            "list index out of range"
        );
        assert_eq!(
            safe_title(None, &format!("{}\nrest", "가".repeat(80))).expect("title"),
            "가".repeat(60),
            "the derived title is capped at 60 characters, not bytes"
        );
        assert_eq!(safe_title(Some("a  b"), "").expect("title"), "a_b");
    }

    #[test]
    fn the_folder_set_is_the_python_structure() {
        assert_eq!(KNOWLEDGE_FOLDERS.len(), 5);
        for folder in ["00_Raw", "10_Wiki", "20_Skills", "30_Projects", "40_Log"] {
            assert!(KNOWLEDGE_FOLDERS.contains(&folder), "{folder}");
        }
    }
}
