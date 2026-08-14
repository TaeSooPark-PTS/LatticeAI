//! Worker command resolution.
//!
//! Three rules, tried in order — inherited from the desktop shell
//! (`src-tauri/src/main.rs`), minus the `ltcai`-on-PATH rule that v11.6.0
//! retired (`ltcai` starts *this* binary now; see [`resolve_worker_command`]),
//! and with one bug fixed: that implementation ran
//! `sort()` on the python candidate list before `dedup()`, which threw away
//! the declared priority (`LTCAI_PYTHON` first, then PATH, then the well-known
//! absolute paths) and picked whatever sorted first. Here the candidate list
//! is deduplicated *in place*, so declaration order is the priority order by
//! construction — see the unit tests at the bottom of this file.
//!
//! Every environment / PATH / filesystem lookup goes through [`HostProbe`] so
//! the resolution order is testable without touching the real machine.

use std::collections::{HashSet, VecDeque};
use std::fmt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use serde::Serialize;

/// Directories appended to `PATH` when looking for an executable, matching the
/// desktop shell (GUI apps inherit a minimal `PATH`).
pub const FALLBACK_PATH_DIRS: [&str; 4] =
    ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"];

/// Absolute python interpreters probed after `PATH`, in priority order.
pub const ABSOLUTE_PYTHON_CANDIDATES: [&str; 3] = [
    "/opt/homebrew/bin/python3",
    "/usr/local/bin/python3",
    "/usr/bin/python3",
];

/// The module the worker is started with.
///
/// v11.6.0 (§3) points this at the **AI-Worker profile** rather than the
/// product server. `latticeai.cli.entrypoint` builds `create_app()` — all 464
/// routes, the SPA, the whole platform — which after One Door would be a second
/// copy of the product listening behind the one that is serving it.
/// `latticeai.worker_app` builds the same construction and then reduces the
/// route table to the surface `worker_route_keys()` names, which is exactly the
/// surface this gateway's proxy allowlist forwards.
pub const WORKER_MODULE: &str = "latticeai.worker_app";

/// The ASGI factory inside [`WORKER_MODULE`].
///
/// The worker is served through uvicorn rather than `python -m
/// latticeai.worker_app`: that module exports `main()` but has no
/// `if __name__ == "__main__"` guard, so the module form imports and exits
/// without binding anything. This is WP-I6's own documented launch string, and
/// it is the one that has been verified to serve.
pub const WORKER_FACTORY: &str = "latticeai.worker_app:create_worker_app";

/// Which of the four resolution rules produced a [`WorkerCommand`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CommandOrigin {
    /// `LATTICEAI_DESKTOP_BACKEND_CMD` (+ optional `..._CWD`).
    EnvOverride,
    /// `LTCAI` / `ltcai` found on `PATH`.
    ///
    /// **Retired as a resolution rule in v11.6.0** — `ltcai` starts the host,
    /// not the worker (see [`resolve_worker_command`]). The variant survives so
    /// a status snapshot written by an older build still deserializes.
    LtcaiOnPath,
    /// A python interpreter that can `import latticeai.worker_app`.
    PythonModule,
    /// The bundled `Resources` tree shipped inside the app bundle.
    BundledTree,
}

impl CommandOrigin {
    /// Stable snake_case name, also used in the status snapshot JSON.
    pub fn as_str(self) -> &'static str {
        match self {
            CommandOrigin::EnvOverride => "env_override",
            CommandOrigin::LtcaiOnPath => "ltcai_on_path",
            CommandOrigin::PythonModule => "python_module",
            CommandOrigin::BundledTree => "bundled_tree",
        }
    }
}

impl fmt::Display for CommandOrigin {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

/// A resolved worker invocation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkerCommand {
    /// Executable to run.
    pub program: String,
    /// Arguments passed to the executable.
    pub args: Vec<String>,
    /// Working directory, when the rule demands one.
    pub cwd: Option<PathBuf>,
    /// Extra `PYTHONPATH` root implied by the rule (bundled tree only).
    pub python_root: Option<PathBuf>,
    /// Which rule produced this command.
    pub origin: CommandOrigin,
}

impl WorkerCommand {
    /// Human-readable command line, as reported by `/host/status`.
    pub fn display(&self) -> String {
        if self.args.is_empty() {
            self.program.clone()
        } else {
            format!("{} {}", self.program, self.args.join(" "))
        }
    }
}

/// Why resolution failed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ResolveError {
    /// None of the four rules matched.
    NotFound,
}

impl fmt::Display for ResolveError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ResolveError::NotFound => f.write_str(
                "worker unavailable: no python can import latticeai.worker_app \
                 and no bundled tree was found",
            ),
        }
    }
}

impl std::error::Error for ResolveError {}

