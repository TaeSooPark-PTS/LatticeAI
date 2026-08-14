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
/// Async, and therefore `Result`, because Tauri requires it of a command that
/// borrows state across an await. It never returns `Err`: the frontend keeps
/// receiving a resolved record exactly as before.
#[tauri::command]
async fn backend_status(state: State<'_, Arc<DesktopBackend>>) -> Result<BackendStatus, String> {
    Ok(state.status().await)
}

/// `restart_backend` → stop, start, and report what happened.
#[tauri::command]
async fn restart_backend(state: State<'_, Arc<DesktopBackend>>) -> Result<BackendStatus, String> {
    Ok(state.restart().await)
}

/// `shutdown_backend` → stop the worker and suppress restarts.
#[tauri::command]
async fn shutdown_backend(state: State<'_, Arc<DesktopBackend>>) -> Result<BackendStatus, String> {
    Ok(state.stop().await)
}

/// `select_folder` → the native directory picker.
#[tauri::command]
fn select_folder() -> Option<String> {
    folder::select_folder()
}

/// Bring the host up, then point the webview at `{origin}/app`.
///
/// The navigation waits on the **host's** `GET /health` at the same origin the
/// window loads — never a bare Python worker. A bound socket only proves
/// something is listening, and landing on `/app` before the host answers is
/// what used to show a blank window.
fn boot(app: &tauri::App) {
    let Some(window) = app.get_webview_window("main") else {
        return;
    };
    let _ = window.set_title("Lattice AI");
    let _ = window.show();
    let _ = window.set_focus();

    let shell = Arc::clone(&app.state::<Arc<DesktopBackend>>());
    tauri::async_runtime::spawn(async move {
        shell.start().await;
        // One reason not to navigate, and it is not "the worker is slow": if
        // this shell was supposed to serve the front door and could not bind
        // it, `app_url` names a port nothing will ever listen on, and sending
        // the window there replaces a readable failure with a blank "cannot
        // connect". The bundled shell stays up and reads `gateway_error` out of
        // `desktopBackendStatus` instead.
        if let Some(reason) = shell.gateway_error() {
            eprintln!("lattice-ai-desktop: staying on the bundled shell — {reason}");
            return;
        }
        // Host /health at the origin the window is about to load. An attached
        // host (LATTICEAI_DESKTOP_BACKEND_ORIGIN) never ran start_gateway, so
        // this is also the first time we wait for it.
        shell.wait_until_serving().await;
        // Navigate either way: a window showing the browser's own "cannot
        // connect" is more use than a window showing nothing while a host that
        // will never arrive is waited for.
        if let Ok(url) = tauri::Url::parse(&shell.app_url()) {
            let _ = window.navigate(url);
        }
    });
}

/// Stop the worker and the front door from a synchronous Tauri callback
/// (window close, app exit).
///
/// `block_on` is safe here because these callbacks run on the event loop
/// thread, outside any async runtime — and the wait is what makes the shutdown
/// graceful: SIGTERM, then SIGKILL only if the worker ignores it, so the
/// worker gets to close its SQLite handles instead of being shot mid-write.
fn stop_worker<R: tauri::Runtime, M: Manager<R>>(manager: &M) {
    if let Some(shell) = manager.try_state::<Arc<DesktopBackend>>() {
        let shell = Arc::clone(&shell);
        let stopping = Arc::clone(&shell);
        tauri::async_runtime::block_on(async move {
            stopping.stop().await;
        });
        // The gateway outlives `shutdown_backend` on purpose (so the window can
        // still read `/host/status`), but not the process.
        shell.stop_gateway();
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
