//! Which front door the desktop opens, and on which ports.
//!
//! v11.6.0 One Door: **lattice-host is the only front door.** The shell either
//! serves the gateway in-process (default) or attaches to an already-running
//! lattice-host named by `LATTICEAI_DESKTOP_BACKEND_ORIGIN`. There is no
//! Python-direct topology — `LATTICEAI_DESKTOP_DIRECT` and
//! `LATTICEAI_DESKTOP_NO_BACKEND` are retired and ignored.
//!
//! | Environment | Topology | Webview origin | Spawns |
//! |---|---|---|---|
//! | *(none)* | `gateway` | `http://127.0.0.1:{gateway}` | worker (internal port) + in-process gateway |
//! | `LATTICEAI_DESKTOP_BACKEND_ORIGIN=…` | `external` | that origin, verbatim (must be a lattice-host) | nothing |
//!
//! An explicit origin outranks the default. `LATTICEAI_PORT`, when set, is
//! honoured verbatim as the **port the person visits** — the gateway port —
//! because the reason to set it is that something else knows the number.

use lattice_host::supervisor::{find_free_port, HostProbe, DEFAULT_PORT, DEFAULT_SCAN_ATTEMPTS};

/// Point the shell at an already-running **lattice-host**. Nothing is spawned,
/// and this exact string is what `backend_origin` returns and the webview
/// navigates to. A bare Python worker origin is not a supported value.
pub const ORIGIN_ENV: &str = "LATTICEAI_DESKTOP_BACKEND_ORIGIN";
/// Retired in 11.6.0: previously a kill switch. Ignored — the shell always
/// boots or attaches through lattice-host.
pub const NO_BACKEND_ENV: &str = "LATTICEAI_DESKTOP_NO_BACKEND";
/// Retired in 11.6.0: previously opted into a Python-direct front door.
/// Ignored — lattice-host is the only front door.
pub const DIRECT_ENV: &str = "LATTICEAI_DESKTOP_DIRECT";
/// An explicit port, honoured verbatim.
pub const PORT_ENV: &str = "LATTICEAI_PORT";

/// How this shell is arranged.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Topology {
    /// Gateway on the public port, worker behind it. The default.
    Gateway,
    /// A lattice-host someone else started (`LATTICEAI_DESKTOP_BACKEND_ORIGIN`).
    External,
}

impl Topology {
    /// The name this topology is reported under, in status and in logs.
    pub fn as_str(self) -> &'static str {
        match self {
            Topology::Gateway => "gateway",
            Topology::External => "external",
        }
    }

    /// Whether this shell owns a worker process.
    pub fn supervises(self) -> bool {
        matches!(self, Topology::Gateway)
    }
}

/// The resolved arrangement: what to serve, what to spawn, where to navigate.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Plan {
    /// Which arrangement was chosen.
    pub topology: Topology,
    /// Where the frontend and the webview go (always a lattice-host origin).
    pub origin: String,
    /// Where the worker itself answers — the proxy target. Equal to `origin`
    /// when we only attach to someone else's host (we do not speak to their
    /// worker).
    pub worker_origin: String,
    /// The worker's port (internal when we supervise; the named host port
    /// when we attach).
    pub worker_port: u16,
    /// The gateway's port, when this process serves one.
    pub gateway_port: Option<u16>,
}

impl Plan {
    /// Whether this shell starts and owns the worker.
    pub fn supervised(&self) -> bool {
        self.topology.supervises()
    }

    /// `{origin}/app` — the URL the window navigates to once health answers.
    pub fn app_url(&self) -> String {
        format!("{}/app", self.origin.trim_end_matches('/'))
    }
}

/// Parse a flag the way the product parses its booleans: set-and-truthy is on,
/// set-and-falsy is off, absent is off.
///
/// Presence alone is deliberately *not* enough — `LATTICEAI_DESKTOP_DIRECT=0`
/// must mean "no", or the variable becomes impossible to turn off in a shell
/// profile that already exports it. The flag itself is retired; the parser
/// stays so a leftover `=0` is not mistaken for "on" if anything still reads it.
pub fn flag_is_on(raw: Option<&str>) -> bool {
    matches!(
        raw.map(|value| value.trim().to_ascii_lowercase())
            .as_deref(),
        Some("1" | "true" | "yes" | "on")
    )
}

