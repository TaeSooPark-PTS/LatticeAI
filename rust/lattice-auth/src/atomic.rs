//! `core/io_utils.atomic_write_json`, minus the JSON.
//!
//! Write to `<path>.tmp` with mode 0600, then `rename(2)` over the target, then
//! re-assert 0600 on the result. Both files this crate owns hold credentials
//! (password digests, session tokens' hashes), so the mode is not decoration —
//! and a torn write of `users.json` would lock the owner out of their own
//! Brain, which is why the rename is the only thing that ever touches the real
//! name.
//!
//! Every failure is swallowed, as the Python original swallows it: an install
//! whose disk is full must still answer requests rather than crash the process.

use std::io::Write;
use std::path::Path;

/// Atomically replace `path` with `text`.
pub fn write_text(path: &Path, text: &str) {
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let mut temp = path.as_os_str().to_os_string();
    temp.push(".tmp");
    let temp = std::path::PathBuf::from(temp);

    let written = (|| -> std::io::Result<()> {
        let mut file = create_private(&temp)?;
        file.write_all(text.as_bytes())?;
        file.flush()
    })();
    if written.is_err() {
        let _ = std::fs::remove_file(&temp);
        return;
    }
    if std::fs::rename(&temp, path).is_err() {
        let _ = std::fs::remove_file(&temp);
        return;
    }
    set_private(path);
}

#[cfg(unix)]
fn create_private(path: &Path) -> std::io::Result<std::fs::File> {
    use std::os::unix::fs::OpenOptionsExt;
    std::fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .mode(0o600)
        .open(path)
}

#[cfg(not(unix))]
fn create_private(path: &Path) -> std::io::Result<std::fs::File> {
    std::fs::File::create(path)
}

#[cfg(unix)]
fn set_private(path: &Path) {
    use std::os::unix::fs::PermissionsExt;
    let _ = std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600));
}

#[cfg(not(unix))]
fn set_private(_path: &Path) {}

/// `data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)` plus the explicit
/// `chmod` the config phase performs afterwards.
pub fn ensure_private_dir(path: &Path) {
    let _ = std::fs::create_dir_all(path);
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o700));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_replacement_is_atomic_and_private() {
        let dir = tempfile::tempdir().unwrap();
        let target = dir.path().join("nested").join("file.json");
        write_text(&target, "{\"a\":1}");
        assert_eq!(std::fs::read_to_string(&target).unwrap(), "{\"a\":1}");
        assert!(!target.with_extension("json.tmp").exists());
        write_text(&target, "{\"a\":2}");
        assert_eq!(std::fs::read_to_string(&target).unwrap(), "{\"a\":2}");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = std::fs::metadata(&target).unwrap().permissions().mode();
            assert_eq!(mode & 0o777, 0o600);
        }
    }

    #[test]
    fn an_unwritable_target_is_swallowed() {
        // A directory in the target's place makes both the temp create and the
        // rename fail; the call must still return.
        let dir = tempfile::tempdir().unwrap();
        let target = dir.path().join("as-a-dir");
        std::fs::create_dir(&target).unwrap();
        std::fs::create_dir(target.with_extension("tmp")).unwrap();
        write_text(&target, "{}");
        assert!(target.is_dir());
    }

    #[test]
    fn the_data_directory_is_created_private() {
        let dir = tempfile::tempdir().unwrap();
        let nested = dir.path().join("a").join("b");
        ensure_private_dir(&nested);
        assert!(nested.is_dir());
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = std::fs::metadata(&nested).unwrap().permissions().mode();
            assert_eq!(mode & 0o777, 0o700);
        }
    }
}
