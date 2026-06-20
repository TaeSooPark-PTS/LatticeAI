use std::{
    env,
    fs::OpenOptions,
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::Mutex,
};

use serde::Serialize;
use tauri::{Manager, State};

struct BackendState {
    origin: String,
    command: String,
    cwd: Option<String>,
    child: Mutex<Option<Child>>,
    last_error: Mutex<Option<String>>,
}

#[derive(Serialize)]
struct BackendStatus {
    origin: String,
    command: String,
    cwd: Option<String>,
    running: bool,
    pid: Option<u32>,
    last_error: Option<String>,
}

struct BackendLaunch {
    command: String,
    program: String,
    args: Vec<String>,
    cwd: Option<PathBuf>,
}

#[tauri::command]
fn backend_origin(state: State<'_, BackendState>) -> String {
    state.origin.clone()
}

#[tauri::command]
fn backend_status(state: State<'_, BackendState>) -> BackendStatus {
    status_from_state(&state)
}

#[tauri::command]
fn restart_backend(state: State<'_, BackendState>) -> BackendStatus {
    kill_backend(&state);
    let launch = backend_launch(&state.origin);
    match spawn_backend(&state.origin, &launch) {
        Ok(child) => {
            if let Ok(mut slot) = state.child.lock() {
                *slot = child;
            }
            set_error(&state, None);
        }
        Err(err) => set_error(&state, Some(err)),
    }
    status_from_state(&state)
}

#[tauri::command]
fn shutdown_backend(state: State<'_, BackendState>) -> BackendStatus {
    kill_backend(&state);
    status_from_state(&state)
}

fn split_command(command: &str) -> Vec<String> {
    command.split_whitespace().map(|part| part.to_string()).collect()
}

fn command_in_path(name: &str) -> Option<String> {
    let mut dirs: Vec<PathBuf> = env::var_os("PATH")
        .map(|value| env::split_paths(&value).collect())
        .unwrap_or_default();
    dirs.extend([
        PathBuf::from("/opt/homebrew/bin"),
        PathBuf::from("/usr/local/bin"),
        PathBuf::from("/usr/bin"),
        PathBuf::from("/bin"),
    ]);
    for dir in dirs {
        let candidate = dir.join(name);
        if candidate.is_file() {
            return Some(candidate.to_string_lossy().to_string());
        }
    }
    None
}

fn python_candidates() -> Vec<String> {
    let mut out = Vec::new();
    if let Ok(value) = env::var("LTCAI_PYTHON") {
        out.push(value);
    }
    for name in ["python3", "python"] {
        if let Some(path) = command_in_path(name) {
            out.push(path);
        }
    }
    out.extend([
        "/opt/homebrew/bin/python3".to_string(),
        "/usr/local/bin/python3".to_string(),
        "/usr/bin/python3".to_string(),
    ]);
    out.sort();
    out.dedup();
    out
}

fn module_importable(python: &str, module: &str) -> bool {
    Command::new(python)
        .args(["-c", &format!("import {module}")])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|status| status.success())
        .unwrap_or(false)
}

fn resource_dir() -> Option<PathBuf> {
    let exe = env::current_exe().ok()?;
    let macos_dir = exe.parent()?;
    let contents_dir = macos_dir.parent()?;
    let resources = contents_dir.join("Resources");
    if resources.exists() {
        Some(resources)
    } else {
        None
    }
}

fn bundled_python_root() -> Option<PathBuf> {
    let resources = resource_dir()?;
    let up = resources.join("_up_");
    if up.join("ltcai_cli.py").is_file() {
        Some(up)
    } else if resources.join("ltcai_cli.py").is_file() {
        Some(resources)
    } else {
        None
    }
}

fn desktop_runtime_dir() -> Option<PathBuf> {
    let home = env::var("HOME").ok()?;
    let dir = PathBuf::from(home).join(".ltcai").join("desktop-runtime");
    let _ = std::fs::create_dir_all(&dir);
    Some(dir)
}

