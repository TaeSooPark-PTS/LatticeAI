//! Lattice AI desktop shell.
//!
//! Everything this file used to do itself — resolving the worker command,
//! spawning it, probing a TCP socket to guess whether it was up, killing and
//! respawning on restart — moved into the `lattice-host` crate in 11.4.0 and is
//! now shared with the `lattice-host` binary, so the desktop and the CLI front
//! door cannot drift apart. What is left here is the part that is genuinely
//! Tauri's: five commands, one window, and the promise that closing the window
//! stops the worker.
//!
//! Since 11.5.0 the shell also *serves* that front door: the gateway runs
//! in-process on the public port and the worker sits behind it. 11.6.0 One
//! Door drops the Python-direct escape hatches — see [`topology`].
//!
//! The five command names and their response shapes are unchanged — the
//! frontend (`frontend/src/api/base.ts`, `frontend/src/api/client.ts`) invokes
//! `backend_origin`, `backend_status` and `select_folder` by name and reads
//! `backend_status` as a plain record. The fields added in 11.5.0 are
//! additive; nothing was renamed or removed.

mod backend;
mod folder;
mod topology;

use std::sync::Arc;

use backend::{BackendStatus, DesktopBackend};
use tauri::{Manager, State};

/// `backend_origin` → the origin every frontend request is sent to.
#[tauri::command]
fn backend_origin(state: State<'_, Arc<DesktopBackend>>) -> String {
    state.origin().to_string()
}

/// `backend_status` → a live snapshot, health probe included.
///
/// Takes an owned `AppHandle` rather than a borrowed `State<'_, _>` so this
/// infallible async command returns `BackendStatus` directly without a
/// vacuous `Result<BackendStatus, String>` wrapper.
#[tauri::command]
async fn backend_status(app: tauri::AppHandle) -> BackendStatus {
    let state = Arc::clone(&app.state::<Arc<DesktopBackend>>());
    state.status().await
}

/// `restart_backend` → stop, start, and propagate failures to the caller.
#[tauri::command]
async fn restart_backend(state: State<'_, Arc<DesktopBackend>>) -> Result<BackendStatus, String> {
    state.restart().await
}

/// `shutdown_backend` → stop the worker and suppress restarts (infallible).
#[tauri::command]
async fn shutdown_backend(app: tauri::AppHandle) -> BackendStatus {
    let state = Arc::clone(&app.state::<Arc<DesktopBackend>>());
    state.stop().await
}

/// `select_folder` → the native directory picker (non-blocking, distinguishes cancel vs error).
#[tauri::command]
async fn select_folder() -> Result<Option<String>, String> {
    folder::select_folder().await
}

/// Bring the host up, then point the webview at `{origin}/app`.
///
/// The navigation waits on the **host's** `GET /health` at the same origin the
/// window loads — never a bare Python worker. A bound socket only proves
/// something is listening, and landing on `/app` before the host answers is
/// what used to show a blank window.
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

/// Stop the worker and the front door from a synchronous Tauri callback
/// (window close, app exit).
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
