use std::collections::{HashSet, VecDeque};
use std::fmt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use serde::Serialize;

pub const FALLBACK_PATH_DIRS: [&str; 4] =
    ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"];

pub const ABSOLUTE_PYTHON_CANDIDATES: [&str; 3] = [
    "/opt/homebrew/bin/python3",
    "/usr/local/bin/python3",
    "/usr/bin/python3",
];

pub const WORKER_MODULE: &str = "latticeai.worker_app";

pub const WORKER_FACTORY: &str = "latticeai.worker_app:create_worker_app";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CommandOrigin {
    EnvOverride,
    LtcaiOnPath,
    PythonModule,
    BundledTree,
}

impl CommandOrigin {
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

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkerCommand {
    pub program: String,
    pub args: Vec<String>,
    pub cwd: Option<PathBuf>,
    pub python_root: Option<PathBuf>,
    pub origin: CommandOrigin,
}

impl WorkerCommand {
    pub fn display(&self) -> String {
        if self.args.is_empty() {
            self.program.clone()
        } else {
            format!("{} {}", self.program, self.args.join(" "))
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ResolveError {
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

pub trait HostProbe: Send + Sync {
    fn env_var(&self, key: &str) -> Option<String>;
    fn path_dirs(&self) -> Vec<PathBuf>;
    fn is_file(&self, path: &Path) -> bool;
    fn module_importable(&self, program: &str, module: &str) -> bool;
    fn resource_dir(&self) -> Option<PathBuf>;
}

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

pub struct EnvOverlayProbe {
    inner: std::sync::Arc<dyn HostProbe>,
    overrides: Vec<(String, String)>,
}

impl EnvOverlayProbe {
    pub fn new(inner: std::sync::Arc<dyn HostProbe>, overrides: Vec<(String, String)>) -> Self {
        Self { inner, overrides }
    }
}

impl HostProbe for EnvOverlayProbe {
    fn env_var(&self, key: &str) -> Option<String> {
        if let Some((_, value)) = self.overrides.iter().rev().find(|(k, _)| k == key) {
            return Some(value.clone());
        }
        self.inner.env_var(key)
    }

    fn path_dirs(&self) -> Vec<PathBuf> {
        self.inner.path_dirs()
    }

    fn is_file(&self, path: &Path) -> bool {
        self.inner.is_file(path)
    }

    fn module_importable(&self, program: &str, module: &str) -> bool {
        self.inner.module_importable(program, module)
    }

    fn resource_dir(&self) -> Option<PathBuf> {
        self.inner.resource_dir()
    }
}

#[derive(Debug, Clone, Default)]
pub struct StaticProbe {
    env: Vec<(String, String)>,
    path_dirs: Vec<PathBuf>,
    files: HashSet<PathBuf>,
    importable: HashSet<String>,
    resource_dir: Option<PathBuf>,
}

impl StaticProbe {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn with_env(mut self, key: &str, value: &str) -> Self {
        self.env.push((key.to_string(), value.to_string()));
        self
    }

    pub fn with_path_dir(mut self, dir: &str) -> Self {
        self.path_dirs.push(PathBuf::from(dir));
        self
    }

    pub fn with_file(mut self, path: &str) -> Self {
        self.files.insert(PathBuf::from(path));
        self
    }

    pub fn with_importable(mut self, program: &str) -> Self {
        self.importable.insert(program.to_string());
        self
    }

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

pub fn python_candidates(probe: &dyn HostProbe) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for env_key in ["LATTICEAI_WORKER_PYTHON", "LTCAI_PYTHON"] {
        if let Some(value) = probe.env_var(env_key) {
            if !value.trim().is_empty() {
                out.push(value);
            }
        }
    }
    if let Some(resources) = probe.resource_dir() {
        for rel in [
            Path::new("python").join("bin").join("python3"),
            Path::new("python").join("bin").join("python"),
            Path::new("python").join("Scripts").join("python.exe"),
            Path::new(".venv").join("bin").join("python"),
            Path::new(".venv").join("Scripts").join("python.exe"),
        ] {
            let candidate = resources.join(rel);
            if probe.is_file(&candidate) {
                out.push(candidate.to_string_lossy().into_owned());
            }
        }
    }
    if let Some(home) = probe
        .env_var("HOME")
        .or_else(|| probe.env_var("USERPROFILE"))
        .filter(|h| !h.trim().is_empty())
    {
        let home_dir = PathBuf::from(home);
        for venv_name in ["venv", "npm-python"] {
            for rel in [
                Path::new("bin").join("python3"),
                Path::new("bin").join("python"),
                Path::new("Scripts").join("python.exe"),
            ] {
                let candidate = home_dir.join(".ltcai").join(venv_name).join(rel);
                if probe.is_file(&candidate) {
                    out.push(candidate.to_string_lossy().into_owned());
                }
            }
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
        let python = candidates
            .iter()
            .find(|path| probe.module_importable(path, "uvicorn"))
            .or_else(|| {
                candidates
                    .iter()
                    .find(|path| probe.is_file(Path::new(path)))
            })
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
