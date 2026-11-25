use std::fs::{File, OpenOptions};
use std::path::{Path, PathBuf};

use super::command::{HostProbe, WorkerCommand};

pub const WORKER_PATH_PREFIX: &str =
    "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin";

pub const SAFETY_OFF_FLAGS: [&str; 6] = [
    "LATTICEAI_ENABLE_TELEGRAM",
    "LATTICEAI_AUTOLOAD_MODELS",
    "LATTICEAI_ALLOW_MODEL_DOWNLOADS",
    "LATTICEAI_CORS_ALLOW_NETWORK",
    "LATTICEAI_ENABLE_EXTERNAL_CONNECTORS",
    "LATTICEAI_TUNNEL",
];

pub const CSRF_TRUSTED_ORIGINS_ENV: &str = "LATTICEAI_CSRF_TRUSTED_ORIGINS";

pub const CORS_ALLOWED_ORIGINS_ENV: &str = "LATTICEAI_CORS_ALLOWED_ORIGINS";

pub const AGENT_ROOT_ENV: &str = "LATTICEAI_AGENT_ROOT";

pub const AGENT_TOOL_SEAM_ENV: &str = "LATTICEAI_AGENT_TOOL_SEAM";

pub fn csrf_trusted_origins(gateway_port: u16) -> String {
    format!("http://127.0.0.1:{gateway_port},http://localhost:{gateway_port}")
}

pub fn cors_allowed_origins(gateway_port: u16) -> String {
    format!(
        "http://127.0.0.1:{gateway_port},http://localhost:{gateway_port},http://[::1]:{gateway_port}"
    )
}

pub fn ltcai_home(probe: &dyn HostProbe) -> Option<PathBuf> {
    probe
        .env_var("HOME")
        .filter(|home| !home.trim().is_empty())
        .map(|home| PathBuf::from(home).join(".ltcai"))
}

pub fn desktop_runtime_dir(probe: &dyn HostProbe) -> Option<PathBuf> {
    let dir = ltcai_home(probe)?.join("desktop-runtime");
    let _ = std::fs::create_dir_all(&dir);
    Some(dir)
}

pub fn log_paths(dir: &Path) -> (PathBuf, PathBuf) {
    (
        dir.join("desktop-sidecar.log"),
        dir.join("desktop-sidecar.err.log"),
    )
}

pub fn open_log(path: &Path) -> std::io::Result<File> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    OpenOptions::new().create(true).append(true).open(path)
}

pub fn worker_path(probe: &dyn HostProbe) -> String {
    match probe.env_var("PATH") {
        Some(existing) if !existing.is_empty() => format!("{WORKER_PATH_PREFIX}:{existing}"),
        _ => WORKER_PATH_PREFIX.to_string(),
    }
}

pub fn worker_python_path(command: &WorkerCommand, probe: &dyn HostProbe) -> Option<String> {
    let mut paths: Vec<PathBuf> = Vec::new();
    if let Some(root) = &command.python_root {
        paths.push(root.clone());
    }
    if let Some(cwd) = &command.cwd {
        if !paths.iter().any(|path| path == cwd) {
            paths.push(cwd.clone());
        }
    }
    if paths.is_empty() {
        return None;
    }
    if let Some(existing) = probe.env_var("PYTHONPATH") {
        paths.extend(std::env::split_paths(&existing));
    }
    std::env::join_paths(paths)
        .ok()
        .map(|value| value.to_string_lossy().into_owned())
}

pub fn worker_env(
    command: &WorkerCommand,
    port: u16,
    gateway_port: Option<u16>,
    runtime_dir: Option<&Path>,
    probe: &dyn HostProbe,
) -> Vec<(String, String)> {
    let mut env: Vec<(String, String)> = vec![
        ("LATTICEAI_HOST".into(), "127.0.0.1".into()),
        ("LATTICEAI_PORT".into(), port.to_string()),
    ];
    for flag in SAFETY_OFF_FLAGS {
        env.push((flag.into(), "false".into()));
    }
    env.push((AGENT_TOOL_SEAM_ENV.into(), "1".into()));
    if let Some(gateway_port) = gateway_port {
        env.push((
            CSRF_TRUSTED_ORIGINS_ENV.into(),
            csrf_trusted_origins(gateway_port),
        ));
        env.push((
            CORS_ALLOWED_ORIGINS_ENV.into(),
            cors_allowed_origins(gateway_port),
        ));
    }
    env.push(("PATH".into(), worker_path(probe)));
    if let Some(dir) = runtime_dir {
        env.push((
            AGENT_ROOT_ENV.into(),
            dir.join("agent_workspace").to_string_lossy().into_owned(),
        ));
    }
    if let Some(python_path) = worker_python_path(command, probe) {
        env.push(("PYTHONPATH".into(), python_path));
    }
    env
}

