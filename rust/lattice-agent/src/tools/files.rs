//! `latticeai.tools.filesystem`'s writers, natively.
//!
//! Three handlers: `write_file`, `edit_file`, `todo_write`. Every path goes
//! through [`Workspace::resolve`] — the same resolver the loop's snapshot and
//! overwrite guard use — so there is one containment rule for the whole crate
//! and no raw path ever reaches `std::fs`.
//!
//! The refusals are the Python strings verbatim, in the Python order. The order
//! matters: `edit_file` compares `old_string` to `new_string` *before* it
//! resolves the path, so a no-op edit on an escaping path reports the no-op.

use serde::Serialize;
use serde_json::{json, Map, Value};

use crate::parse::pystr::{char_slice, is_truthy, py_str};
use crate::tools::args;
use crate::tools::sandbox::{ToolError, Workspace, MAX_FILE_BYTES};

/// Where `todo_write` persists, relative to the workspace root.
pub const TODO_REL_PATH: &str = ".lattice/todos.json";

/// `_TODO_ALLOWED_STATUS`, sorted — Python prints `sorted(...)` in its refusal.
const TODO_ALLOWED_STATUS: [&str; 3] = ["completed", "in_progress", "pending"];

/// The largest list `todo_write` accepts.
const MAX_TODOS: usize = 50;

/// `write_file(path, content)`.
///
/// The content passes [`crate::content::sanitize::sanitize_write_content`] before it
/// reaches a disk (v11.7.0). Python ran that pipeline in the *loop* only, so a
/// direct dispatch — `/agent/tool`, a hook, the harness — wrote whatever the
/// model produced; this is the door that closes. Content that already validates
/// is returned byte-for-byte, so nothing a person authored is touched, and the
/// loop's own pass (`agentloop::gates`) is an identity transform when it gets
/// here. The **result shape stays `{path, bytes}`**: the transcript's
/// `content_sanitize` is the loop's to record, and a new key here would change
/// what every existing reader of a tool result sees.
pub fn write_file(workspace: &Workspace, args: &Map<String, Value>) -> Result<Value, ToolError> {
    let path = args::required_str(args, "path")?;
    let content = args::optional_str(args, "content", "")?;
    // Missing/empty content must not land as a 0-byte success. Aliases
    // (`content_source`/`text`/`body`) are skipped: live tape used
    // `content_source` as a path, not the file text.
    if content.is_empty() {
        return Err(ToolError::tool(
            "write_file needs args.content (the full file text). Nothing was written.",
        ));
    }
    let target = workspace.resolve(&path)?;
    let (content, _sanitize) =
        crate::content::sanitize::sanitize_write_content(&path, &content, "");
    if content.len() as u64 > MAX_FILE_BYTES {
        return Err(ToolError::tool("Content is too large to write."));
    }
    if let Some(parent) = target.parent() {
        std::fs::create_dir_all(parent).map_err(io_error)?;
    }
    std::fs::write(&target, &content).map_err(io_error)?;
    Ok(json!({
        "path": workspace.relative(&target),
        "bytes": file_size(&target),
    }))
}

/// `edit_file(path, old_string, new_string, replace_all=False)`.
///
/// **Not** sanitized, which is Python's scope and not an oversight:
/// `new_string` is a fragment spliced into a file the user already has, so it
/// has no file type to validate against and no document shape to repair to.
/// Running the write-side pipeline over a replacement would judge a line of
/// CSS as if it were a stylesheet and "repair" it into one.
pub fn edit_file(workspace: &Workspace, args: &Map<String, Value>) -> Result<Value, ToolError> {
    let path = args::required_str(args, "path")?;
    let old = args::required_str(args, "old_string")?;
    let new = args::required_str(args, "new_string")?;
    let replace_all = args.get("replace_all").is_some_and(is_truthy);
    if old == new {
        return Err(ToolError::tool(
            "old_string and new_string are identical; nothing to change.",
        ));
    }
    let target = workspace.resolve(&path)?;
    if !target.is_file() {
        return Err(ToolError::tool("File does not exist."));
    }
    if file_size(&target) > MAX_FILE_BYTES {
        return Err(ToolError::tool("File is too large to edit."));
    }
    let original = std::fs::read_to_string(&target).map_err(io_error)?;

    let occurrences = original.matches(&old).count();
    if occurrences == 0 {
        return Err(ToolError::tool(
            "old_string not found in file. Read the file first and copy the exact bytes (including whitespace).",
        ));
    }
    if occurrences > 1 && !replace_all {
        return Err(ToolError::tool(format!(
            "old_string is ambiguous: appears {occurrences} times. Add more context to make it unique, or pass replace_all=true."
        )));
    }
    let updated = if replace_all {
        original.replace(&old, &new)
    } else {
        original.replacen(&old, &new, 1)
    };
    if updated.len() as u64 > MAX_FILE_BYTES {
        return Err(ToolError::tool(
            "Resulting file would exceed the workspace size limit.",
        ));
    }
    std::fs::write(&target, &updated).map_err(io_error)?;

    // `original[: original.find(old_string)].count("\n") + 1` — the newlines
    // before the first occurrence. Byte or character index makes no difference
    // to a newline count, so `str::find`'s byte offset is the same answer.
    let prefix = original.find(&old).unwrap_or(0);
    let edited_line = original[..prefix].matches('\n').count() + 1;
    Ok(json!({
        "path": workspace.relative(&target),
        "replacements": if replace_all { occurrences } else { 1 },
        "bytes": file_size(&target),
        "first_edit_line": edited_line,
    }))
}

