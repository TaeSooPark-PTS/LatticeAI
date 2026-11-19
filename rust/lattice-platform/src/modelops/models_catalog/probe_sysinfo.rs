//! Host hardware, OS, disk, network, and binary probes for [`super`].

use std::path::Path;
use std::time::Duration;

pub fn read_ram_bytes() -> Option<u64> {
    #[cfg(target_os = "macos")]
    {
        parse_u64_stdout(sysctl_n(&["hw.memsize"])?)
    }
    #[cfg(target_os = "linux")]
    {
        parse_meminfo_total(&std::fs::read_to_string("/proc/meminfo").ok()?)
    }
    #[cfg(not(any(target_os = "macos", target_os = "linux")))]
    {
        None
    }
}

pub fn parse_meminfo_total(text: &str) -> Option<u64> {
    for line in text.lines() {
        let Some(rest) = line.strip_prefix("MemTotal:") else {
            continue;
        };
        let kb: u64 = rest.split_whitespace().next()?.parse().ok()?;
        return Some(kb.saturating_mul(1024));
    }
    None
}

pub(super) fn detect_apple_silicon() -> bool {
    #[cfg(target_os = "macos")]
    {
        if let Some(value) = sysctl_n(&["hw.optional.arm64"]) {
            if value.trim() == "1" {
                return true;
            }
        }
        cfg!(target_arch = "aarch64")
    }
    #[cfg(not(target_os = "macos"))]
    {
        false
    }
}

pub(super) fn os_version() -> String {
    #[cfg(target_os = "macos")]
    {
        sysctl_n(&["kern.osproductversion"]).unwrap_or_else(|| std::env::consts::FAMILY.to_string())
    }
    #[cfg(target_os = "linux")]
    {
        std::fs::read_to_string("/etc/os-release")
            .ok()
            .and_then(|text| {
                text.lines().find_map(|line| {
                    line.strip_prefix("PRETTY_NAME=")
                        .map(|value| value.trim_matches('"').to_string())
                })
            })
            .unwrap_or_else(|| std::env::consts::FAMILY.to_string())
    }
    #[cfg(not(any(target_os = "macos", target_os = "linux")))]
    {
        std::env::consts::FAMILY.to_string()
    }
}

pub(super) fn cpu_model() -> String {
    #[cfg(target_os = "macos")]
    {
        sysctl_n(&["machdep.cpu.brand_string"])
            .unwrap_or_else(|| std::env::consts::ARCH.to_string())
    }
    #[cfg(target_os = "linux")]
    {
        std::fs::read_to_string("/proc/cpuinfo")
            .ok()
            .and_then(|text| {
                text.lines().find_map(|line| {
                    line.strip_prefix("model name")
                        .and_then(|rest| rest.split(':').nth(1))
                        .map(|value| value.trim().to_string())
                })
            })
            .unwrap_or_else(|| std::env::consts::ARCH.to_string())
    }
    #[cfg(not(any(target_os = "macos", target_os = "linux")))]
    {
        std::env::consts::ARCH.to_string()
    }
}

pub(super) fn disk_free_bytes(path: &Path) -> Option<u64> {
    #[cfg(unix)]
    {
        let cstr = std::ffi::CString::new(path.to_string_lossy().as_bytes()).ok()?;
        let mut buf = std::mem::MaybeUninit::<libc::statvfs>::uninit();
        let rc = unsafe { libc::statvfs(cstr.as_ptr(), buf.as_mut_ptr()) };
        if rc != 0 {
            return None;
        }
        let buf = unsafe { buf.assume_init() };
        // f_bavail is u64 on Linux but a narrower type on macOS — the cast is
        // required on one platform and a same-type no-op on the other.
        #[allow(clippy::unnecessary_cast)]
        Some((buf.f_bavail as u64).saturating_mul(buf.f_frsize))
    }
    #[cfg(not(unix))]
    {
        let _ = path;
        None
    }
}

pub(super) fn detect_package_manager() -> String {
    for (bin, name) in [
        ("brew", "brew"),
        ("apt-get", "apt"),
        ("dnf", "dnf"),
        ("pacman", "pacman"),
    ] {
        if command_exists(bin) {
            return name.to_string();
        }
    }
    "none".into()
}

pub fn command_exists(bin: &str) -> bool {
    std::process::Command::new(bin)
        .arg("--version")
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .map(|status| status.success())
        .unwrap_or(false)
}

pub(super) fn detect_internet() -> bool {
    std::net::TcpStream::connect_timeout(
        &std::net::SocketAddr::from(([1, 1, 1, 1], 443)),
        Duration::from_millis(400),
    )
    .is_ok()
}

#[cfg(target_os = "macos")]
fn sysctl_n(args: &[&str]) -> Option<String> {
    let output = std::process::Command::new("sysctl")
        .arg("-n")
        .args(args)
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if text.is_empty() {
        None
    } else {
        Some(text)
    }
}

#[cfg(target_os = "macos")]
fn parse_u64_stdout(text: String) -> Option<u64> {
    text.trim().parse().ok()
}

// ── POST /setup/set-api-key ──────────────────────────────────────────────────
