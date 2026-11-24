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
        return Err(ToolError::tool(
            "Target path resolves to the workspace root.",
        ));
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

        fn open_file(
            &self,
            name: &str,
            flags: libc::c_int,
            mode: libc::c_uint,
        ) -> io::Result<File> {
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
        CString::new(s).map_err(|_| {
            io::Error::new(io::ErrorKind::InvalidInput, "interior NUL byte in segment")
        })
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

#[cfg(windows)]
mod windows_handle {
    use std::ffi::OsStr;
    use std::fs::File;
    use std::io;
    use std::os::windows::ffi::OsStrExt;
    use std::os::windows::io::{FromRawHandle, RawHandle};
    use std::path::{Path, PathBuf};

    const INVALID_HANDLE_VALUE: isize = -1;
    const GENERIC_READ: u32 = 0x8000_0000;
    const GENERIC_WRITE: u32 = 0x4000_0000;
    const FILE_SHARE_READ: u32 = 0x0000_0001;
    const FILE_SHARE_WRITE: u32 = 0x0000_0002;
    const FILE_SHARE_DELETE: u32 = 0x0000_0004;
    const CREATE_NEW: u32 = 1;
    const CREATE_ALWAYS: u32 = 2;
    const OPEN_EXISTING: u32 = 3;
    const FILE_ATTRIBUTE_DIRECTORY: u32 = 0x0000_0010;
    const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x0000_0400;
    const FILE_ATTRIBUTE_NORMAL: u32 = 0x0000_0080;
    const FILE_FLAG_BACKUP_SEMANTICS: u32 = 0x0200_0000;
    const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
    const VOLUME_NAME_DOS: u32 = 0x0;
    const ERROR_ALREADY_EXISTS: i32 = 183;

    #[repr(C)]
    #[derive(Default)]
    struct FileTime {
        low: u32,
        high: u32,
    }

    #[repr(C)]
    #[derive(Default)]
    struct ByHandleFileInformation {
        file_attributes: u32,
        creation_time: FileTime,
        last_access_time: FileTime,
        last_write_time: FileTime,
        volume_serial_number: u32,
        file_size_high: u32,
        file_size_low: u32,
        number_of_links: u32,
        file_index_high: u32,
        file_index_low: u32,
    }

    #[link(name = "kernel32")]
    extern "system" {
        fn CreateFileW(
            lp_file_name: *const u16,
            dw_desired_access: u32,
            dw_share_mode: u32,
            lp_security_attributes: *const std::ffi::c_void,
            dw_creation_disposition: u32,
            dw_flags_and_attributes: u32,
            h_template_file: isize,
        ) -> isize;
        fn CreateDirectoryW(
            lp_path_name: *const u16,
            lp_security_attributes: *const std::ffi::c_void,
        ) -> i32;
        fn GetFileInformationByHandle(
            h_file: isize,
            lp_file_information: *mut ByHandleFileInformation,
        ) -> i32;
        fn GetFinalPathNameByHandleW(
            h_file: isize,
            lpsz_file_path: *mut u16,
            cch_file_path: u32,
            dw_flags: u32,
        ) -> u32;
        fn CloseHandle(h_object: isize) -> i32;
    }

    struct OwnedWinHandle(isize);

    impl OwnedWinHandle {
        fn raw(&self) -> isize {
            self.0
        }

        fn into_file(self) -> File {
            let raw = self.0;
            std::mem::forget(self);
            // SAFETY: `raw` is a valid, uniquely owned Win32 file handle.
            unsafe { File::from_raw_handle(raw as RawHandle) }
        }
    }

    impl Drop for OwnedWinHandle {
        fn drop(&mut self) {
            if self.0 != INVALID_HANDLE_VALUE && self.0 != 0 {
                // SAFETY: `self.0` is an open Win32 handle owned exclusively by this guard.
                unsafe {
                    let _ = CloseHandle(self.0);
                }
            }
        }
    }

    fn to_wide_null(path: &Path) -> io::Result<Vec<u16>> {
        let os: &OsStr = path.as_os_str();
        let mut wide: Vec<u16> = os.encode_wide().collect();
        if wide.contains(&0) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "Path contains interior NUL byte.",
            ));
        }
        wide.push(0);
        Ok(wide)
    }

    fn file_info(handle: isize) -> io::Result<ByHandleFileInformation> {
        let mut info = ByHandleFileInformation::default();
        // SAFETY: `handle` is open and `info` is a valid writable output struct.
        let ok = unsafe { GetFileInformationByHandle(handle, &mut info) };
        if ok == 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(info)
    }

    fn final_path(handle: isize) -> io::Result<String> {
        // SAFETY: Query required length first.
        let needed =
            unsafe { GetFinalPathNameByHandleW(handle, std::ptr::null_mut(), 0, VOLUME_NAME_DOS) };
        if needed == 0 {
            return Err(io::Error::last_os_error());
        }
        let mut buf = vec![0u16; needed as usize];
        // SAFETY: `buf` has capacity `needed`.
        let written =
            unsafe { GetFinalPathNameByHandleW(handle, buf.as_mut_ptr(), needed, VOLUME_NAME_DOS) };
        if written == 0 || written >= needed {
            return Err(io::Error::last_os_error());
        }
        buf.truncate(written as usize);
        Ok(String::from_utf16_lossy(&buf))
    }

    fn normalize_win_path(p: &str) -> String {
        let trimmed = p.trim_end_matches('\\');
        trimmed.to_ascii_lowercase()
    }

    fn is_within_root(root_final: &str, candidate_final: &str) -> bool {
        let r = normalize_win_path(root_final);
        let c = normalize_win_path(candidate_final);
        if c == r {
            return true;
        }
        c.starts_with(&r) && c.as_bytes().get(r.len()) == Some(&b'\\')
    }

    fn open_dir_handle_no_reparse(
        path: &Path,
    ) -> io::Result<(OwnedWinHandle, ByHandleFileInformation, String)> {
        let wide = to_wide_null(path)?;
        let share = FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE;
        let flags = FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT;
        // SAFETY: `wide` is NUL-terminated UTF-16.
        let raw = unsafe {
            CreateFileW(
                wide.as_ptr(),
                GENERIC_READ,
                share,
                std::ptr::null(),
                OPEN_EXISTING,
                flags,
                0,
            )
        };
        if raw == INVALID_HANDLE_VALUE {
            return Err(io::Error::last_os_error());
        }
        let handle = OwnedWinHandle(raw);
        let info = file_info(handle.raw())?;
        if (info.file_attributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0 {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "Symlink or junction traversal refused inside workspace sandbox.",
            ));
        }
        if (info.file_attributes & FILE_ATTRIBUTE_DIRECTORY) == 0 {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "Parent component is not a directory.",
            ));
        }
        let resolved = final_path(handle.raw())?;
        Ok((handle, info, resolved))
    }

    fn verify_root_unswapped(root: &Path, vol: u32, idx_hi: u32, idx_lo: u32) -> io::Result<()> {
        let (_h, info, _fp) = open_dir_handle_no_reparse(root)?;
        if info.volume_serial_number != vol
            || info.file_index_high != idx_hi
            || info.file_index_low != idx_lo
        {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "Workspace root changed during handle-pinned I/O (TOCTOU detected).",
            ));
        }
        Ok(())
    }

    fn traverse_parents(
        root: &Path,
        parent_parts: &[String],
        create_missing: bool,
    ) -> io::Result<(Vec<OwnedWinHandle>, PathBuf, String, (u32, u32, u32))> {
        let (root_h, root_info, root_final) = open_dir_handle_no_reparse(root)?;
        let root_id = (
            root_info.volume_serial_number,
            root_info.file_index_high,
            root_info.file_index_low,
        );
        let mut pinned = vec![root_h];
        let mut cur_path = root.to_path_buf();

        for seg in parent_parts {
            if seg.is_empty()
                || seg == "."
                || seg == ".."
                || seg.contains('/')
                || seg.contains('\\')
                || seg.contains('\0')
            {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "Invalid path segment inside workspace.",
                ));
            }
            cur_path.push(seg);
            if create_missing {
                let wide = to_wide_null(&cur_path)?;
                // SAFETY: `wide` is NUL-terminated UTF-16.
                let ok = unsafe { CreateDirectoryW(wide.as_ptr(), std::ptr::null()) };
                if ok == 0 {
                    let err = io::Error::last_os_error();
                    if err.raw_os_error() != Some(ERROR_ALREADY_EXISTS) {
                        return Err(err);
                    }
                }
            }
            let (dir_h, _dir_info, dir_final) = open_dir_handle_no_reparse(&cur_path)?;
            if !is_within_root(&root_final, &dir_final) {
                return Err(io::Error::new(
                    io::ErrorKind::PermissionDenied,
                    "Parent directory escaped workspace root.",
                ));
            }
            pinned.push(dir_h);
        }

        Ok((pinned, cur_path, root_final, root_id))
    }

    pub fn open_for_read(root: &Path, parts: &[String]) -> io::Result<File> {
        let (pinned, mut cur_path, root_final, (vol, idx_hi, idx_lo)) =
            traverse_parents(root, &parts[..parts.len() - 1], false)?;
        let leaf = &parts[parts.len() - 1];
        cur_path.push(leaf);
        let wide = to_wide_null(&cur_path)?;
        let share = FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE;
        let flags = FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT;
        // SAFETY: `wide` is NUL-terminated UTF-16.
        let raw = unsafe {
            CreateFileW(
                wide.as_ptr(),
                GENERIC_READ,
                share,
                std::ptr::null(),
                OPEN_EXISTING,
                flags,
                0,
            )
        };
        if raw == INVALID_HANDLE_VALUE {
            return Err(io::Error::last_os_error());
        }
        let handle = OwnedWinHandle(raw);
        let info = file_info(handle.raw())?;
        if (info.file_attributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0 {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "Symlink traversal refused inside workspace sandbox.",
            ));
        }
        if (info.file_attributes & FILE_ATTRIBUTE_DIRECTORY) != 0 {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "Target is a directory, not a regular file.",
            ));
        }
        let leaf_final = final_path(handle.raw())?;
        if !is_within_root(&root_final, &leaf_final) {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "Opened file handle escaped workspace root.",
            ));
        }
        verify_root_unswapped(root, vol, idx_hi, idx_lo)?;
        drop(pinned);
        Ok(handle.into_file())
    }

    pub fn open_for_write(root: &Path, parts: &[String], create_new: bool) -> io::Result<File> {
        let (pinned, mut cur_path, root_final, (vol, idx_hi, idx_lo)) =
            traverse_parents(root, &parts[..parts.len() - 1], true)?;
        let leaf = &parts[parts.len() - 1];
        cur_path.push(leaf);
        let wide = to_wide_null(&cur_path)?;
        let share = FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE;
        let disposition = if create_new {
            CREATE_NEW
        } else {
            CREATE_ALWAYS
        };
        let flags = FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT;
        // SAFETY: `wide` is NUL-terminated UTF-16.
        let raw = unsafe {
            CreateFileW(
                wide.as_ptr(),
                GENERIC_WRITE,
                share,
                std::ptr::null(),
                disposition,
                flags,
                0,
            )
        };
        if raw == INVALID_HANDLE_VALUE {
            return Err(io::Error::last_os_error());
        }
        let handle = OwnedWinHandle(raw);
        let info = file_info(handle.raw())?;
        if (info.file_attributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0 {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "Symlink traversal refused inside workspace sandbox.",
            ));
        }
        if (info.file_attributes & FILE_ATTRIBUTE_DIRECTORY) != 0 {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "Target is a directory, not a regular file.",
            ));
        }
        let leaf_final = final_path(handle.raw())?;
        if !is_within_root(&root_final, &leaf_final) {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "Opened file handle escaped workspace root.",
            ));
        }
        verify_root_unswapped(root, vol, idx_hi, idx_lo)?;
        drop(pinned);
        Ok(handle.into_file())
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
pub fn safe_read_bytes(
    workspace: &Workspace,
    target: &Path,
    max_bytes: u64,
) -> Result<Vec<u8>, ToolError> {
    let parts = relative_components(workspace, target)?;
    #[cfg(unix)]
    let mut file = unix_fd::open_for_read(workspace.root(), &parts).map_err(map_safe_io_err)?;
    #[cfg(windows)]
    let mut file =
        windows_handle::open_for_read(workspace.root(), &parts).map_err(map_safe_io_err)?;
    #[cfg(not(any(unix, windows)))]
    let mut file: std::fs::File = {
        return Err(ToolError::tool(
            "Handle-pinned workspace I/O is unsupported on this platform.",
        ));
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
pub fn safe_write_bytes(
    workspace: &Workspace,
    target: &Path,
    bytes: &[u8],
) -> Result<u64, ToolError> {
    let parts = relative_components(workspace, target)?;
    #[cfg(unix)]
    let mut file =
        unix_fd::open_for_write(workspace.root(), &parts, false).map_err(map_safe_io_err)?;
    #[cfg(windows)]
    let mut file =
        windows_handle::open_for_write(workspace.root(), &parts, false).map_err(map_safe_io_err)?;
    #[cfg(not(any(unix, windows)))]
    let mut file: std::fs::File = {
        return Err(ToolError::tool(
            "Handle-pinned workspace I/O is unsupported on this platform.",
        ));
    };
    file.write_all(bytes).map_err(map_safe_io_err)?;
    file.flush().map_err(map_safe_io_err)?;
    Ok(bytes.len() as u64)
}

/// Atomically create a brand-new workspace file (`O_CREAT | O_EXCL | O_NOFOLLOW`),
/// returning `Ok(true)` if created or `Ok(false)` if it already existed.
pub fn safe_create_new_bytes(
    workspace: &Workspace,
    target: &Path,
    bytes: &[u8],
) -> Result<bool, ToolError> {
    let parts = relative_components(workspace, target)?;
    #[cfg(unix)]
    let file_res = unix_fd::open_for_write(workspace.root(), &parts, true);
    #[cfg(windows)]
    let file_res = windows_handle::open_for_write(workspace.root(), &parts, true);
    #[cfg(not(any(unix, windows)))]
    let file_res: io::Result<std::fs::File> = Err(io::Error::new(
        io::ErrorKind::Unsupported,
        "Handle-pinned workspace I/O is unsupported on this platform.",
    ));
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
