mod backend;
mod folder;
mod topology;

use std::sync::Arc;

use backend::{BackendStatus, DesktopBackend};
use tauri::{Manager, State};

#[tauri::command]
fn backend_origin(state: State<'_, Arc<DesktopBackend>>) -> String {
    state.origin().to_string()
}

#[tauri::command]
async fn backend_status(app: tauri::AppHandle) -> BackendStatus {
    let state = Arc::clone(&app.state::<Arc<DesktopBackend>>());
    state.status().await
}

#[tauri::command]
async fn restart_backend(state: State<'_, Arc<DesktopBackend>>) -> Result<BackendStatus, String> {
    state.restart().await
}

#[tauri::command]
async fn shutdown_backend(app: tauri::AppHandle) -> BackendStatus {
    let state = Arc::clone(&app.state::<Arc<DesktopBackend>>());
    state.stop().await
}

#[tauri::command]
async fn select_folder() -> Result<Option<String>, String> {
    folder::select_folder().await
}

fn boot(app: &tauri::App) {
    let Some(window) = app.get_webview_window("main") else {
        eprintln!("lattice-ai-desktop: main webview window not found during boot");
        return;
    };
    if let Err(err) = window.set_title("Lattice AI") {
        eprintln!("lattice-ai-desktop: failed to set window title: {err}");
    }
    if let Err(err) = window.show() {
        eprintln!("lattice-ai-desktop: failed to show window: {err}");
    }
    if let Err(err) = window.set_focus() {
        eprintln!("lattice-ai-desktop: failed to focus window: {err}");
    }

    let shell = Arc::clone(&app.state::<Arc<DesktopBackend>>());
    tauri::async_runtime::spawn(async move {
        shell.start().await;
        if let Some(reason) = shell.gateway_error() {
            eprintln!("lattice-ai-desktop: staying on the bundled shell — {reason}");
            return;
        }
        shell.wait_until_serving().await;
        match tauri::Url::parse(&shell.app_url()) {
            Ok(url) => {
                if let Err(err) = window.navigate(url) {
                    eprintln!("lattice-ai-desktop: failed to navigate main window: {err}");
                }
            }
            Err(err) => {
                eprintln!(
                    "lattice-ai-desktop: invalid app_url {}: {err}",
                    shell.app_url()
                );
            }
        }
    });
}

fn stop_worker<R: tauri::Runtime, M: Manager<R>>(manager: &M) {
    if let Some(shell) = manager.try_state::<Arc<DesktopBackend>>() {
        let shell = Arc::clone(&shell);
        tauri::async_runtime::block_on(async move {
            shell.stop().await;
            shell.stop_gateway().await;
        });
    }
}

fn main() {
    let shell = match DesktopBackend::resolve() {
        Ok(shell) => Arc::new(shell),
        Err(err) => {
            eprintln!("lattice-ai-desktop: cannot prepare the backend: {err}");
            std::process::exit(1);
        }
    };
    eprintln!(
        "lattice-ai-desktop: backend {} [{}] ({})",
        shell.origin(),
        shell.topology().as_str(),
        if shell.supervised() {
            "supervised here"
        } else {
            "external — nothing will be spawned"
        }
    );

    tauri::Builder::default()
        .manage(shell)
        .invoke_handler(tauri::generate_handler![
            backend_origin,
            backend_status,
            restart_backend,
            shutdown_backend,
            select_folder
        ])
        .setup(|app| {
            boot(app);
            Ok(())
        })
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::CloseRequested { .. }) {
                stop_worker(window);
            }
        })
        .build(tauri::generate_context!())
        .expect("failed to build Lattice AI desktop shell")
        .run(|app_handle, event| {
            if matches!(
                event,
                tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit
            ) {
                stop_worker(app_handle);
            }
        });
}
