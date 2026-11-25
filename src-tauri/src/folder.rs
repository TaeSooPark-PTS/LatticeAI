#[cfg(target_os = "macos")]
pub async fn select_folder() -> Result<Option<String>, String> {
    tokio::task::spawn_blocking(|| {
        let output = std::process::Command::new("/usr/bin/osascript")
            .args([
                "-e",
                r#"POSIX path of (choose folder with prompt "Choose a folder for Lattice AI")"#,
            ])
            .output()
            .map_err(|err| format!("failed to launch /usr/bin/osascript: {err}"))?;
        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            if stderr.contains("-128") || stderr.to_ascii_lowercase().contains("user canceled") {
                return Ok(None);
            }
            return Err(format!(
                "folder picker failed (status {}): {}",
                output.status,
                stderr.trim()
            ));
        }
        let path = String::from_utf8_lossy(&output.stdout).trim().to_string();
        if path.is_empty() {
            Ok(None)
        } else {
            Ok(Some(path))
        }
    })
    .await
    .map_err(|err| format!("folder picker task failed: {err}"))?
}

#[cfg(not(target_os = "macos"))]
pub async fn select_folder() -> Result<Option<String>, String> {
    Ok(None)
}
