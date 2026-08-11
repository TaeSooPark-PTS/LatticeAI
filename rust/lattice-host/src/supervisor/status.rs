//! The supervisor's status snapshot — the single source of truth the gateway,
//! the Tauri shell and `/host/status` all read.

use serde::Serialize;

use super::command::CommandOrigin;

/// A point-in-time view of the supervised worker.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct WorkerStatus {
    /// Whether the supervisor owns a live worker process.
    pub running: bool,
    /// Whether `GET /health` last answered 2xx.
    pub healthy: bool,
    /// OS process id of the worker, when running.
    pub pid: Option<u32>,
    /// The chosen worker port — authoritative, not the configured preference.
    pub port: u16,
    /// `http://127.0.0.1:{port}`.
    pub origin: String,
    /// Resolved command line, once resolution has happened.
    pub command: Option<String>,
    /// Which resolution rule produced the command.
    pub command_origin: Option<CommandOrigin>,
    /// Working directory of the worker, when the rule set one.
    pub cwd: Option<String>,
    /// How many times the supervisor has restarted the worker after a crash.
    pub restarts: u32,
    /// Last error observed (spawn failure, non-zero exit, health timeout).
    pub last_error: Option<String>,
    /// Whether this host actually supervises the worker (`false` in
    /// `--no-spawn` mode, where it only fronts an already-running worker).
    pub supervised: bool,
    /// Seconds since the current worker process started.
    pub uptime_seconds: Option<u64>,
}

impl WorkerStatus {
    /// An "unstarted" snapshot for `port`.
    pub fn idle(port: u16, supervised: bool) -> Self {
        Self {
            running: false,
            healthy: false,
            pid: None,
            port,
            origin: origin_for(port),
            command: None,
            command_origin: None,
            cwd: None,
            restarts: 0,
            last_error: None,
            supervised,
            uptime_seconds: None,
        }
    }
}

/// The loopback origin for a port.
pub fn origin_for(port: u16) -> String {
    format!("http://127.0.0.1:{port}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn idle_snapshot_is_honest_about_having_nothing() {
        let status = WorkerStatus::idle(4825, true);
        assert!(!status.running);
        assert!(!status.healthy);
        assert_eq!(status.pid, None);
        assert_eq!(status.origin, "http://127.0.0.1:4825");
        assert_eq!(status.restarts, 0);
        assert!(status.supervised);
    }

    #[test]
    fn snapshot_serialises_with_the_documented_field_names() {
        let mut status = WorkerStatus::idle(4899, false);
        status.command = Some("/usr/bin/python3 -m latticeai.cli.entrypoint".into());
        status.command_origin = Some(CommandOrigin::PythonModule);
        let value = serde_json::to_value(&status).expect("serialise");
        for key in [
            "running",
            "healthy",
            "pid",
            "port",
            "origin",
            "command",
            "command_origin",
            "cwd",
            "restarts",
            "last_error",
            "supervised",
            "uptime_seconds",
        ] {
            assert!(value.get(key).is_some(), "missing field {key}");
        }
        assert_eq!(value["command_origin"], "python_module");
        assert_eq!(value["port"], 4899);
    }
}
