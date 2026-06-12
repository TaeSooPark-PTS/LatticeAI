use std::{
    env,
    process::{Child, Command, Stdio},
    sync::Mutex,
};

use tauri::{Manager, State};

struct BackendState {
    origin: String,
    child: Mutex<Option<Child>>,
}

#[tauri::command]
fn backend_origin(state: State<'_, BackendState>) -> String {
    state.origin.clone()
}

fn split_command(command: &str) -> Vec<String> {
    command
        .split_whitespace()
        .map(|part| part.to_string())
        .collect()
}

fn spawn_backend(origin: &str) -> Option<Child> {
    if env::var("LATTICEAI_DESKTOP_NO_BACKEND").is_ok() {
        return None;
    }
    let command = env::var("LATTICEAI_DESKTOP_BACKEND_CMD")
        .unwrap_or_else(|_| "python3 ltcai_cli.py --host 127.0.0.1 --port 8765".to_string());
    let parts = split_command(&command);
    if parts.is_empty() {
        return None;
    }
    let mut cmd = Command::new(&parts[0]);
    cmd.args(&parts[1..])
        .env("LATTICEAI_HOST", "127.0.0.1")
        .env("LATTICEAI_PORT", origin.rsplit(':').next().unwrap_or("8765"))
        .env("LATTICEAI_TUNNEL", "false")
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    if let Ok(cwd) = env::var("LATTICEAI_DESKTOP_BACKEND_CWD") {
        cmd.current_dir(cwd);
    }
    cmd.spawn().ok()
}

fn main() {
    let origin = env::var("LATTICEAI_DESKTOP_BACKEND_ORIGIN")
        .unwrap_or_else(|_| "http://127.0.0.1:8765".to_string());
    let child = spawn_backend(&origin);
    tauri::Builder::default()
        .manage(BackendState {
            origin,
            child: Mutex::new(child),
        })
        .invoke_handler(tauri::generate_handler![backend_origin])
        .setup(|app| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.set_title("Lattice AI");
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::CloseRequested { .. }) {
                if let Some(state) = window.try_state::<BackendState>() {
                    if let Ok(mut child) = state.child.lock() {
                        if let Some(process) = child.as_mut() {
                            let _ = process.kill();
                        }
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("failed to run Lattice AI desktop shell");
}