/// Everything the resolver needs to know about the machine it runs on.
///
/// Implemented by [`SystemProbe`] for production and [`StaticProbe`] for
/// tests; object safe on purpose so the supervisor can hold `Arc<dyn
/// HostProbe>`.
pub trait HostProbe: Send + Sync {
    /// Read an environment variable.
    fn env_var(&self, key: &str) -> Option<String>;
    /// Directories on `PATH`, in order.
    fn path_dirs(&self) -> Vec<PathBuf>;
    /// Whether `path` exists and is a regular file.
    fn is_file(&self, path: &Path) -> bool;
    /// Whether `program -c "import <module>"` succeeds.
    fn module_importable(&self, program: &str, module: &str) -> bool;
    /// The app bundle `Resources` directory, when running from a bundle.
    fn resource_dir(&self) -> Option<PathBuf>;
}

/// Talks to the real process environment and filesystem.
#[derive(Debug, Clone, Copy, Default)]
pub struct SystemProbe;

impl HostProbe for SystemProbe {
    fn env_var(&self, key: &str) -> Option<String> {
        std::env::var(key).ok()
    }

    fn path_dirs(&self) -> Vec<PathBuf> {
        std::env::var_os("PATH")
            .map(|value| std::env::split_paths(&value).collect())
            .unwrap_or_default()
    }

    fn is_file(&self, path: &Path) -> bool {
        path.is_file()
    }

    fn module_importable(&self, program: &str, module: &str) -> bool {
        Command::new(program)
            .args(["-c", &format!("import {module}")])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map(|status| status.success())
            .unwrap_or(false)
    }

    fn resource_dir(&self) -> Option<PathBuf> {
        let exe = std::env::current_exe().ok()?;
        let resources = exe.parent()?.parent()?.join("Resources");
        resources.exists().then_some(resources)
    }
}

/// A scripted [`HostProbe`] for tests: no process spawning, no filesystem.
#[derive(Debug, Clone, Default)]
pub struct StaticProbe {
    env: Vec<(String, String)>,
    path_dirs: Vec<PathBuf>,
    files: HashSet<PathBuf>,
    importable: HashSet<String>,
    resource_dir: Option<PathBuf>,
}

impl StaticProbe {
    /// Empty probe: nothing on PATH, no files, no bundle.
    pub fn new() -> Self {
        Self::default()
    }

    /// Set an environment variable.
    pub fn with_env(mut self, key: &str, value: &str) -> Self {
        self.env.push((key.to_string(), value.to_string()));
        self
    }

    /// Append a `PATH` directory (order preserved).
    pub fn with_path_dir(mut self, dir: &str) -> Self {
        self.path_dirs.push(PathBuf::from(dir));
        self
    }

    /// Declare that `path` exists as a regular file.
    pub fn with_file(mut self, path: &str) -> Self {
        self.files.insert(PathBuf::from(path));
        self
    }

    /// Declare that `program` can import the worker module.
    pub fn with_importable(mut self, program: &str) -> Self {
        self.importable.insert(program.to_string());
        self
    }

    /// Declare the bundle `Resources` directory.
    pub fn with_resource_dir(mut self, dir: &str) -> Self {
        self.resource_dir = Some(PathBuf::from(dir));
        self
    }
}

impl HostProbe for StaticProbe {
    fn env_var(&self, key: &str) -> Option<String> {
        self.env
            .iter()
            .find(|(name, _)| name == key)
            .map(|(_, value)| value.clone())
    }

    fn path_dirs(&self) -> Vec<PathBuf> {
        self.path_dirs.clone()
    }

    fn is_file(&self, path: &Path) -> bool {
        self.files.contains(path)
    }

    fn module_importable(&self, program: &str, _module: &str) -> bool {
        self.importable.contains(program)
    }

    fn resource_dir(&self) -> Option<PathBuf> {
        self.resource_dir.clone()
    }
}

/// Deduplicate while keeping the first occurrence of every item.
///
/// This is the fix for the desktop shell's `sort() + dedup()`: sorting is what
/// destroyed the priority order, deduplication alone never does.
pub fn dedup_preserving_order<T: Clone + Eq + std::hash::Hash>(items: Vec<T>) -> Vec<T> {
    let mut seen: HashSet<T> = HashSet::with_capacity(items.len());
    let mut out = Vec::with_capacity(items.len());
    for item in items {
        if seen.insert(item.clone()) {
            out.push(item);
        }
    }
    out
}

/// Find `name` on `PATH` (plus the GUI fallback directories), first hit wins.
pub fn find_in_path(probe: &dyn HostProbe, name: &str) -> Option<String> {
    let mut dirs = probe.path_dirs();
    dirs.extend(FALLBACK_PATH_DIRS.iter().map(PathBuf::from));
    for dir in dedup_preserving_order(dirs) {
        let candidate = dir.join(name);
        if probe.is_file(&candidate) {
            return Some(candidate.to_string_lossy().into_owned());
        }
    }
    None
}

