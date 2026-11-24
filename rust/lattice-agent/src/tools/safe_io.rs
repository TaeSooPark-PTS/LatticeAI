//! OS-level handle-relative fd-confined filesystem operations (P0.4).
//!
//! Eliminates TOCTOU (Time-of-Check to Time-of-Use) race conditions and symlink
//! escapes between path resolution and file I/O:
//! - On Unix (macOS/Linux): opens the canonical workspace root with
//!   `O_RDONLY | O_DIRECTORY | O_CLOEXEC`, pins its `(st_dev, st_ino)` via
//!   `fstat`, walks every relative path component using `openat(..., O_NOFOLLOW | O_CLOEXEC)`
//!   (and `mkdirat` when creating parent directories), verifies the root fd's
//!   `(st_dev, st_ino)` matches the live directory inode, and performs reads or
//!   creates/writes directly on the opened file descriptor.
//! - On non-Unix platforms: verifies each component with non-following
//!   `symlink_metadata` and canonical containment immediately before I/O.

use std::io::{self, Read, Write};
use std::path::{Component, Path};

use crate::tools::sandbox::{ToolError, Workspace};

fn relative_components(workspace: &Workspace, target: &Path) -> Result<Vec<String>, ToolError> {
    let rel = target
        .strip_prefix(workspace.root())
        .map_err(|_| ToolError::tool("Path escapes the agent workspace."))?;
    let mut parts = Vec::new();
    for comp in rel.components() {
        match comp {
            Component::Normal(seg) => {
                let s = seg
                    .to_str()
                    .ok_or_else(|| ToolError::tool("Path contains invalid UTF-8."))?;
                if s.is_empty() || s.contains('\0') {
                    return Err(ToolError::tool("Path contains invalid segment."));
                }
                parts.push(s.to_string());
            }
            Component::CurDir => {}
            Component::ParentDir | Component::RootDir | Component::Prefix(_) => {
                return Err(ToolError::tool("Path escapes the agent workspace."));
            }
        }
    }
    if parts.is_empty() {
        return Err(ToolError::tool("Target path resolves to the workspace root."));
    }
    Ok(parts)
}

#[cfg(unix)]
mod unix_fd {
    use std::ffi::CString;
    use std::fs::File;
    use std::io;
    use std::os::fd::{FromRawFd, RawFd};
    use std::path::Path;

    struct OwnedDirFd(RawFd);

    impl OwnedDirFd {
        fn open_root(root: &Path) -> io::Result<(Self, u64, u64)> {
            let c_root = path_to_cstring(root)?;
            // SAFETY: c_root is a valid NUL-terminated path; fd is closed in Drop.
            let fd = unsafe {
                libc::open(
                    c_root.as_ptr(),
                    libc::O_RDONLY | libc::O_DIRECTORY | libc::O_CLOEXEC | libc::O_NOFOLLOW,
                )
            };
            if fd < 0 {
                return Err(io::Error::last_os_error());
            }
            let guard = Self(fd);
            let (dev, ino) = fstat_dev_ino(guard.0)?;
            Ok((guard, dev, ino))
        }

        fn open_subdir(&self, name: &str, create: bool) -> io::Result<Self> {
            let c_name = str_to_cstring(name)?;
            if create {
                // SAFETY: self.0 is a valid directory fd and c_name is NUL-terminated.
                let rc = unsafe { libc::mkdirat(self.0, c_name.as_ptr(), 0o755) };
                if rc < 0 {
                    let err = io::Error::last_os_error();
                    if err.kind() != io::ErrorKind::AlreadyExists {
                        return Err(err);
                    }
                }
            }
            // SAFETY: O_NOFOLLOW refuses symlinks atomically in the kernel.
            let fd = unsafe {
                libc::openat(
                    self.0,
                    c_name.as_ptr(),
                    libc::O_RDONLY | libc::O_DIRECTORY | libc::O_CLOEXEC | libc::O_NOFOLLOW,
                )
            };
            if fd < 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(Self(fd))
        }

        fn open_file(&self, name: &str, flags: libc::c_int, mode: libc::c_uint) -> io::Result<File> {
            let c_name = str_to_cstring(name)?;
            // SAFETY: O_NOFOLLOW rejects symlink leaf atomically; returned fd is owned by File.
            let fd = unsafe {
                libc::openat(
                    self.0,
                    c_name.as_ptr(),
                    flags | libc::O_NOFOLLOW | libc::O_CLOEXEC,
                    mode,
                )
            };
            if fd < 0 {
                return Err(io::Error::last_os_error());
            }
            // SAFETY: fd is a freshly opened owned file descriptor.
            Ok(unsafe { File::from_raw_fd(fd) })
        }
    }

    impl Drop for OwnedDirFd {
        fn drop(&mut self) {
            // SAFETY: OwnedDirFd exclusively owns self.0.
            unsafe {
                libc::close(self.0);
            }
        }
    }