#[cfg(test)]
mod tests {
    use super::super::command::{CommandOrigin, StaticProbe};
    use super::*;

    fn command() -> WorkerCommand {
        WorkerCommand {
            program: "/usr/bin/python3".into(),
            args: vec!["-m".into(), "latticeai.cli.entrypoint".into()],
            cwd: None,
            python_root: None,
            origin: CommandOrigin::PythonModule,
        }
    }

    fn lookup<'a>(env: &'a [(String, String)], key: &str) -> Option<&'a str> {
        env.iter()
            .find(|(name, _)| name == key)
            .map(|(_, value)| value.as_str())
    }

    #[test]
    fn the_pinned_env_matches_the_desktop_shell() {
        let probe = StaticProbe::new().with_env("PATH", "/custom/bin");
        let env = worker_env(&command(), 4825, None, Some(Path::new("/rt")), &probe);
        assert_eq!(lookup(&env, "LATTICEAI_HOST"), Some("127.0.0.1"));
        assert_eq!(lookup(&env, "LATTICEAI_PORT"), Some("4825"));
        for flag in SAFETY_OFF_FLAGS {
            assert_eq!(lookup(&env, flag), Some("false"), "{flag} must be off");
        }
        assert_eq!(
            lookup(&env, "PATH"),
            Some(format!("{WORKER_PATH_PREFIX}:/custom/bin").as_str())
        );
        assert_eq!(
            lookup(&env, "LATTICEAI_AGENT_ROOT"),
            Some("/rt/agent_workspace")
        );
    }

    #[test]
    fn every_supervised_worker_gets_the_agent_seam_opened() {
        for gateway in [None, Some(4825)] {
            let env = worker_env(&command(), 4899, gateway, None, &StaticProbe::new());
            assert_eq!(
                lookup(&env, AGENT_TOOL_SEAM_ENV),
                Some("1"),
                "the native loop cannot run against a closed seam"
            );
        }
        assert_eq!(AGENT_TOOL_SEAM_ENV, "LATTICEAI_AGENT_TOOL_SEAM");
        assert!(
            !SAFETY_OFF_FLAGS.contains(&AGENT_TOOL_SEAM_ENV),
            "the seam is opened, not pinned off"
        );
    }

    #[test]
    fn no_runtime_dir_means_no_agent_root_override() {
        let env = worker_env(&command(), 4825, None, None, &StaticProbe::new());
        assert_eq!(lookup(&env, "LATTICEAI_AGENT_ROOT"), None);
    }

    #[test]
    fn a_fronted_worker_is_told_to_trust_the_gateway_origin() {
        let env = worker_env(&command(), 4899, Some(4825), None, &StaticProbe::new());
        assert_eq!(
            lookup(&env, CSRF_TRUSTED_ORIGINS_ENV),
            Some("http://127.0.0.1:4825,http://localhost:4825"),
            "both spellings a browser can produce for the gateway port"
        );
        assert_eq!(
            lookup(&env, "LATTICEAI_PORT"),
            Some("4899"),
            "the worker still binds its own internal port"
        );
    }

    #[test]
    fn an_unfronted_worker_gets_no_extra_trusted_origin() {
        let env = worker_env(&command(), 4825, None, None, &StaticProbe::new());
        assert_eq!(
            lookup(&env, CSRF_TRUSTED_ORIGINS_ENV),
            None,
            "the direct topology has no second origin to trust"
        );
        assert_eq!(
            lookup(&env, CORS_ALLOWED_ORIGINS_ENV),
            None,
            "…and no second origin to answer cross-origin either"
        );
    }

    #[test]
    fn the_trusted_origin_list_is_the_comma_form_config_parses() {
        assert_eq!(
            csrf_trusted_origins(41234),
            "http://127.0.0.1:41234,http://localhost:41234"
        );
    }

    #[test]
    fn a_fronted_worker_answers_cross_origin_calls_from_the_gateway() {
        let env = worker_env(&command(), 4899, Some(4825), None, &StaticProbe::new());
        assert_eq!(
            lookup(&env, CORS_ALLOWED_ORIGINS_ENV),
            Some("http://127.0.0.1:4825,http://localhost:4825,http://[::1]:4825"),
            "every spelling a browser can produce for the gateway port"
        );
        assert_eq!(
            cors_allowed_origins(41234),
            "http://127.0.0.1:41234,http://localhost:41234,http://[::1]:41234"
        );
        assert!(
            !SAFETY_OFF_FLAGS.contains(&CORS_ALLOWED_ORIGINS_ENV),
            "this names loopback origins; LATTICEAI_CORS_ALLOW_NETWORK stays off"
        );
        assert_eq!(lookup(&env, "LATTICEAI_CORS_ALLOW_NETWORK"), Some("false"));
    }

    #[test]
    fn path_falls_back_to_the_fixed_prefix() {
        assert_eq!(worker_path(&StaticProbe::new()), WORKER_PATH_PREFIX);
    }

    #[test]
    fn python_path_is_absent_for_ambient_commands() {
        assert_eq!(worker_python_path(&command(), &StaticProbe::new()), None);
    }

    #[test]
    fn python_path_puts_the_bundle_first_then_cwd_then_inherited() {
        let mut cmd = command();
        cmd.python_root = Some(PathBuf::from("/bundle"));
        cmd.cwd = Some(PathBuf::from("/work"));
        let probe = StaticProbe::new().with_env("PYTHONPATH", "/inherited");
        let value = worker_python_path(&cmd, &probe).expect("python path");
        assert_eq!(value, "/bundle:/work:/inherited");
    }

    #[test]
    fn python_path_does_not_duplicate_a_cwd_equal_to_the_bundle() {
        let mut cmd = command();
        cmd.python_root = Some(PathBuf::from("/bundle"));
        cmd.cwd = Some(PathBuf::from("/bundle"));
        assert_eq!(
            worker_python_path(&cmd, &StaticProbe::new()).as_deref(),
            Some("/bundle")
        );
    }

    #[test]
    fn log_paths_keep_the_existing_sidecar_names() {
        let (out, err) = log_paths(Path::new("/home/u/.ltcai"));
        assert!(out.ends_with("desktop-sidecar.log"));
        assert!(err.ends_with("desktop-sidecar.err.log"));
    }

    #[test]
    fn logs_are_appended_not_truncated() {
        let dir = tempfile::tempdir().expect("tempdir");
        let path = dir.path().join("nested").join("sidecar.log");
        {
            use std::io::Write;
            let mut file = open_log(&path).expect("open");
            writeln!(file, "first").expect("write");
        }
        {
            use std::io::Write;
            let mut file = open_log(&path).expect("reopen");
            writeln!(file, "second").expect("write");
        }
        let body = std::fs::read_to_string(&path).expect("read");
        assert_eq!(body, "first\nsecond\n");
    }

    #[test]
    fn ltcai_home_requires_a_home_variable() {
        assert_eq!(ltcai_home(&StaticProbe::new()), None);
        let probe = StaticProbe::new().with_env("HOME", "/home/u");
        assert_eq!(ltcai_home(&probe), Some(PathBuf::from("/home/u/.ltcai")));
    }

    #[test]
    fn desktop_runtime_dir_is_created_under_ltcai() {
        let dir = tempfile::tempdir().expect("tempdir");
        let probe = StaticProbe::new().with_env("HOME", &dir.path().to_string_lossy());
        let runtime = desktop_runtime_dir(&probe).expect("runtime dir");
        assert!(runtime.is_dir());
        assert!(runtime.ends_with("desktop-runtime"));
    }
}
