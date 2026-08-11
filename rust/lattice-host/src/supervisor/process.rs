//! Spawning and signalling the worker process.
//!
//! Graceful shutdown is SIGTERM first (so the worker can close its SQLite
//! handles and flush logs), SIGKILL only after the grace period. On non-unix
//! targets there is no SIGTERM, so both paths collapse to the std kill.

use std::path::Path;
use std::process::Stdio;

use tokio::process::{Child, Command};

use super::command::{HostProbe, WorkerCommand};
use super::worker_env::{log_paths, open_log, worker_env};

/// Result of asking a process to stop.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SignalOutcome {
    /// The signal was delivered.
    Delivered,
    /// The process was already gone.
    Gone,
    /// Signalling is not supported on this platform.
    Unsupported,
}

#[cfg(unix)]
fn raw_signal(pid: u32, signal: i32) -> SignalOutcome {
    // SAFETY: kill(2) with a pid we spawned; no memory is touched.
    let rc = unsafe { libc::kill(pid as libc::pid_t, signal) };
    if rc == 0 {
        SignalOutcome::Delivered
    } else {
        SignalOutcome::Gone
    }
}

/// Ask the process to terminate (SIGTERM on unix).
#[cfg(unix)]
pub fn terminate(pid: u32) -> SignalOutcome {
    raw_signal(pid, libc::SIGTERM)
}

/// Kill the process outright (SIGKILL on unix).
#[cfg(unix)]
pub fn force_kill(pid: u32) -> SignalOutcome {
    raw_signal(pid, libc::SIGKILL)
}

/// Whether the process is still alive (`kill(pid, 0)` on unix).
#[cfg(unix)]
pub fn is_alive(pid: u32) -> bool {
    raw_signal(pid, 0) == SignalOutcome::Delivered
}

/// Ask the process to terminate. Not supported off unix.
#[cfg(not(unix))]
pub fn terminate(_pid: u32) -> SignalOutcome {
    SignalOutcome::Unsupported
}

/// Kill the process outright. Not supported off unix.
#[cfg(not(unix))]
pub fn force_kill(_pid: u32) -> SignalOutcome {
    SignalOutcome::Unsupported
}

/// Liveness check. Always `true` off unix (we cannot tell).
#[cfg(not(unix))]
pub fn is_alive(_pid: u32) -> bool {
    true
}

/// Spawn the worker with the pinned environment and log redirection.
pub fn spawn_worker(
    command: &WorkerCommand,
    port: u16,
    runtime_dir: Option<&Path>,
    log_dir: Option<&Path>,
    probe: &dyn HostProbe,
    extra_env: &[(String, String)],
) -> std::io::Result<Child> {
    let mut cmd = Command::new(&command.program);
    cmd.args(&command.args);
    for (key, value) in worker_env(command, port, runtime_dir, probe) {
        cmd.env(key, value);
    }
    for (key, value) in extra_env {
        cmd.env(key, value);
    }
    match (&command.cwd, runtime_dir) {
        (Some(cwd), _) => {
            cmd.current_dir(cwd);
        }
        (None, Some(runtime)) => {
            cmd.current_dir(runtime);
        }
        (None, None) => {}
    }
    cmd.stdin(Stdio::null());
    match log_dir {
        Some(dir) => {
            let (out_path, err_path) = log_paths(dir);
            match open_log(&out_path) {
                Ok(file) => {
                    cmd.stdout(Stdio::from(file));
                }
                Err(_) => {
                    cmd.stdout(Stdio::null());
                }
            }
            match open_log(&err_path) {
                Ok(file) => {
                    cmd.stderr(Stdio::from(file));
                }
                Err(_) => {
                    cmd.stderr(Stdio::null());
                }
            }
        }
        None => {
            cmd.stdout(Stdio::null()).stderr(Stdio::null());
        }
    }
    // Nothing outlives the supervisor: if the host dies, so does the worker.
    cmd.kill_on_drop(true);
    cmd.spawn()
}

#[cfg(test)]
mod tests {
    use super::super::command::{CommandOrigin, StaticProbe};
    use super::*;
    use std::time::Duration;

    fn sh_command(script: &str) -> WorkerCommand {
        WorkerCommand {
            program: "/bin/sh".into(),
            args: vec!["-c".into(), script.into()],
            cwd: None,
            python_root: None,
            origin: CommandOrigin::EnvOverride,
        }
    }