/// One cleaned todo. The field **order** is the file's key order: Python builds
/// the dict as `{id, content, status}` and `json.dumps` writes insertion order,
/// so a `BTreeMap` here would rewrite every existing todo file on first save.
#[derive(Debug, Clone, Serialize)]
struct Todo {
    id: String,
    content: String,
    status: String,
}

/// `todo_write(todos)`.
pub fn todo_write(workspace: &Workspace, args: &Map<String, Value>) -> Result<Value, ToolError> {
    let raw = args::truthy_value(args, "todos")
        .cloned()
        .unwrap_or(json!([]));
    let Some(items) = raw.as_array() else {
        return Err(ToolError::tool("todos must be a list."));
    };
    if items.len() > MAX_TODOS {
        return Err(ToolError::tool(
            "Too many todos (max 50). Split into smaller batches.",
        ));
    }

    let mut cleaned: Vec<Todo> = Vec::with_capacity(items.len());
    let mut in_progress = 0usize;
    for (index, item) in items.iter().enumerate() {
        let number = index + 1;
        let Some(fields) = item.as_object() else {
            return Err(ToolError::tool(format!("Todo #{number} is not an object.")));
        };
        let content = args::coerced_str(fields, "content", "").trim().to_string();
        if content.is_empty() {
            return Err(ToolError::tool(format!(
                "Todo #{number} is missing 'content'."
            )));
        }
        let status = args::coerced_str(fields, "status", "pending")
            .trim()
            .to_lowercase();
        if !TODO_ALLOWED_STATUS.contains(&status.as_str()) {
            return Err(ToolError::tool(format!(
                "Todo #{number} has invalid status '{status}'. Use one of {}.",
                crate::parse::pystr::py_list_repr(&TODO_ALLOWED_STATUS.map(String::from))
            )));
        }
        if status == "in_progress" {
            in_progress += 1;
        }
        cleaned.push(Todo {
            id: match fields.get("id").filter(|value| is_truthy(value)) {
                Some(value) => py_str(value),
                None => number.to_string(),
            },
            content: char_slice(&content, 240).to_string(),
            status,
        });
    }

    let target = workspace.root().join(TODO_REL_PATH);
    if let Some(parent) = target.parent() {
        std::fs::create_dir_all(parent).map_err(io_error)?;
    }
    // `json.dumps(cleaned, ensure_ascii=False, indent=2)`: two-space indent, no
    // escaped non-ASCII, no trailing newline. `to_string_pretty` is all three.
    let body = serde_json::to_string_pretty(&cleaned)
        .map_err(|error| ToolError::tool(error.to_string()))?;
    std::fs::write(&target, body).map_err(io_error)?;

    Ok(json!({
        "todos": cleaned,
        "path": TODO_REL_PATH,
        "warning": if in_progress > 1 {
            json!("More than one todo is in_progress; keep only one active at a time.")
        } else {
            Value::Null
        },
    }))
}

/// `target.stat().st_size`, and zero for a file that vanished under us.
pub(crate) fn file_size(path: &std::path::Path) -> u64 {
    std::fs::metadata(path).map(|meta| meta.len()).unwrap_or(0)
}

