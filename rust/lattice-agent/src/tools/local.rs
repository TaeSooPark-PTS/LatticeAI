use serde_json::{json, Map, Value};

use crate::tools::args;
use crate::tools::files::{file_size, io_error};
use crate::tools::sandbox::{resolve_soft, ToolError};

pub const LOCAL_MAX_FILE_BYTES: u64 = 2_000_000;

pub fn expanduser(value: &str) -> std::path::PathBuf {
    if value == "~" || value.starts_with("~/") {
        if let Some(home) = std::env::var_os("HOME") {
            if !home.is_empty() {
                let mut path = std::path::PathBuf::from(home);
                path.push(value.trim_start_matches('~').trim_start_matches('/'));
                return path;
            }
        }
    }
    std::path::PathBuf::from(value)
}

fn absolute(value: &str) -> std::path::PathBuf {
    let expanded = expanduser(value);
    let candidate = if expanded.is_absolute() {
        expanded
    } else {
        std::env::current_dir()
            .unwrap_or_else(|_| std::path::PathBuf::from("."))
            .join(expanded)
    };
    resolve_soft(&candidate)
}

pub fn is_blocked(normalized: &str, prefixes: &[String]) -> bool {
    prefixes.iter().any(|prefix| {
        normalized == prefix.trim_end_matches('/') || normalized.starts_with(prefix.as_str())
    })
}

pub fn local_write(prefixes: &[String], args: &Map<String, Value>) -> Result<Value, ToolError> {
    let path = args::required_str(args, "path")?;
    let content = args::optional_str(args, "content", "")?;
    let target = absolute(&path);
    let normalized = target.display().to_string().replace('\\', "/");
    if is_blocked(&normalized, prefixes) {
        return Err(ToolError::tool("차단된 시스템 경로에는 쓸 수 없습니다."));
    }
    if content.len() as u64 > LOCAL_MAX_FILE_BYTES {
        return Err(ToolError::tool("내용이 너무 큽니다."));
    }
    if let Some(parent) = target.parent() {
        std::fs::create_dir_all(parent).map_err(permission_error)?;
    }
    std::fs::write(&target, &content).map_err(permission_error)?;
    Ok(json!({
        "path": target.display().to_string(),
        "bytes": file_size(&target),
    }))
}

pub fn local_write_confined(
    workspace: &crate::tools::sandbox::Workspace,
    prefixes: &[String],
    args: &Map<String, Value>,
) -> Result<Value, ToolError> {
    let raw_path = args::required_str(args, "path")?;
    let content = args::optional_str(args, "content", "")?;
    let trimmed = raw_path.trim();
    let candidate = if trimmed == "~" || trimmed.starts_with("~/") {
        let rel = trimmed.trim_start_matches('~').trim_start_matches('/');
        workspace.root().join(rel)
    } else {
        let p = std::path::Path::new(trimmed);
        if p.is_absolute() {
            p.to_path_buf()
        } else {
            workspace.root().join(p)
        }
    };
    let target = resolve_soft(&candidate);
    if !workspace.contains(&target) {
        return Err(ToolError::tool("Path escapes the agent workspace."));
    }
    let normalized = target.display().to_string().replace('\\', "/");
    if is_blocked(&normalized, prefixes) {
        return Err(ToolError::tool("차단된 시스템 경로에는 쓸 수 없습니다."));
    }
    if content.len() as u64 > LOCAL_MAX_FILE_BYTES {
        return Err(ToolError::tool("내용이 너무 큽니다."));
    }
    if let Some(parent) = target.parent() {
        std::fs::create_dir_all(parent).map_err(permission_error)?;
    }
    std::fs::write(&target, &content).map_err(permission_error)?;
    Ok(json!({
        "path": target.display().to_string(),
        "bytes": file_size(&target),
    }))
}

fn permission_error(error: std::io::Error) -> ToolError {
    if error.kind() == std::io::ErrorKind::PermissionDenied {
        ToolError::tool(format!("쓰기 권한 없음: {error}"))
    } else {
        io_error(error)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::kernel::policy::default_blocked_write_prefixes;

    fn args(value: Value) -> Map<String, Value> {
        value.as_object().expect("object").clone()
    }

    #[test]
    fn a_write_lands_at_the_absolute_resolved_path() {
        let dir = tempfile::tempdir().expect("tempdir");
        let target = dir.path().join("nested/note.txt");
        let result = local_write(
            &default_blocked_write_prefixes(),
            &args(json!({"path": target.display().to_string(), "content": "한글"})),
        )
        .expect("write");
        assert_eq!(result["bytes"], 6);
        assert_eq!(
            result["path"],
            json!(resolve_soft(&target).display().to_string()),
            "the reported path is the resolved one"
        );
        assert_eq!(std::fs::read_to_string(&target).expect("read"), "한글");
    }

    #[test]
    fn the_system_prefixes_are_refused_and_their_neighbours_are_not() {
        let prefixes = default_blocked_write_prefixes();
        for blocked in [
            "/etc/hosts",
            "/etc",
            "/usr/local/bin/x",
            "/Library/LaunchAgents/x.plist",
        ] {
            assert!(is_blocked(blocked, &prefixes), "{blocked}");
            assert_eq!(
                local_write(&prefixes, &args(json!({"path": blocked, "content": "x"})))
                    .expect_err("blocked")
                    .message,
                "차단된 시스템 경로에는 쓸 수 없습니다."
            );
        }
        for allowed in ["/etcetera/a", "/home/u/etc/a", "/tmp/a"] {
            assert!(!is_blocked(allowed, &prefixes), "{allowed}");
        }
    }

    #[test]
    fn the_size_cap_is_the_local_one_not_the_workspace_one() {
        let dir = tempfile::tempdir().expect("tempdir");
        let path = dir.path().join("big.txt").display().to_string();
        let big = "x".repeat(LOCAL_MAX_FILE_BYTES as usize + 1);
        assert_eq!(
            local_write(&[], &args(json!({"path": path.clone(), "content": big})))
                .expect_err("too large")
                .message,
            "내용이 너무 큽니다."
        );
        let medium = "x".repeat(600_000);
        assert!(local_write(&[], &args(json!({"path": path, "content": medium}))).is_ok());
    }

    #[test]
    fn a_missing_path_is_the_key_error() {
        assert_eq!(
            local_write(&[], &args(json!({"content": "x"})))
                .expect_err("missing")
                .message,
            "'path'"
        );
    }

    #[test]
    fn a_tilde_expands_from_home() {
        let dir = tempfile::tempdir().expect("tempdir");
        let home = std::env::var_os("HOME").map(std::path::PathBuf::from);
        match home {
            Some(home) if !home.as_os_str().is_empty() => {
                assert_eq!(expanduser("~/notes/a.md"), home.join("notes/a.md"));
                assert_eq!(expanduser("~"), home);
            }
            _ => assert_eq!(
                expanduser("~/notes/a.md"),
                std::path::PathBuf::from("~/notes/a.md")
            ),
        }
        assert_eq!(expanduser("~root/x"), std::path::PathBuf::from("~root/x"));
        assert_eq!(
            expanduser(&dir.path().display().to_string()),
            dir.path().to_path_buf()
        );
    }
}