    #[tokio::test]
    async fn spawned_worker_sees_the_pinned_environment() {
        let dir = tempfile::tempdir().expect("tempdir");
        let out = dir.path().join("env.txt");
        let script = format!(
            "printf '%s|%s|%s\\n' \"$LATTICEAI_HOST\" \"$LATTICEAI_PORT\" \
             \"$LATTICEAI_ENABLE_TELEGRAM\" > {}",
            out.display()
        );
        let mut child = spawn_worker(
            &sh_command(&script),
            4899,
            None,
            None,
            &StaticProbe::new(),
            &[],
        )
        .expect("spawn");
        let status = child.wait().await.expect("wait");
        assert!(status.success());
        assert_eq!(
            std::fs::read_to_string(&out).expect("read"),
            "127.0.0.1|4899|false\n"
        );
    }

    #[tokio::test]
    async fn stdout_and_stderr_land_in_the_sidecar_logs() {
        let dir = tempfile::tempdir().expect("tempdir");
        let mut child = spawn_worker(
            &sh_command("echo out; echo err 1>&2"),
            4899,
            None,
            Some(dir.path()),
            &StaticProbe::new(),
            &[],
        )
        .expect("spawn");
        child.wait().await.expect("wait");
        let (out_path, err_path) = log_paths(dir.path());
        assert_eq!(std::fs::read_to_string(out_path).expect("out"), "out\n");
        assert_eq!(std::fs::read_to_string(err_path).expect("err"), "err\n");
    }

    #[tokio::test]
    async fn extra_env_overrides_the_pinned_values() {
        let dir = tempfile::tempdir().expect("tempdir");
        let out = dir.path().join("env.txt");
        let script = format!("printf '%s' \"$LATTICEAI_EXTRA\" > {}", out.display());
        let mut child = spawn_worker(
            &sh_command(&script),
            4899,
            None,
            None,
            &StaticProbe::new(),
            &[("LATTICEAI_EXTRA".to_string(), "yes".to_string())],
        )
        .expect("spawn");
        child.wait().await.expect("wait");
        assert_eq!(std::fs::read_to_string(&out).expect("read"), "yes");
    }

    #[tokio::test]
    async fn terminate_stops_a_normal_child_and_is_alive_flips() {
        let mut child = spawn_worker(
            &sh_command("while true; do sleep 0.05; done"),
            4899,
            None,
            None,
            &StaticProbe::new(),
            &[],
        )
        .expect("spawn");
        let pid = child.id().expect("pid");
        assert!(is_alive(pid));
        assert_eq!(terminate(pid), SignalOutcome::Delivered);
        let status = tokio::time::timeout(Duration::from_secs(5), child.wait())
            .await
            .expect("child exits after SIGTERM")
            .expect("wait");
        assert!(!status.success(), "signalled child does not exit cleanly");
    }

    /// Wait until the child has actually reached the point where it is
    /// signal-ready. Signalling a process that has not finished `exec` yet
    /// tests nothing (the default disposition would kill it).
    async fn wait_for_marker(marker: &std::path::Path) {
        for _ in 0..500 {
            if marker.exists() {
                return;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        panic!("child never signalled readiness at {}", marker.display());
    }

    #[tokio::test]
    async fn force_kill_stops_a_child_that_ignores_sigterm() {
        let dir = tempfile::tempdir().expect("tempdir");
        let marker = dir.path().join("ready");
        let script = format!(
            "trap '' TERM; : > {}; while true; do sleep 0.05; done",
            marker.display()
        );
        let mut child = spawn_worker(
            &sh_command(&script),
            4899,
            None,
            None,
            &StaticProbe::new(),
            &[],
        )
        .expect("spawn");
        let pid = child.id().expect("pid");
        wait_for_marker(&marker).await;
        assert_eq!(terminate(pid), SignalOutcome::Delivered);
        // Still alive: the trap swallowed SIGTERM.
        tokio::time::sleep(Duration::from_millis(150)).await;
        assert!(child.try_wait().expect("try_wait").is_none());
        assert_eq!(force_kill(pid), SignalOutcome::Delivered);
        tokio::time::timeout(Duration::from_secs(5), child.wait())
            .await
            .expect("child exits after SIGKILL")
            .expect("wait");
    }

    #[test]
    fn signalling_a_dead_pid_reports_gone() {
        // PID 0 means "the whole process group" for kill(2); use a pid that
        // cannot exist instead.
        assert_eq!(terminate(u32::MAX / 2), SignalOutcome::Gone);
        assert!(!is_alive(u32::MAX / 2));
    }

    #[tokio::test]
    async fn a_missing_program_is_a_spawn_error_not_a_panic() {
        let cmd = WorkerCommand {
            program: "/definitely/not/here".into(),
            args: vec![],
            cwd: None,
            python_root: None,
            origin: CommandOrigin::PythonModule,
        };
        let err =
            spawn_worker(&cmd, 4825, None, None, &StaticProbe::new(), &[]).expect_err("must fail");
        assert_eq!(err.kind(), std::io::ErrorKind::NotFound);
    }
}