/// The port named by an origin, when it names one.
pub fn origin_port(origin: &str) -> Option<u16> {
    let authority = origin
        .split_once("://")
        .map(|(_, rest)| rest)
        .unwrap_or(origin)
        .split('/')
        .next()
        .unwrap_or_default();
    authority
        .rsplit_once(':')
        .and_then(|(_, port)| port.trim().parse::<u16>().ok())
        .filter(|port| *port != 0)
}

/// The public port: an explicit `LATTICEAI_PORT` verbatim, else 4825 scanning
/// upward.
///
/// An explicit port is an instruction, not a preference — if it is busy the
/// shell must fail loudly on it rather than quietly answer somewhere else,
/// because the whole point of setting it is that something else knows the
/// number.
pub fn resolve_port(probe: &dyn HostProbe) -> u16 {
    let pinned = probe
        .env_var(PORT_ENV)
        .and_then(|raw| raw.trim().parse::<u16>().ok())
        .filter(|port| *port != 0);
    match pinned {
        Some(port) => port,
        None => find_free_port(DEFAULT_PORT, DEFAULT_SCAN_ATTEMPTS, &[]).unwrap_or(DEFAULT_PORT),
    }
}

/// `http://127.0.0.1:{port}`.
fn loopback(port: u16) -> String {
    format!("http://127.0.0.1:{port}")
}

