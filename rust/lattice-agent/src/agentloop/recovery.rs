//! ROLLBACK — putting the workspace back, honestly.
//!
//! A port of `latticeai.core.agent.recovery`. Three tiers, tried in order: git
//! where the policy says the tool is git-rollbackable, then the pre-write
//! snapshot the executor captured, and otherwise `mode="none"` — an admission,
//! not a claim. Rollback always ends the run as FAILED, because it *is* the
//! recovery from a failed verification.
//!
//! This is the one phase that touches the workspace directly instead of going
//! through the worker seam. That is deliberate: a rollback that has to ask a
//! possibly-broken worker for permission to undo the worker's own writes is not
//! a rollback.

use std::time::Duration;

use serde_json::{json, Value};

use super::{RunRequest, Runtime, GIT_ROLLBACK_TIMEOUT_SECS};
use crate::pystr::{is_truthy, py_list_repr, py_str};
use crate::state::{AgentRunContext, AgentState};

impl Runtime {
    fn snapshot_for(ctx: &AgentRunContext, path: &str) -> Option<Value> {
        ctx.rollback_log
            .iter()
            .find(|entry| entry.get("path").and_then(Value::as_str) == Some(path))
            .cloned()
    }

    /// Recover one path: git when governed and available, else the pre-write
    /// snapshot, else an honest `mode="none"`.
    async fn rollback_one(&self, ctx: &AgentRunContext, path: &str, governance: &Value) -> Value {
        if governance.get("rollback").and_then(Value::as_str) == Some("git") {
            let mut result = self.git_checkout(path).await;
            if result.get("ok") == Some(&json!(true)) {
                result["mode"] = json!("git");
                return result;
            }
        }
        let snapshot = Self::snapshot_for(ctx, path);
        if let Some(snapshot) = snapshot {
            if !snapshot.get("too_large").is_some_and(is_truthy) {
                let existed = snapshot.get("existed").is_some_and(is_truthy);
                let content = if existed {
                    snapshot.get("content").and_then(Value::as_str)
                } else {
                    None
                };
                let mut restored = self.restore_snapshot(path, content);
                if restored.get("path").is_none() {
                    restored["path"] = json!(path);
                }
                restored["mode"] = json!("snapshot");
                return restored;
            }
        }
        json!({
            "path": path, "ok": false, "mode": "none",
            "error": "no rollback available (git not applicable, no usable snapshot)",
        })
    }

    /// `ToolDispatchService.rollback_file`: `git checkout -- <path>` under the
    /// agent root, with the same ten-second ceiling.
    async fn git_checkout(&self, path: &str) -> Value {
        let mut command = tokio::process::Command::new("git");
        command
            .args(["checkout", "--", path])
            .current_dir(self.deps.workspace.root())
            .stdin(std::process::Stdio::null());
        let spawned = tokio::time::timeout(
            Duration::from_secs(GIT_ROLLBACK_TIMEOUT_SECS),
            command.output(),
        )
        .await;
        match spawned {
            Ok(Ok(output)) => json!({
                "path": path,
                "ok": output.status.success(),
                "stderr": crate::pystr::char_slice(
                    &String::from_utf8_lossy(&output.stderr), 200,
                ),
            }),
            // Python wraps the call in `except Exception`, so a missing git or a
            // timeout is a failed recovery attempt, never a failed run.
            Ok(Err(error)) => json!({"path": path, "ok": false, "error": error.to_string()}),
            Err(_) => json!({
                "path": path, "ok": false,
                "error": format!("git checkout timed out after {GIT_ROLLBACK_TIMEOUT_SECS} seconds"),
            }),
        }
    }

