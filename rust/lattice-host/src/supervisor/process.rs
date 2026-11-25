use std::path::Path;
use std::process::Stdio;

use tokio::process::{Child, Command};

use super::command::{HostProbe, WorkerCommand};
use super::worker_env::{log_paths, open_log, worker_env};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SignalOutcome {
    Delivered,
    Gone,
    Unsupported,
}

#[cfg(unix)]
fn raw_signal(pid: u32, signal: i32) -> SignalOutcome {
    let rc = unsafe { libc::kill(pid as libc::pid_t, signal) };
    if rc == 0 {
        SignalOutcome::Delivered
    } else {
        SignalOutcome::Gone
    }
}

#[cfg(unix)]
pub fn terminate(pid: u32) -> SignalOutcome {
    raw_signal(pid, libc::SIGTERM)
}

#[cfg(unix)]
pub fn force_kill(pid: u32) -> SignalOutcome {
    raw_signal(pid, libc::SIGKILL)
}

#[cfg(unix)]
pub fn is_alive(pid: u32) -> bool {
    raw_signal(pid, 0) == SignalOutcome::Delivered
}

#[cfg(not(unix))]
pub fn terminate(_pid: u32) -> SignalOutcome {
    SignalOutcome::Unsupported
}

#[cfg(not(unix))]
pub fn force_kill(_pid: u32) -> SignalOutcome {
    SignalOutcome::Unsupported
}

#[cfg(not(unix))]
pub fn is_alive(_pid: u32) -> bool {
    true
}

pub fn spawn_worker(
    command: &WorkerCommand,
    port: u16,
    gateway_port: Option<u16>,
    runtime_dir: Option<&Path>,
    log_dir: Option<&Path>,
    probe: &dyn HostProbe,
    extra_env: &[(String, String)],
) -> std::io::Result<Child> {
    let mut cmd = Command::new(&command.program);
    cmd.args(&command.args);
    for (key, value) in worker_env(command, port, gateway_port, runtime_dir, probe) {
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
    async fn a_fronted_worker_process_receives_the_gateway_trusted_origins() {
        let dir = tempfile::tempdir().expect("tempdir");
        let out = dir.path().join("env.txt");
        let script = format!(
            "printf '%s|%s' \"$LATTICEAI_PORT\" \"$LATTICEAI_CSRF_TRUSTED_ORIGINS\" > {}",
            out.display()
        );
        let mut child = spawn_worker(
            &sh_command(&script),
            4899,
            Some(4825),
            None,
            None,
            &StaticProbe::new(),
            &[],
        )
        .expect("spawn");
        child.wait().await.expect("wait");
        assert_eq!(
            std::fs::read_to_string(&out).expect("read"),
            "4899|http://127.0.0.1:4825,http://localhost:4825"
        );
    }

    #[tokio::test]
    async fn stdout_and_stderr_land_in_the_sidecar_logs() {
        let dir = tempfile::tempdir().expect("tempdir");
        let mut child = spawn_worker(
            &sh_command("echo out; echo err 1>&2"),
            4899,
            None,
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
            None,
            &StaticProbe::new(),
            &[],
        )
        .expect("spawn");
        let pid = child.id().expect("pid");
        wait_for_marker(&marker).await;
        assert_eq!(terminate(pid), SignalOutcome::Delivered);
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
        let err = spawn_worker(&cmd, 4825, None, None, None, &StaticProbe::new(), &[])
            .expect_err("must fail");
        assert_eq!(err.kind(), std::io::ErrorKind::NotFound);
    }
}