/// Resolve the arrangement from the environment the probe reports.
pub fn resolve(probe: &dyn HostProbe) -> Plan {
    let external = probe
        .env_var(ORIGIN_ENV)
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty());
    if let Some(origin) = external {
        let host_port = origin_port(&origin).unwrap_or_else(|| resolve_port(probe));
        return Plan {
            topology: Topology::External,
            worker_origin: origin.clone(),
            origin,
            worker_port: host_port,
            gateway_port: None,
        };
    }

    // Retired escape hatches: log once so a leftover profile is visible, then
    // take the only remaining door.
    if flag_is_on(probe.env_var(DIRECT_ENV).as_deref()) {
        eprintln!(
            "lattice-ai-desktop: {DIRECT_ENV} is retired in 11.6.0; \
             the shell always fronts lattice-host"
        );
    }
    if probe.env_var(NO_BACKEND_ENV).is_some() {
        eprintln!(
            "lattice-ai-desktop: {NO_BACKEND_ENV} is retired in 11.6.0; \
             the shell always boots through lattice-host"
        );
    }

    // The default: the gateway takes the public port and the worker moves
    // behind it, on the first free port above — never the gateway's own.
    let gateway_port = resolve_port(probe);
    let worker_port = find_free_port(
        gateway_port.checked_add(1).unwrap_or(DEFAULT_PORT),
        DEFAULT_SCAN_ATTEMPTS,
        &[gateway_port],
    )
    .unwrap_or(gateway_port.wrapping_add(1));
    Plan {
        topology: Topology::Gateway,
        origin: loopback(gateway_port),
        worker_origin: loopback(worker_port),
        worker_port,
        gateway_port: Some(gateway_port),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use lattice_host::supervisor::StaticProbe;

    #[test]
    fn an_origin_names_its_port_or_admits_it_does_not() {
        assert_eq!(origin_port("http://127.0.0.1:8765"), Some(8765));
        assert_eq!(origin_port("http://127.0.0.1:8765/app"), Some(8765));
        assert_eq!(origin_port("https://[::1]:4825"), Some(4825));
        assert_eq!(origin_port("127.0.0.1:4899"), Some(4899));
        assert_eq!(origin_port("http://localhost"), None);
        assert_eq!(origin_port("http://[::1]"), None);
        assert_eq!(origin_port("http://127.0.0.1:0"), None);
        assert_eq!(origin_port(""), None);
    }

    #[test]
    fn an_explicit_port_is_honoured_verbatim() {
        let probe = StaticProbe::new().with_env(PORT_ENV, " 8765 ");
        assert_eq!(resolve_port(&probe), 8765);
    }

    #[test]
    fn without_a_pinned_port_the_scan_starts_at_the_unified_default() {
        let port = resolve_port(&StaticProbe::new());
        assert!(
            (DEFAULT_PORT..DEFAULT_PORT + DEFAULT_SCAN_ATTEMPTS).contains(&port),
            "expected a port scanned upward from {DEFAULT_PORT}, got {port}"
        );
    }

    #[test]
    fn a_flag_is_on_only_when_it_says_so() {
        for on in ["1", "true", "TRUE", "yes", "on", " on "] {
            assert!(flag_is_on(Some(on)), "{on}");
        }
        for off in ["0", "false", "no", "off", "", "perhaps"] {
            assert!(!flag_is_on(Some(off)), "{off}");
        }
        assert!(!flag_is_on(None));
    }

    #[test]
    fn the_default_topology_puts_the_gateway_in_front() {
        let plan = resolve(&StaticProbe::new().with_env(PORT_ENV, "41830"));
        assert_eq!(plan.topology, Topology::Gateway);
        assert_eq!(plan.gateway_port, Some(41830));
        assert_eq!(plan.origin, "http://127.0.0.1:41830");
        assert!(
            plan.worker_port > 41830,
            "the worker moves behind the front door, not onto it"
        );
        assert_eq!(
            plan.worker_origin,
            format!("http://127.0.0.1:{}", plan.worker_port)
        );
        assert!(plan.supervised());
        assert_eq!(plan.app_url(), "http://127.0.0.1:41830/app");
    }

    #[test]
    fn a_retired_direct_flag_still_uses_the_gateway() {
        let probe = StaticProbe::new()
            .with_env(DIRECT_ENV, "1")
            .with_env(PORT_ENV, "41831");
        let plan = resolve(&probe);
        assert_eq!(plan.topology, Topology::Gateway);
        assert_eq!(plan.gateway_port, Some(41831));
        assert_ne!(plan.worker_origin, plan.origin);
        assert!(plan.supervised());
    }

    #[test]
    fn a_falsy_direct_flag_leaves_the_default_alone() {
        let probe = StaticProbe::new()
            .with_env(DIRECT_ENV, "0")
            .with_env(PORT_ENV, "41832");
        assert_eq!(resolve(&probe).topology, Topology::Gateway);
    }

    #[test]
    fn an_external_origin_spawns_nothing_and_is_used_verbatim() {
        let probe = StaticProbe::new().with_env(ORIGIN_ENV, " http://127.0.0.1:9100/ ");
        let plan = resolve(&probe);
        assert_eq!(plan.topology, Topology::External);
        assert_eq!(plan.origin, "http://127.0.0.1:9100/");
        assert_eq!(plan.app_url(), "http://127.0.0.1:9100/app");
        assert_eq!(plan.worker_port, 9100);
        assert_eq!(plan.worker_origin, plan.origin);
        assert!(
            plan.gateway_port.is_none(),
            "we must not bind a port someone else's host already holds"
        );
        assert!(!plan.supervised());
    }

    #[test]
    fn an_external_origin_without_a_port_still_resolves_one() {
        let probe = StaticProbe::new().with_env(ORIGIN_ENV, "http://localhost");
        let plan = resolve(&probe);
        assert_eq!(plan.origin, "http://localhost");
        assert!(plan.worker_port >= DEFAULT_PORT);
    }

    #[test]
    fn a_retired_kill_switch_still_uses_the_gateway() {
        let probe = StaticProbe::new()
            .with_env(NO_BACKEND_ENV, "1")
            .with_env(PORT_ENV, "41833");
        let plan = resolve(&probe);
        assert_eq!(plan.topology, Topology::Gateway);
        assert!(plan.supervised());
        assert_eq!(plan.gateway_port, Some(41833));
    }

    #[test]
    fn an_explicit_origin_outranks_every_other_switch() {
        let probe = StaticProbe::new()
            .with_env(NO_BACKEND_ENV, "1")
            .with_env(DIRECT_ENV, "1")
            .with_env(ORIGIN_ENV, "http://127.0.0.1:9100");
        let plan = resolve(&probe);
        assert_eq!(plan.topology, Topology::External);
        assert!(!plan.supervised());
    }

    #[test]
    fn every_topology_reports_a_name() {
        for (topology, name) in [
            (Topology::Gateway, "gateway"),
            (Topology::External, "external"),
        ] {
            assert_eq!(topology.as_str(), name);
        }
    }
}
