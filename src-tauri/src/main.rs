use std::{
    env,
    process::{Child, Command, Stdio},
    sync::Mutex,
};

use serde::Serialize;
use tauri::{Manager, State};

struct BackendState {
    origin: String,
    command: String,
    child: Mutex<Option<Child>>,
    last_error: Mutex<Option<String>>,
}

#[derive(Serialize)]
struct BackendStatus {
    origin: String,
    command: String,
    running: bool,
    pid: Option<u32>,
    last_error: Option<String>,
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
    match spawn_backend(&state.origin, &state.command) {
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
    command
        .split_whitespace()
        .map(|part| part.to_string())
        .collect()
}

fn backend_command() -> String {
    env::var("LATTICEAI_DESKTOP_BACKEND_CMD")
        .unwrap_or_else(|_| "python3 ltcai_cli.py --host 127.0.0.1 --port 8765".to_string())
}

fn set_error(state: &BackendState, err: Option<String>) {
    if let Ok(mut last) = state.last_error.lock() {
        *last = err;
    }
}

fn spawn_backend(origin: &str, command: &str) -> Result<Option<Child>, String> {
    if env::var("LATTICEAI_DESKTOP_NO_BACKEND").is_ok() {
        return Ok(None);
    }
    let parts = split_command(&command);
    if parts.is_empty() {
        return Err("Desktop backend command is empty.".to_string());
    }
    let mut cmd = Command::new(&parts[0]);
    cmd.args(&parts[1..])
        .env("LATTICEAI_HOST", "127.0.0.1")
        .env("LATTICEAI_PORT", origin.rsplit(':').next().unwrap_or("8765"))
        .env("LATTICEAI_ENABLE_TELEGRAM", "false")
        .env("LATTICEAI_AUTOLOAD_MODELS", "false")
        .env("LATTICEAI_CORS_ALLOW_NETWORK", "false")
        .env("LATTICEAI_TUNNEL", "false")
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    if let Ok(cwd) = env::var("LATTICEAI_DESKTOP_BACKEND_CWD") {
        cmd.current_dir(cwd);
    }
    cmd.spawn()
        .map(Some)
        .map_err(|err| format!("Failed to start desktop backend '{}': {}", parts[0], err))
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
    let last_error = state
        .last_error
        .lock()
        .ok()
        .and_then(|guard| guard.clone());
    BackendStatus {
        origin: state.origin.clone(),
        command: state.command.clone(),
        running,
        pid,
        last_error,
    }
}

fn main() {
    let origin = env::var("LATTICEAI_DESKTOP_BACKEND_ORIGIN")
        .unwrap_or_else(|_| "http://127.0.0.1:8765".to_string());
    let command = backend_command();
    let (child, last_error) = match spawn_backend(&origin, &command) {
        Ok(child) => (child, None),
        Err(err) => (None, Some(err)),
    };
    tauri::Builder::default()
        .manage(BackendState {
            origin,
            command,
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
        .run(tauri::generate_context!())
        .expect("failed to run Lattice AI desktop shell");
}