fn python_path_env(launch: &BackendLaunch) -> Option<String> {
    let mut paths: Vec<PathBuf> = Vec::new();
    if let Some(resources) = bundled_python_root() {
        paths.push(resources);
    }
    if let Some(cwd) = &launch.cwd {
        if !paths.iter().any(|path| path == cwd) {
            paths.push(cwd.clone());
        }
    }
    if let Some(existing) = env::var_os("PYTHONPATH") {
        paths.extend(env::split_paths(&existing));
    }
    env::join_paths(paths).ok().map(|value| value.to_string_lossy().to_string())
}

fn backend_launch(origin: &str) -> BackendLaunch {
    let port = origin.rsplit(':').next().unwrap_or("8765").to_string();
    if let Ok(command) = env::var("LATTICEAI_DESKTOP_BACKEND_CMD") {
        let parts = split_command(&command);
        if let Some(program) = parts.first() {
            return BackendLaunch {
                command,
                program: program.clone(),
                args: parts[1..].to_vec(),
                cwd: env::var("LATTICEAI_DESKTOP_BACKEND_CWD").ok().map(PathBuf::from),
            };
        }
    }

    for name in ["LTCAI", "ltcai"] {
        if let Some(program) = command_in_path(name) {
            return BackendLaunch {
                command: format!("{program} --host 127.0.0.1 --port {port}"),
                program,
                args: vec!["--host".into(), "127.0.0.1".into(), "--port".into(), port],
                cwd: None,
            };
        }
    }

    for python in python_candidates() {
        if module_importable(&python, "ltcai_cli") {
            return BackendLaunch {
                command: format!("{python} -m ltcai_cli --host 127.0.0.1 --port {port}"),
                program: python,
                args: vec![
                    "-m".into(),
                    "ltcai_cli".into(),
                    "--host".into(),
                    "127.0.0.1".into(),
                    "--port".into(),
                    port,
                ],
                cwd: None,
            };
        }
    }

    if let Some(resources) = bundled_python_root() {
        let launcher = resources.join("ltcai_cli.py");
        if launcher.is_file() {
            if let Some(python) = python_candidates().into_iter().next() {
                return BackendLaunch {
                    command: format!("{python} {} --host 127.0.0.1 --port {port}", launcher.display()),
                    program: python,
                    args: vec![
                        launcher.to_string_lossy().to_string(),
                        "--host".into(),
                        "127.0.0.1".into(),
                        "--port".into(),
                        port,
                    ],
                    cwd: None,
                };
            }
        }
    }

    BackendLaunch {
        command: "unavailable: LTCAI executable or importable ltcai_cli module not found".to_string(),
        program: String::new(),
        args: Vec::new(),
        cwd: None,
    }
}

fn set_error(state: &BackendState, err: Option<String>) {
    if let Ok(mut last) = state.last_error.lock() {
        *last = err;
    }
}

fn spawn_backend(origin: &str, launch: &BackendLaunch) -> Result<Option<Child>, String> {
    if env::var("LATTICEAI_DESKTOP_NO_BACKEND").is_ok() {
        return Ok(None);
    }
    if launch.program.is_empty() {
        return Err("Desktop backend unavailable: LTCAI executable or importable ltcai_cli module not found.".to_string());
    }

    let mut cmd = Command::new(&launch.program);
    cmd.args(&launch.args)
        .env("LATTICEAI_HOST", "127.0.0.1")
        .env("LATTICEAI_PORT", origin.rsplit(':').next().unwrap_or("8765"))
        .env("LATTICEAI_ENABLE_TELEGRAM", "false")
        .env("LATTICEAI_AUTOLOAD_MODELS", "false")
        .env("LATTICEAI_ALLOW_MODEL_DOWNLOADS", "false")
        .env("LATTICEAI_CORS_ALLOW_NETWORK", "false")
        .env("LATTICEAI_ENABLE_EXTERNAL_CONNECTORS", "false")
        .env("LATTICEAI_TUNNEL", "false")
        .env(
            "PATH",
            format!(
                "{}:{}",
                "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                env::var("PATH").unwrap_or_default()
            ),
        );
    if let Some(runtime_dir) = desktop_runtime_dir() {
        cmd.env("LATTICEAI_AGENT_ROOT", runtime_dir.join("agent_workspace"));
        if launch.cwd.is_none() {
            cmd.current_dir(&runtime_dir);
        }
    }
    if let Some(python_path) = python_path_env(launch) {
        cmd.env("PYTHONPATH", python_path);
    }
    if let Some(cwd) = &launch.cwd {
        cmd.current_dir(cwd);
    }
    if let Ok(home) = env::var("HOME") {
        let log_dir = PathBuf::from(home).join(".ltcai");
        let _ = std::fs::create_dir_all(&log_dir);
        if let Ok(file) = OpenOptions::new().create(true).append(true).open(log_dir.join("desktop-sidecar.log")) {
            cmd.stdout(Stdio::from(file));
        } else {
            cmd.stdout(Stdio::null());
        }
        if let Ok(file) = OpenOptions::new().create(true).append(true).open(log_dir.join("desktop-sidecar.err.log")) {
            cmd.stderr(Stdio::from(file));
        } else {
            cmd.stderr(Stdio::null());
        }
    } else {
        cmd.stdout(Stdio::null()).stderr(Stdio::null());
    }

    cmd.spawn()
        .map(Some)
        .map_err(|err| format!("Failed to start desktop backend '{}': {}", launch.command, err))
}

