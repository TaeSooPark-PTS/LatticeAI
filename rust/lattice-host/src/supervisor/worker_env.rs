//! The worker's pinned environment and log destinations.
//!
//! Replicated from `src-tauri/src/main.rs` (the spawn block) so the supervised
//! worker keeps exactly the same safety posture: loopback only, no telegram,
//! no model autoload or downloads, no network CORS, no external connectors, no
//! tunnel, and an agent root confined to `~/.ltcai/desktop-runtime`.

use std::fs::{File, OpenOptions};
use std::path::{Path, PathBuf};

use super::command::{HostProbe, WorkerCommand};

/// PATH prefix given to the worker — GUI processes inherit a minimal PATH.
pub const WORKER_PATH_PREFIX: &str =
    "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin";

/// Safety flags pinned to `false` for every supervised worker.
pub const SAFETY_OFF_FLAGS: [&str; 6] = [
    "LATTICEAI_ENABLE_TELEGRAM",
    "LATTICEAI_AUTOLOAD_MODELS",
    "LATTICEAI_ALLOW_MODEL_DOWNLOADS",
    "LATTICEAI_CORS_ALLOW_NETWORK",
    "LATTICEAI_ENABLE_EXTERNAL_CONNECTORS",
    "LATTICEAI_TUNNEL",
];

/// Browser origins the worker will accept cookie-authenticated writes from
/// (`latticeai/core/config.py`, parsed as a comma-separated list).
pub const CSRF_TRUSTED_ORIGINS_ENV: &str = "LATTICEAI_CSRF_TRUSTED_ORIGINS";

/// Browser origins the worker will answer *cross-origin* requests from
/// (`latticeai/core/config.py` → `cors_extra_origins`, additive to the
/// worker's own origin).
pub const CORS_ALLOWED_ORIGINS_ENV: &str = "LATTICEAI_CORS_ALLOWED_ORIGINS";

/// Environment variable naming the agent workspace root. An explicit value
/// (process env or supervisor `extra_env`) wins over the desktop-runtime
/// default this module otherwise pins.
pub const AGENT_ROOT_ENV: &str = "LATTICEAI_AGENT_ROOT";

/// Opens the worker's AI-worker seam (`/agent/llm`, `/agent/tool`,
/// `/agent/change-proposal`) for the native loop orchestrator.
///
/// Off by default in the worker, and injected **only here**: these endpoints
/// exist for `lattice-agent`'s loop, and a worker nobody supervises has no
/// reason to expose a bare completion endpoint or a loop-scoped tool dispatch.
/// The host opening it for its own child is what makes "the kernel decides, the
/// worker executes" a boundary rather than a convention — and the worker keeps
/// its mode-invariant guards behind the seam regardless, so opening it widens
/// *reachability*, never authority.
pub const AGENT_TOOL_SEAM_ENV: &str = "LATTICEAI_AGENT_TOOL_SEAM";

/// The origins a worker must trust to be usable through a front door.
///
/// The CSRF guard builds its default trust set from the **worker's own** host
/// and port (`csrf.py::_default_origins`), and the gateway strips `Host` as a
/// hop-by-hop header, so a page served on the gateway port posts an `Origin`
/// the worker has never heard of and every cookie-authenticated write is
/// rejected. That is the single blocker to running the gateway as the front
/// door, and this is the whole fix: name the gateway's origin, in both
/// spellings a browser can produce for loopback.
///
/// **Still load-bearing after One Door**, and the reasoning is worth writing
/// down because the obvious conclusion is the wrong one. The gateway now owns
/// the browser-facing surface and applies its own Origin guard, so it is
/// tempting to stop injecting this. But the proxy allowlist still forwards
/// browser-facing writes — `POST /models/load`, `POST /engines/prepare-model`,
/// `DELETE /models/unload/{model_id}` — and a proxied request arrives at the
/// worker carrying the browser's session cookie *and* its
/// `Origin: …:{gateway port}`.
/// Without this variable the worker's own guard sees a cross-site origin and
/// answers 403, so loading a model from the SPA would stop working. WP-I2 §4
/// states the rule: do not drop it while any browser-facing path proxies.
pub fn csrf_trusted_origins(gateway_port: u16) -> String {
    format!("http://127.0.0.1:{gateway_port},http://localhost:{gateway_port}")
}

