use serde::Serialize;

use super::command::CommandOrigin;

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct WorkerStatus {
    pub running: bool,
    pub healthy: bool,
    pub pid: Option<u32>,
    pub port: u16,
    pub origin: String,
    pub command: Option<String>,
    pub command_origin: Option<CommandOrigin>,
    pub cwd: Option<String>,
    pub restarts: u32,
    pub last_error: Option<String>,
    pub supervised: bool,
    pub uptime_seconds: Option<u64>,
}

impl WorkerStatus {
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
