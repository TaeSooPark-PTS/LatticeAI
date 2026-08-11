//! The native folder picker behind the `select_folder` command.
//!
//! Unchanged from the pre-11.4.0 shell: macOS gets an AppleScript `choose
//! folder` dialog, every other platform answers `None` and the frontend falls
//! back to its Electron bridge or to typing a path.

/// Ask the user for a directory. `None` when they cancel, or when the platform
/// has no picker here.
#[cfg(target_os = "macos")]
pub fn select_folder() -> Option<String> {
    let output = std::process::Command::new("osascript")
        .args([
            "-e",
            r#"POSIX path of (choose folder with prompt "Choose a folder for Lattice AI")"#,
        ])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let path = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if path.is_empty() {
        None
    } else {
        Some(path)
    }
}

/// No native picker outside macOS; the caller falls back.
#[cfg(not(target_os = "macos"))]
pub fn select_folder() -> Option<String> {
    None
}