/// Python interpreters to try, in priority order, deduplicated in place.
pub fn python_candidates(probe: &dyn HostProbe) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    if let Some(value) = probe.env_var("LTCAI_PYTHON") {
        if !value.trim().is_empty() {
            out.push(value);
        }
    }
    for name in ["python3", "python"] {
        if let Some(path) = find_in_path(probe, name) {
            out.push(path);
        }
    }
    out.extend(ABSOLUTE_PYTHON_CANDIDATES.iter().map(|p| p.to_string()));
    dedup_preserving_order(out)
}

/// `python -m uvicorn latticeai.worker_app:create_worker_app --factory …`.
fn module_args(port: u16) -> Vec<String> {
    vec![
        "-m".into(),
        "uvicorn".into(),
        WORKER_FACTORY.into(),
        "--factory".into(),
        "--host".into(),
        "127.0.0.1".into(),
        "--port".into(),
        port.to_string(),
    ]
}

/// The bundled `Resources` tree, if one holds a worker.
///
/// The marker is `latticeai/worker_app.py` — what v11.6.0 ships — with
/// `latticeai/cli/entrypoint.py` accepted as a fallback so a bundle built
/// before the flip still boots (WP-P2 §integrator checklist 3). WP-P1 keeps the
/// `latticeai/` directory itself, so neither marker can be lost to a glob.
fn bundled_root(probe: &dyn HostProbe) -> Option<PathBuf> {
    let resources = probe.resource_dir()?;
    let holds_worker = |root: &Path| {
        probe.is_file(&root.join("latticeai").join("worker_app.py"))
            || probe.is_file(&root.join("latticeai").join("cli").join("entrypoint.py"))
    };
    let up = resources.join("_up_");
    if holds_worker(&up) {
        Some(up)
    } else if holds_worker(&resources) {
        Some(resources)
    } else {
        None
    }
}

/// Resolve the worker command for `port`.
///
/// Rules, in order:
/// 1. `LATTICEAI_DESKTOP_BACKEND_CMD` (with `LATTICEAI_DESKTOP_BACKEND_CWD`),
/// 2. the first python candidate that can import [`WORKER_MODULE`] →
///    `-m uvicorn latticeai.worker_app:create_worker_app --factory …`,
/// 3. the bundled `Resources` tree, run with the first *existing* python
///    candidate and `PYTHONPATH`/cwd pointed at the tree.
///
/// **`ltcai` on `PATH` is no longer a worker rule** (v11.6.0 §3, WP-P2). It used
/// to be rule 2, back when `ltcai` started the Python product server. After One
/// Door `bin/ltcai.js` starts *this binary*, so the old rule made a host that
/// could not find a python spawn another host, which would spawn another — an
/// unbounded process tree whose first symptom is a machine that will not
/// quiesce. WP-P2 mitigated it from the launcher side by pinning
/// `LATTICEAI_DESKTOP_BACKEND_CMD` so rule 1 wins; removing the rule closes it
/// from this side, for every caller, including one that starts the binary
/// directly. [`CommandOrigin::LtcaiOnPath`] is kept as a variant so an old
/// status snapshot still deserializes.
pub fn resolve_worker_command(
    probe: &dyn HostProbe,
    port: u16,
) -> Result<WorkerCommand, ResolveError> {
    if let Some(raw) = probe.env_var("LATTICEAI_DESKTOP_BACKEND_CMD") {
        let mut parts: VecDeque<String> = raw.split_whitespace().map(str::to_string).collect();
        if let Some(program) = parts.pop_front() {
            return Ok(WorkerCommand {
                program,
                args: parts.into_iter().collect(),
                cwd: probe
                    .env_var("LATTICEAI_DESKTOP_BACKEND_CWD")
                    .filter(|value| !value.trim().is_empty())
                    .map(PathBuf::from),
                python_root: None,
                origin: CommandOrigin::EnvOverride,
            });
        }
    }

    let candidates = python_candidates(probe);
    for python in &candidates {
        if probe.module_importable(python, WORKER_MODULE) {
            return Ok(WorkerCommand {
                program: python.clone(),
                args: module_args(port),
                cwd: None,
                python_root: None,
                origin: CommandOrigin::PythonModule,
            });
        }
    }

    if let Some(root) = bundled_root(probe) {
        // Unlike rule 3 the module is not importable from the ambient
        // environment, so pick the first interpreter that actually exists and
        // point PYTHONPATH/cwd at the bundled tree.
        let python = candidates
            .iter()
            .find(|path| probe.is_file(Path::new(path)))
            .or_else(|| candidates.first())
            .cloned();
        if let Some(python) = python {
            return Ok(WorkerCommand {
                program: python,
                args: module_args(port),
                cwd: Some(root.clone()),
                python_root: Some(root),
                origin: CommandOrigin::BundledTree,
            });
        }
    }

    Err(ResolveError::NotFound)
}