    /// ROLLBACK: recover written files (git → snapshot → none), then FAILED.
    pub async fn rollback(&mut self, ctx: &mut AgentRunContext, req: &RunRequest) {
        let mut rolled: Vec<Value> = Vec::new();
        let mut seen: Vec<String> = Vec::new();
        for step in ctx.transcript.clone() {
            if step.get("state").and_then(Value::as_str) != Some(AgentState::Executing.as_str()) {
                continue;
            }
            let Some(result) = step.get("result").filter(|value| value.is_object()) else {
                continue;
            };
            let governance = step
                .get("governance")
                .filter(|value| is_truthy(value))
                .cloned()
                .unwrap_or_else(|| json!({}));
            let path = result
                .get("path")
                .filter(|value| is_truthy(value))
                .or_else(|| step.get("args").and_then(|args| args.get("path")))
                .filter(|value| is_truthy(value))
                .map(py_str)
                .unwrap_or_default();
            if path.is_empty() || seen.contains(&path) {
                continue;
            }
            let git_governed = governance.get("rollback").and_then(Value::as_str) == Some("git");
            let file_create = step
                .get("action")
                .and_then(Value::as_str)
                .is_some_and(|action| self.deps.file_create_actions.contains(action));
            if !git_governed && !file_create {
                continue;
            }
            seen.push(path.clone());
            rolled.push(self.rollback_one(ctx, &path, &governance).await);
        }

        ctx.transcript.push(json!({
            "state": AgentState::Rollback.as_str(),
            "rolled_back": rolled,
        }));
        let recovered_count = rolled
            .iter()
            .filter(|entry| entry.get("ok").is_some_and(is_truthy))
            .count();
        ctx.trace.decision(
            "rollback",
            "rolled_back",
            &[
                ("attempted", json!(rolled.len())),
                ("recovered", json!(recovered_count)),
            ],
        );
        let recovered: Vec<String> = rolled
            .iter()
            .filter(|entry| entry.get("ok").is_some_and(is_truthy))
            .map(|entry| {
                format!(
                    "{} ({})",
                    py_str(entry.get("path").unwrap_or(&Value::Null)),
                    py_str(entry.get("mode").unwrap_or(&Value::Null)),
                )
            })
            .collect();
        ctx.final_message = if recovered.is_empty() {
            "롤백을 시도했으나 복구할 파일이 없거나 git/스냅샷 복구 수단이 없습니다.".into()
        } else {
            // Python interpolates the *list*, so the message carries a Python
            // list literal — single quotes and all.
            format!(
                "실행 실패로 롤백했습니다. 복구 파일: {}",
                py_list_repr(&recovered)
            )
        };
        self.audit(
            "agent_rollback",
            &[
                ("user_email", json!(req.user_email)),
                ("rolled_back", json!(rolled)),
            ],
        );
        self.emit_step(
            "rollback",
            "rolled_back",
            &[("recovered", json!(recovered.len()))],
        );
        // Rollback is recovery from a failed verification — terminal is FAILED.
        ctx.state = AgentState::Failed;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agentloop::harness::harness;
    use crate::agentloop::SNAPSHOT_MAX_BYTES;

    fn wrote(path: &str, rollback: &str) -> Value {
        json!({"state": "EXECUTING", "action": "write_file",
               "args": {"path": path}, "governance": {"rollback": rollback},
               "result": {"path": path, "bytes": 3}})
    }

    #[tokio::test]
    async fn a_snapshot_restores_prior_content_and_deletes_a_new_file() {
        let mut harness = harness(&[]).await;
        std::fs::write(harness.root.join("existing.md"), b"before").expect("seed");
        std::fs::write(harness.root.join("existing.md"), b"after").expect("overwrite");
        std::fs::write(harness.root.join("fresh.md"), b"new").expect("create");
        let mut ctx = harness.context();
        ctx.rollback_log = vec![
            json!({"path": "existing.md", "existed": true, "content": "before",
                   "too_large": false}),
            json!({"path": "fresh.md", "existed": false, "content": null, "too_large": false}),
        ];
        ctx.transcript = vec![wrote("existing.md", "none"), wrote("fresh.md", "none")];

        harness.runtime.rollback(&mut ctx, &harness.request).await;

        assert_eq!(ctx.state, AgentState::Failed, "rollback always ends FAILED");
        assert_eq!(
            std::fs::read_to_string(harness.root.join("existing.md")).expect("read"),
            "before"
        );
        assert!(
            !harness.root.join("fresh.md").exists(),
            "a created file is removed"
        );
        let rolled = ctx.transcript.last().expect("step")["rolled_back"].clone();
        assert_eq!(
            rolled[0],
            json!({"path": "existing.md", "ok": true,
                                     "action": "restored", "mode": "snapshot"})
        );
        assert_eq!(
            rolled[1],
            json!({"path": "fresh.md", "ok": true,
                                     "action": "deleted", "mode": "snapshot"})
        );
        assert_eq!(
            ctx.final_message,
            "실행 실패로 롤백했습니다. 복구 파일: ['existing.md (snapshot)', 'fresh.md (snapshot)']",
            "the message interpolates a Python list repr"
        );
        assert_eq!(harness.runtime.audit[0]["event"], "agent_rollback");
    }

    #[tokio::test]
    async fn without_a_usable_snapshot_the_answer_is_mode_none() {
        let mut harness = harness(&[]).await;
        let mut ctx = harness.context();
        ctx.rollback_log = vec![json!({"path": "big.md", "existed": true, "content": null,
                                       "too_large": true})];
        ctx.transcript = vec![wrote("big.md", "none"), wrote("unknown.md", "none")];
        harness.runtime.rollback(&mut ctx, &harness.request).await;
        let rolled = ctx.transcript.last().expect("step")["rolled_back"].clone();
        for entry in rolled.as_array().expect("rows") {
            assert_eq!(entry["mode"], "none");
            assert_eq!(entry["ok"], false);
            assert_eq!(
                entry["error"],
                "no rollback available (git not applicable, no usable snapshot)"
            );
        }
        assert_eq!(
            ctx.final_message,
            "롤백을 시도했으나 복구할 파일이 없거나 git/스냅샷 복구 수단이 없습니다."
        );
    }

    #[tokio::test]
    async fn only_git_governed_or_file_creating_steps_are_recovered() {
        let mut harness = harness(&[]).await;
        let mut ctx = harness.context();
        ctx.transcript = vec![
            // A read with a result and a path: not a file create, not git.
            json!({"state": "EXECUTING", "action": "read_file", "args": {"path": "skip.md"},
                   "governance": {"rollback": "none"}, "result": {"path": "skip.md"}}),
            // A blocked step has no result at all.
            json!({"state": "EXECUTING", "action": "write_file", "args": {"path": "blocked.md"},
                   "error": "BLOCKED"}),
            wrote("kept.md", "none"),
            // The same path twice is recovered once.
            wrote("kept.md", "none"),
        ];
        harness.runtime.rollback(&mut ctx, &harness.request).await;
        let rolled = ctx.transcript.last().expect("step")["rolled_back"]
            .as_array()
            .expect("rows")
            .clone();
        assert_eq!(rolled.len(), 1);
        assert_eq!(rolled[0]["path"], "kept.md");
    }

    #[tokio::test]
    async fn a_git_governed_path_falls_back_to_the_snapshot_when_git_cannot_help() {
        // The workspace is not a git repository, so `git checkout` fails and the
        // snapshot tier answers — which is exactly the honest ordering.
        let mut harness = harness(&[]).await;
        std::fs::write(harness.root.join("tracked.md"), b"changed").expect("write");
        let mut ctx = harness.context();
        ctx.rollback_log = vec![json!({"path": "tracked.md", "existed": true,
                                       "content": "original", "too_large": false})];
        ctx.transcript = vec![wrote("tracked.md", "git")];
        harness.runtime.rollback(&mut ctx, &harness.request).await;
        let rolled = ctx.transcript.last().expect("step")["rolled_back"].clone();
        assert_eq!(rolled[0]["mode"], "snapshot");
        assert_eq!(
            std::fs::read_to_string(harness.root.join("tracked.md")).expect("read"),
            "original"
        );
    }

    #[tokio::test]
    async fn nothing_to_roll_back_is_still_a_recorded_rollback() {
        let mut harness = harness(&[]).await;
        let mut ctx = harness.context();
        harness.runtime.rollback(&mut ctx, &harness.request).await;
        assert_eq!(
            ctx.transcript,
            vec![json!({"state": "ROLLBACK", "rolled_back": []})]
        );
        assert_eq!(ctx.state, AgentState::Failed);
    }

    #[test]
    fn a_snapshot_of_an_escaping_path_says_so_instead_of_reading_it() {
        let dir = tempfile::tempdir().expect("tempdir");
        let workspace =
            crate::sandbox::Workspace::new(dir.path().join("agent_workspace")).expect("ws");
        std::fs::write(dir.path().join("outside.txt"), b"secret").expect("outside");
        let runtime = Runtime::new(crate::agentloop::LoopDeps::new(
            crate::worker::WorkerClient::new("http://127.0.0.1:1"),
            workspace,
        ));
        assert_eq!(
            runtime.snapshot_file("../outside.txt"),
            json!({"existed": false, "content": null, "too_large": false,
                   "error": "path escapes the agent workspace"})
        );
        assert_eq!(
            runtime.restore_snapshot("../outside.txt", Some("x")),
            json!({"path": "../outside.txt", "ok": false,
                   "error": "path escapes the agent workspace"})
        );
        assert_eq!(
            std::fs::read_to_string(dir.path().join("outside.txt")).expect("read"),
            "secret"
        );
    }

    #[test]
    fn a_snapshot_over_the_cap_records_the_size_not_the_bytes() {
        let dir = tempfile::tempdir().expect("tempdir");
        let workspace =
            crate::sandbox::Workspace::new(dir.path().join("agent_workspace")).expect("ws");
        let big = workspace.root().join("big.bin");
        std::fs::write(&big, vec![b'a'; (SNAPSHOT_MAX_BYTES + 1) as usize]).expect("big");
        std::fs::create_dir_all(workspace.root().join("adir")).expect("dir");
        let runtime = Runtime::new(crate::agentloop::LoopDeps::new(
            crate::worker::WorkerClient::new("http://127.0.0.1:1"),
            workspace,
        ));
        assert_eq!(
            runtime.snapshot_file("big.bin"),
            json!({"existed": true, "content": null, "too_large": true})
        );
        assert_eq!(
            runtime.snapshot_file("adir"),
            json!({"existed": false, "content": null, "too_large": false}),
            "a directory is not a file to snapshot"
        );
        assert_eq!(
            runtime.snapshot_file("missing.md"),
            json!({"existed": false, "content": null, "too_large": false})
        );
    }
}
