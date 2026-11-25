use std::fmt;
use std::net::{Ipv4Addr, SocketAddrV4, TcpListener};

use super::command::HostProbe;

pub const DEFAULT_PORT: u16 = 4825;

pub const DEFAULT_SCAN_ATTEMPTS: u16 = 64;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PortError {
    pub start: u16,
    pub attempts: u16,
}

impl fmt::Display for PortError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "no free loopback port in {}..{}",
            self.start,
            self.start.saturating_add(self.attempts)
        )
    }
}

impl std::error::Error for PortError {}

pub fn is_port_free(port: u16) -> bool {
    TcpListener::bind(SocketAddrV4::new(Ipv4Addr::LOCALHOST, port)).is_ok()
}

pub fn preferred_port(probe: &dyn HostProbe, keys: &[&str]) -> u16 {
    for key in keys {
        if let Some(raw) = probe.env_var(key) {
            if let Ok(port) = raw.trim().parse::<u16>() {
                if port != 0 {
                    return port;
                }
            }
        }
    }
    DEFAULT_PORT
}

pub fn find_free_port(start: u16, attempts: u16, exclude: &[u16]) -> Result<u16, PortError> {
    for offset in 0..attempts.max(1) {
        let Some(port) = start.checked_add(offset) else {
            break;
        };
        if port == 0 || exclude.contains(&port) {
            continue;
        }
        if is_port_free(port) {
            return Ok(port);
        }
    }
    Err(PortError { start, attempts })
}

#[cfg(test)]
mod tests {
    use super::super::command::StaticProbe;
    use super::*;

    #[test]
    fn preferred_port_defaults_to_the_unified_port() {
        assert_eq!(
            preferred_port(&StaticProbe::new(), &["LATTICEAI_PORT"]),
            4825
        );
    }

    #[test]
    fn preferred_port_reads_the_environment_and_ignores_junk() {
        let probe = StaticProbe::new().with_env("LATTICEAI_PORT", " 4899 ");
        assert_eq!(preferred_port(&probe, &["LATTICEAI_PORT"]), 4899);
        let junk = StaticProbe::new().with_env("LATTICEAI_PORT", "not-a-port");
        assert_eq!(preferred_port(&junk, &["LATTICEAI_PORT"]), DEFAULT_PORT);
        let zero = StaticProbe::new().with_env("LATTICEAI_PORT", "0");
        assert_eq!(preferred_port(&zero, &["LATTICEAI_PORT"]), DEFAULT_PORT);
    }

    #[test]
    fn preferred_port_honours_key_priority() {
        let probe = StaticProbe::new()
            .with_env("LATTICEAI_HOST_PORT", "5000")
            .with_env("LATTICEAI_PORT", "6000");
        assert_eq!(
            preferred_port(&probe, &["LATTICEAI_HOST_PORT", "LATTICEAI_PORT"]),
            5000
        );
        assert_eq!(
            preferred_port(&probe, &["LATTICEAI_MISSING", "LATTICEAI_PORT"]),
            6000
        );
    }

    #[test]
    fn scan_skips_a_busy_port_and_returns_the_next_free_one() {
        let held = TcpListener::bind(SocketAddrV4::new(Ipv4Addr::LOCALHOST, 0)).expect("bind");
        let busy = held.local_addr().expect("addr").port();
        assert!(!is_port_free(busy), "the held port is genuinely busy");
        let chosen = find_free_port(busy, 16, &[]).expect("free port");
        assert!(chosen > busy, "expected an upward scan, got {chosen}");
        drop(held);
    }

    #[test]
    fn scan_honours_the_exclusion_list() {
        let free = find_free_port(41000, 64, &[]).expect("free port");
        let next = find_free_port(free, 64, &[free]).expect("free port");
        assert_ne!(next, free);
    }

    #[test]
    fn scan_reports_a_useful_error_when_it_runs_out() {
        let held = TcpListener::bind(SocketAddrV4::new(Ipv4Addr::LOCALHOST, 0)).expect("bind");
        let busy = held.local_addr().expect("addr").port();
        let err = find_free_port(busy, 1, &[]).expect_err("no room");
        assert_eq!(err.start, busy);
        assert!(err.to_string().contains("no free loopback port"));
        drop(held);
    }

    #[test]
    fn zero_attempts_probes_the_start_port_and_nothing_beyond() {
        let held = TcpListener::bind(SocketAddrV4::new(Ipv4Addr::LOCALHOST, 0)).expect("bind");
        let busy = held.local_addr().expect("addr").port();
        let err = find_free_port(busy, 0, &[]).expect_err("busy start, single probe");
        assert_eq!(err.start, busy);
        drop(held);
    }
}