/// The origins a worker must answer *cross-origin* requests from to be usable
/// through a front door.
///
/// The CSRF injection above fixes cookie-authenticated writes from a page the
/// gateway served. This fixes the other half: a caller on a **different**
/// origin — the browser extension, the VS Code extension, any local tool —
/// sends a preflight, and `CORSMiddleware`'s allowlist is built from the
/// *worker's* own port, so the gateway port it was pointed at was rejected with
/// no `Access-Control-Allow-Origin` at all. In the desktop topology the gateway
/// owns 4825, the port those clients have always used, so this is the one that
/// makes them keep working.
///
/// Three spellings, because all three are what a browser can produce for
/// loopback: `[::1]` is added here and not in the CSRF list only because the
/// worker folds its CORS allowlist into the CSRF trust set anyway
/// (`runtime/web_runtime.py`), so naming it once covers both.
///
/// After One Door no browser talks to the worker cross-origin — the SPA is
/// same-origin with the gateway, and the extensions point at the gateway port
/// too — so the `Access-Control-Allow-Origin` half of this is no longer reached
/// in the product topology. It is kept for the half that is: the worker merges
/// this list into its CSRF trust set, so dropping it would quietly narrow the
/// trust the variable above exists to widen. One variable, two effects; only
/// one of them is now dead, and removing the pair to retire the dead half would
/// take the live half with it.
pub fn cors_allowed_origins(gateway_port: u16) -> String {
    format!(
        "http://127.0.0.1:{gateway_port},http://localhost:{gateway_port},http://[::1]:{gateway_port}"
    )
}

/// `~/.ltcai`, the directory that holds logs and the desktop runtime.
pub fn ltcai_home(probe: &dyn HostProbe) -> Option<PathBuf> {
    probe
        .env_var("HOME")
        .filter(|home| !home.trim().is_empty())
        .map(|home| PathBuf::from(home).join(".ltcai"))
}

/// `~/.ltcai/desktop-runtime`, created on demand.
pub fn desktop_runtime_dir(probe: &dyn HostProbe) -> Option<PathBuf> {
    let dir = ltcai_home(probe)?.join("desktop-runtime");
    let _ = std::fs::create_dir_all(&dir);
    Some(dir)
}

/// `(stdout, stderr)` log paths inside `dir`, matching the existing desktop
/// sidecar names so nothing that tails those files breaks.
pub fn log_paths(dir: &Path) -> (PathBuf, PathBuf) {
    (
        dir.join("desktop-sidecar.log"),
        dir.join("desktop-sidecar.err.log"),
    )
}

/// Open a log file for appending, creating parents as needed.
pub fn open_log(path: &Path) -> std::io::Result<File> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    OpenOptions::new().create(true).append(true).open(path)
}

/// `PATH` handed to the worker: the fixed prefix, then whatever we inherited.
pub fn worker_path(probe: &dyn HostProbe) -> String {
    match probe.env_var("PATH") {
        Some(existing) if !existing.is_empty() => format!("{WORKER_PATH_PREFIX}:{existing}"),
        _ => WORKER_PATH_PREFIX.to_string(),
    }
}

/// `PYTHONPATH` for the worker: bundled root (if any), then the working
/// directory, then the inherited value. `None` when there is nothing to add.
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

/// The full environment pinned onto the worker process, in a stable order.
///
/// `runtime_dir` is where `LATTICEAI_AGENT_ROOT` is rooted; when `None` the
/// agent root is left untouched (the worker falls back to its own default).
///
/// `gateway_port` is the front door this worker sits behind, when it sits
/// behind one. `None` — the direct topology, where the browser talks to the
/// worker's own port — injects nothing, because there is no second origin to
/// trust and inventing one would widen the CSRF policy for no reason.
///
/// [`AGENT_TOOL_SEAM_ENV`] is set for **every** supervised worker, fronted or
/// not: the loop orchestrator runs in this process and talks to this child over
/// loopback in either topology.
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

    /// The seam is the loop's only way to reach a model or a tool, and the
    /// worker refuses it (404) unless the host says so.
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

    /// The front-door unblocker (plan §2a): a worker fronted by the gateway is
    /// told to trust the gateway's origin, because its own CSRF default only
    /// knows the port it bound.
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

    /// The other half of the front-door unblocker: a preflight from the gateway
    /// origin used to come back 400 with no `Access-Control-Allow-Origin`,
    /// which in the desktop topology means the browser extension and the VS
    /// Code extension — both pointed at 4825, the port the *gateway* now owns.
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