fn kill_backend(state: &BackendState) {
    if let Ok(mut child) = state.child.lock() {
        if let Some(mut process) = child.take() {
            let _ = process.kill();
            let _ = process.wait();
        }
    }
}

fn status_from_state(state: &BackendState) -> BackendStatus {
    let mut running = false;
    let mut pid = None;
    if let Ok(mut child_slot) = state.child.lock() {
        if let Some(child) = child_slot.as_mut() {
            match child.try_wait() {
                Ok(Some(status)) => {
                    set_error(state, Some(format!("Desktop backend exited with status {}", status)));
                    *child_slot = None;
                }
                Ok(None) => {
                    running = true;
                    pid = Some(child.id());
                }
                Err(err) => set_error(state, Some(format!("Unable to inspect desktop backend: {}", err))),
            }
        }
    }
    let last_error = state.last_error.lock().ok().and_then(|guard| guard.clone());
    BackendStatus {
        origin: state.origin.clone(),
        command: state.command.clone(),
        cwd: state.cwd.clone(),
        running,
        pid,
        last_error,
    }
}

fn wait_for_backend(origin: &str) {
    let host_port = origin
        .trim_start_matches("http://")
        .trim_start_matches("https://")
        .split('/')
        .next()
        .unwrap_or("127.0.0.1:8765")
        .to_string();
    for _ in 0..45 {
        if std::net::TcpStream::connect(&host_port).is_ok() {
            return;
        }
        std::thread::sleep(std::time::Duration::from_millis(500));
    }
}

fn main() {
    let origin = env::var("LATTICEAI_DESKTOP_BACKEND_ORIGIN")
        .unwrap_or_else(|_| "http://127.0.0.1:8765".to_string());
    let launch = backend_launch(&origin);
    let command = launch.command.clone();
    let cwd = launch.cwd.as_ref().map(|path| path.to_string_lossy().to_string());
    let (child, last_error) = match spawn_backend(&origin, &launch) {
        Ok(child) => (child, None),
        Err(err) => (None, Some(err)),
    };
    tauri::Builder::default()
        .manage(BackendState {
            origin,
            command,
            cwd,
            child: Mutex::new(child),
            last_error: Mutex::new(last_error),
        })
        .invoke_handler(tauri::generate_handler![
            backend_origin,
            backend_status,
            restart_backend,
            shutdown_backend
        ])
        .setup(|app| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.set_title("Lattice AI");
                let _ = window.show();
                let _ = window.set_focus();
                let origin = app.state::<BackendState>().origin.clone();
                let target = format!("{}/app", origin.trim_end_matches('/'));
                let window_for_nav = window.clone();
                std::thread::spawn(move || {
                    wait_for_backend(&origin);
                    if let Ok(url) = tauri::Url::parse(&target) {
                        let _ = window_for_nav.navigate(url);
                    }
                });
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::CloseRequested { .. }) {
                if let Some(state) = window.try_state::<BackendState>() {
                    kill_backend(&state);
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("failed to build Lattice AI desktop shell")
        .run(|app_handle, event| {
            if matches!(
                event,
                tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit
            ) {
                if let Some(state) = app_handle.try_state::<BackendState>() {
                    kill_backend(&state);
                }
            }
        });
}