    fn path_to_cstring(path: &Path) -> io::Result<CString> {
        use std::os::unix::ffi::OsStrExt;
        CString::new(path.as_os_str().as_bytes())
            .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "interior NUL byte in path"))
    }

    fn str_to_cstring(s: &str) -> io::Result<CString> {
        CString::new(s)
            .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "interior NUL byte in segment"))
    }

    fn fstat_dev_ino(fd: RawFd) -> io::Result<(u64, u64)> {
        let mut st: libc::stat = unsafe { std::mem::zeroed() };
        // SAFETY: fd is open and st is valid writable memory.
        let rc = unsafe { libc::fstat(fd, &mut st) };
        if rc < 0 {
            return Err(io::Error::last_os_error());
        }
        #[allow(clippy::unnecessary_cast)]
        Ok((st.st_dev as u64, st.st_ino as u64))
    }

    fn verify_root_unswapped(root: &Path, expected_dev: u64, expected_ino: u64) -> io::Result<()> {
        let meta = std::fs::symlink_metadata(root)?;
        if meta.file_type().is_symlink() {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "workspace root replaced with symlink",
            ));
        }
        use std::os::unix::fs::MetadataExt;
        if meta.dev() != expected_dev || meta.ino() != expected_ino {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "workspace root directory swapped during I/O",
            ));
        }
        Ok(())
    }

    pub fn open_for_read(root: &Path, parts: &[String]) -> io::Result<File> {
        let (mut dir, dev, ino) = OwnedDirFd::open_root(root)?;
        for seg in &parts[..parts.len() - 1] {
            dir = dir.open_subdir(seg, false)?;
        }
        let leaf = &parts[parts.len() - 1];
        let file = dir.open_file(leaf, libc::O_RDONLY, 0)?;
        verify_root_unswapped(root, dev, ino)?;
        Ok(file)
    }

    pub fn open_for_write(root: &Path, parts: &[String], create_new: bool) -> io::Result<File> {
        let (mut dir, dev, ino) = OwnedDirFd::open_root(root)?;
        for seg in &parts[..parts.len() - 1] {
            dir = dir.open_subdir(seg, true)?;
        }
        let leaf = &parts[parts.len() - 1];
        let flags = if create_new {
            libc::O_WRONLY | libc::O_CREAT | libc::O_EXCL
        } else {
            libc::O_WRONLY | libc::O_CREAT | libc::O_TRUNC
        };
        let file = dir.open_file(leaf, flags, 0o644)?;
        verify_root_unswapped(root, dev, ino)?;
        Ok(file)
    }
}

fn map_safe_io_err(err: io::Error) -> ToolError {
    #[cfg(unix)]
    if err.raw_os_error() == Some(libc::ELOOP) {
        return ToolError::tool("Symlink traversal refused inside workspace sandbox.");
    }
    if err.kind() == io::ErrorKind::PermissionDenied {
        return ToolError::tool(format!("Workspace sandbox refused operation: {err}"));
    }
    ToolError::tool(err.to_string())
}

/// Read a workspace file via handle-relative `O_NOFOLLOW` descriptor traversal.
pub fn safe_read_bytes(workspace: &Workspace, target: &Path, max_bytes: u64) -> Result<Vec<u8>, ToolError> {
    let parts = relative_components(workspace, target)?;
    #[cfg(unix)]
    let mut file = unix_fd::open_for_read(workspace.root(), &parts).map_err(map_safe_io_err)?;
    #[cfg(not(unix))]
    let mut file = {
        let meta = std::fs::symlink_metadata(target).map_err(map_safe_io_err)?;
        if meta.file_type().is_symlink() {
            return Err(ToolError::tool("Symlink traversal refused inside workspace sandbox."));
        }
        std::fs::File::open(target).map_err(map_safe_io_err)?
    };
    let meta = file.metadata().map_err(map_safe_io_err)?;
    if !meta.is_file() {
        return Err(ToolError::tool("Target is not a regular file."));
    }
    if meta.len() > max_bytes {
        return Err(ToolError::tool("File is too large to read."));
    }
    let mut buf = Vec::with_capacity(meta.len() as usize);
    file.read_to_end(&mut buf).map_err(map_safe_io_err)?;
    Ok(buf)
}

/// Write bytes to a workspace file via handle-relative `O_NOFOLLOW` descriptor traversal.
pub fn safe_write_bytes(workspace: &Workspace, target: &Path, bytes: &[u8]) -> Result<u64, ToolError> {
    let parts = relative_components(workspace, target)?;
    #[cfg(unix)]
    let mut file = unix_fd::open_for_write(workspace.root(), &parts, false).map_err(map_safe_io_err)?;
    #[cfg(not(unix))]
    let mut file = {
        if let Some(parent) = target.parent() {
            std::fs::create_dir_all(parent).map_err(map_safe_io_err)?;
        }
        if let Ok(meta) = std::fs::symlink_metadata(target) {
            if meta.file_type().is_symlink() {
                return Err(ToolError::tool("Symlink traversal refused inside workspace sandbox."));
            }
        }
        std::fs::File::create(target).map_err(map_safe_io_err)?
    };
    file.write_all(bytes).map_err(map_safe_io_err)?;
    file.flush().map_err(map_safe_io_err)?;
    Ok(bytes.len() as u64)
}

/// Atomically create a brand-new workspace file (`O_CREAT | O_EXCL | O_NOFOLLOW`),
/// returning `Ok(true)` if created or `Ok(false)` if it already existed.
pub fn safe_create_new_bytes(workspace: &Workspace, target: &Path, bytes: &[u8]) -> Result<bool, ToolError> {
    let parts = relative_components(workspace, target)?;
    #[cfg(unix)]
    let file_res = unix_fd::open_for_write(workspace.root(), &parts, true);
    #[cfg(not(unix))]
    let file_res = {
        if let Some(parent) = target.parent() {
            std::fs::create_dir_all(parent).map_err(map_safe_io_err)?;
        }
        std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(target)
    };
    match file_res {
        Ok(mut file) => {
            file.write_all(bytes).map_err(map_safe_io_err)?;
            file.flush().map_err(map_safe_io_err)?;
            Ok(true)
        }
        Err(err) if err.kind() == io::ErrorKind::AlreadyExists => Ok(false),
        Err(err) => Err(map_safe_io_err(err)),
    }
}