/// An `OSError` Python would let escape as a 500. Named as a deviation in the
/// wiring note: the loop records an error step and the run continues.
pub(crate) fn io_error(error: std::io::Error) -> ToolError {
    ToolError::tool(error.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn workspace() -> (tempfile::TempDir, Workspace) {
        let dir = tempfile::tempdir().expect("tempdir");
        let workspace = Workspace::new(dir.path().join("agent_workspace")).expect("workspace");
        (dir, workspace)
    }

    fn args(value: Value) -> Map<String, Value> {
        value.as_object().expect("object").clone()
    }

    #[test]
    fn a_write_creates_missing_parents_and_reports_the_size_on_disk() {
        let (_dir, workspace) = workspace();
        let result = write_file(
            &workspace,
            &args(json!({"path": "notes/deep/a.md", "content": "한글\n"})),
        )
        .expect("write");
        assert_eq!(result["path"], "notes/deep/a.md");
        assert_eq!(result["bytes"], 7, "bytes, not characters");
        assert_eq!(
            std::fs::read_to_string(workspace.root().join("notes/deep/a.md")).expect("read"),
            "한글\n"
        );
    }

    #[test]
    fn a_write_with_missing_content_is_a_corrective_error_and_writes_nothing() {
        let (_dir, workspace) = workspace();
        let error = write_file(
            &workspace,
            &args(json!({"path": "notes/summary.md", "content_source": "README.md"})),
        )
        .expect_err("missing content");
        assert_eq!(
            error.message,
            "write_file needs args.content (the full file text). Nothing was written."
        );
        assert!(
            !workspace.root().join("notes/summary.md").exists(),
            "nothing was written"
        );
        assert!(
            !workspace.root().join("notes").exists(),
            "parents were not created either"
        );
    }

    #[test]
    fn a_write_with_empty_content_is_a_corrective_error_and_writes_nothing() {
        let (_dir, workspace) = workspace();
        std::fs::write(workspace.root().join("keep.md"), "keep").expect("seed");
        let error = write_file(&workspace, &args(json!({"path": "keep.md", "content": ""})))
            .expect_err("empty content");
        assert_eq!(
            error.message,
            "write_file needs args.content (the full file text). Nothing was written."
        );
        assert_eq!(
            std::fs::read_to_string(workspace.root().join("keep.md")).expect("read"),
            "keep",
            "existing file was not overwritten"
        );
    }

    #[test]
    fn a_write_is_refused_for_the_size_cap_and_for_an_escape() {
        let (_dir, workspace) = workspace();
        let huge = "x".repeat(MAX_FILE_BYTES as usize + 1);
        assert_eq!(
            write_file(&workspace, &args(json!({"path": "a.md", "content": huge})))
                .expect_err("too large")
                .message,
            "Content is too large to write."
        );
        assert_eq!(
            write_file(
                &workspace,
                &args(json!({"path": "../escape.md", "content": "x"}))
            )
            .expect_err("escape")
            .message,
            "Path escapes the agent workspace."
        );
        assert!(
            !workspace.root().join("a.md").exists(),
            "nothing was written"
        );
    }

    #[test]
    fn an_edit_replaces_once_and_reports_where() {
        let (_dir, workspace) = workspace();
        std::fs::write(workspace.root().join("a.md"), "one\ntwo\nthree\n").expect("seed");
        let result = edit_file(
            &workspace,
            &args(json!({"path": "a.md", "old_string": "two", "new_string": "2"})),
        )
        .expect("edit");
        assert_eq!(result["replacements"], 1);
        assert_eq!(result["first_edit_line"], 2);
        assert_eq!(result["bytes"], 12);
        assert_eq!(
            std::fs::read_to_string(workspace.root().join("a.md")).expect("read"),
            "one\n2\nthree\n"
        );
    }

    #[test]
    fn an_ambiguous_edit_is_refused_unless_replace_all() {
        let (_dir, workspace) = workspace();
        std::fs::write(workspace.root().join("a.md"), "x\nx\nx\n").expect("seed");
        let ambiguous = args(json!({"path": "a.md", "old_string": "x", "new_string": "y"}));
        assert_eq!(
            edit_file(&workspace, &ambiguous).expect_err("ambiguous").message,
            "old_string is ambiguous: appears 3 times. Add more context to make it unique, or pass replace_all=true."
        );
        let mut all = ambiguous.clone();
        all.insert("replace_all".into(), json!(true));
        let result = edit_file(&workspace, &all).expect("replace all");
        assert_eq!(result["replacements"], 3);
        assert_eq!(
            std::fs::read_to_string(workspace.root().join("a.md")).expect("read"),
            "y\ny\ny\n"
        );
    }

    #[test]
    fn the_edit_refusals_are_in_pythons_order() {
        let (_dir, workspace) = workspace();
        // Identical strings are refused before the path is even resolved.
        assert_eq!(
            edit_file(
                &workspace,
                &args(json!({"path": "../out", "old_string": "a", "new_string": "a"}))
            )
            .expect_err("identical")
            .message,
            "old_string and new_string are identical; nothing to change."
        );
        assert_eq!(
            edit_file(
                &workspace,
                &args(json!({"path": "missing.md", "old_string": "a", "new_string": "b"}))
            )
            .expect_err("missing")
            .message,
            "File does not exist."
        );
        std::fs::write(workspace.root().join("a.md"), "hello\n").expect("seed");
        assert_eq!(
            edit_file(
                &workspace,
                &args(json!({"path": "a.md", "old_string": "nope", "new_string": "b"}))
            )
            .expect_err("absent")
            .message,
            "old_string not found in file. Read the file first and copy the exact bytes (including whitespace)."
        );
        assert_eq!(
            edit_file(
                &workspace,
                &args(json!({"path": "a.md", "old_string": "a"}))
            )
            .expect_err("missing key")
            .message,
            "'new_string'"
        );
    }

    #[test]
    fn a_directory_is_not_a_file_to_edit() {
        let (_dir, workspace) = workspace();
        std::fs::create_dir_all(workspace.root().join("sub")).expect("dir");
        assert_eq!(
            edit_file(
                &workspace,
                &args(json!({"path": "sub", "old_string": "a", "new_string": "b"}))
            )
            .expect_err("not a file")
            .message,
            "File does not exist."
        );
    }

    #[test]
    fn todos_are_cleaned_and_written_in_pythons_key_order() {
        let (_dir, workspace) = workspace();
        let result = todo_write(
            &workspace,
            &args(json!({"todos": [
                {"content": "  첫 번째  ", "status": "IN_PROGRESS"},
                {"id": 7, "content": "second"},
            ]})),
        )
        .expect("todo_write");
        assert_eq!(result["path"], TODO_REL_PATH);
        assert_eq!(result["warning"], Value::Null);
        assert_eq!(
            result["todos"][0]["id"], "1",
            "the index is the fallback id"
        );
        assert_eq!(result["todos"][0]["content"], "첫 번째");
        assert_eq!(result["todos"][0]["status"], "in_progress");
        assert_eq!(result["todos"][1]["id"], "7");
        assert_eq!(result["todos"][1]["status"], "pending");

        let body =
            std::fs::read_to_string(workspace.root().join(TODO_REL_PATH)).expect("todo file");
        assert!(
            body.starts_with("[\n  {\n    \"id\": \"1\",\n    \"content\": \"첫 번째\",\n    \"status\": \"in_progress\"\n  },"),
            "{body}"
        );
        assert!(
            !body.ends_with('\n'),
            "json.dumps writes no trailing newline"
        );
    }

    #[test]
    fn a_second_in_progress_todo_only_warns() {
        let (_dir, workspace) = workspace();
        let result = todo_write(
            &workspace,
            &args(json!({"todos": [
                {"content": "a", "status": "in_progress"},
                {"content": "b", "status": "in_progress"},
            ]})),
        )
        .expect("todo_write");
        assert_eq!(
            result["warning"],
            "More than one todo is in_progress; keep only one active at a time."
        );
    }

    #[test]
    fn the_todo_refusals_name_the_offending_row() {
        let (_dir, workspace) = workspace();
        assert_eq!(
            todo_write(&workspace, &args(json!({"todos": "nope"})))
                .expect_err("not a list")
                .message,
            "todos must be a list."
        );
        let many: Vec<Value> = (0..51).map(|i| json!({"content": i.to_string()})).collect();
        assert_eq!(
            todo_write(&workspace, &args(json!({"todos": many})))
                .expect_err("too many")
                .message,
            "Too many todos (max 50). Split into smaller batches."
        );
        assert_eq!(
            todo_write(&workspace, &args(json!({"todos": ["nope"]})))
                .expect_err("not an object")
                .message,
            "Todo #1 is not an object."
        );
        assert_eq!(
            todo_write(&workspace, &args(json!({"todos": [{"content": "  "}]})))
                .expect_err("no content")
                .message,
            "Todo #1 is missing 'content'."
        );
        assert_eq!(
            todo_write(
                &workspace,
                &args(json!({"todos": [{"content": "a"}, {"content": "b", "status": "later"}]}))
            )
            .expect_err("bad status")
            .message,
            "Todo #2 has invalid status 'later'. Use one of ['completed', 'in_progress', 'pending']."
        );
    }

    #[test]
    fn an_empty_todo_list_clears_the_file() {
        let (_dir, workspace) = workspace();
        todo_write(&workspace, &args(json!({"todos": [{"content": "a"}]}))).expect("first");
        let result = todo_write(&workspace, &Map::new()).expect("absent todos is an empty list");
        assert_eq!(result["todos"], json!([]));
        assert_eq!(
            std::fs::read_to_string(workspace.root().join(TODO_REL_PATH)).expect("file"),
            "[]"
        );
    }
}
