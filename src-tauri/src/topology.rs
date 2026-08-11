//! Which front door the desktop opens, and on which ports.
//!
//! Through 11.4.0 the shell had one topology: spawn the Python worker, point
//! the webview at the worker's own port. 11.5.0 makes the Rust gateway the
//! **default front door** (plan §2a) — the shell serves `lattice-host`'s
//! gateway on the public port and supervises the worker on an internal one
//! behind it — because that is the only arrangement in which the native
//! `/rust/*` and `/host/*` surfaces exist for the app at all.
//!
//! Three escape hatches are preserved, and one is new. They are resolved here,
//! in one pure-ish function, so "what will this shell do" is a table rather
//! than a trail through `backend.rs`:
//!
//! | Environment | Topology | Webview origin | Spawns |
//! |---|---|---|---|
//! | *(none)* | `gateway` | `http://127.0.0.1:{gateway}` | worker (internal port) + in-process gateway |
//! | `LATTICEAI_DESKTOP_DIRECT=1` | `direct` | `http://127.0.0.1:{worker}` | worker only (11.4.0 behaviour) |
//! | `LATTICEAI_DESKTOP_BACKEND_ORIGIN=…` | `external` | that origin, verbatim | nothing |
//! | `LATTICEAI_DESKTOP_NO_BACKEND=1` | `disabled` | `http://127.0.0.1:{worker}` | nothing |
//!
//! An explicit origin outranks every other switch (something already exists and
//! was named), and the kill switch outranks the topology choice (there is
//! nothing to front). `LATTICEAI_PORT`, when set, is honoured verbatim as the
//! **port the person visits** — the gateway port in the default topology, the
//! worker port in the others — because the reason to set it is that something
//! else knows the number.

use lattice_host::supervisor::{find_free_port, HostProbe, DEFAULT_PORT, DEFAULT_SCAN_ATTEMPTS};

/// Point the shell at an already-running worker. Nothing is spawned, and this
/// exact string is what `backend_origin` returns and the webview navigates to.
pub const ORIGIN_ENV: &str = "LATTICEAI_DESKTOP_BACKEND_ORIGIN";
/// Kill switch: open the window with no worker at all.
pub const NO_BACKEND_ENV: &str = "LATTICEAI_DESKTOP_NO_BACKEND";
/// Opt back into the 11.4.0 topology: webview straight at the worker.
pub const DIRECT_ENV: &str = "LATTICEAI_DESKTOP_DIRECT";
/// An explicit port, honoured verbatim.
pub const PORT_ENV: &str = "LATTICEAI_PORT";

/// How this shell is arranged.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Topology {
    /// Gateway on the public port, worker behind it. The default.
    Gateway,
    /// Webview straight at the worker, no gateway (`LATTICEAI_DESKTOP_DIRECT`).
    Direct,
    /// A worker someone else started (`LATTICEAI_DESKTOP_BACKEND_ORIGIN`).
    External,
    /// No backend at all (`LATTICEAI_DESKTOP_NO_BACKEND`).
    Disabled,
}

impl Topology {
    /// The name this topology is reported under, in status and in logs.
    pub fn as_str(self) -> &'static str {
        match self {
            Topology::Gateway => "gateway",
            Topology::Direct => "direct",
            Topology::External => "external",
            Topology::Disabled => "disabled",
        }
    }

    /// Whether this shell owns a worker process.
    pub fn supervises(self) -> bool {
        matches!(self, Topology::Gateway | Topology::Direct)
    }
}

/// The resolved arrangement: what to serve, what to spawn, where to navigate.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Plan {
    /// Which arrangement was chosen.
    pub topology: Topology,
    /// Where the frontend and the webview go.
    pub origin: String,
    /// Where the worker itself answers — the proxy target, and the origin the
    /// supervisor health-gates on.
    pub worker_origin: String,
    /// The worker's port.
    pub worker_port: u16,
    /// The gateway's port, when there is a gateway.
    pub gateway_port: Option<u16>,
    /// The kill switch was set: there is deliberately nothing to wait for.
    pub disabled: bool,
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
/// profile that already exports it.
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
        let worker_port = origin_port(&origin).unwrap_or_else(|| resolve_port(probe));
        return Plan {
            topology: Topology::External,
            worker_origin: origin.clone(),
            origin,
            worker_port,
            gateway_port: None,
            disabled: false,
        };
    }

    if probe.env_var(NO_BACKEND_ENV).is_some() {
        let worker_port = resolve_port(probe);
        return Plan {
            topology: Topology::Disabled,
            origin: loopback(worker_port),
            worker_origin: loopback(worker_port),
            worker_port,
            gateway_port: None,
            disabled: true,
        };
    }

    if flag_is_on(probe.env_var(DIRECT_ENV).as_deref()) {
        let worker_port = resolve_port(probe);
        return Plan {
            topology: Topology::Direct,
            origin: loopback(worker_port),
            worker_origin: loopback(worker_port),
            worker_port,
            gateway_port: None,
            disabled: false,
        };
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
        disabled: false,
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
        assert!(!plan.disabled);
        assert_eq!(plan.app_url(), "http://127.0.0.1:41830/app");
    }

    #[test]
    fn direct_restores_the_previous_topology() {
        let probe = StaticProbe::new()
            .with_env(DIRECT_ENV, "1")
            .with_env(PORT_ENV, "41831");
        let plan = resolve(&probe);
        assert_eq!(plan.topology, Topology::Direct);
        assert_eq!(plan.gateway_port, None);
        assert_eq!(plan.worker_port, 41831);
        assert_eq!(plan.origin, "http://127.0.0.1:41831");
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
        assert!(!plan.supervised());
        assert!(!plan.disabled, "a worker was named, so the shell waits");
    }

    #[test]
    fn an_external_origin_without_a_port_still_resolves_one() {
        let probe = StaticProbe::new().with_env(ORIGIN_ENV, "http://localhost");
        let plan = resolve(&probe);
        assert_eq!(plan.origin, "http://localhost");
        assert!(plan.worker_port >= DEFAULT_PORT);
    }

    #[test]
    fn the_kill_switch_spawns_nothing_but_still_names_an_origin() {
        let probe = StaticProbe::new().with_env(NO_BACKEND_ENV, "1");
        let plan = resolve(&probe);
        assert_eq!(plan.topology, Topology::Disabled);
        assert!(plan.disabled);
        assert!(!plan.supervised());
        assert!(plan.origin.starts_with("http://127.0.0.1:"));
    }

    #[test]
    fn an_explicit_origin_outranks_every_other_switch() {
        let probe = StaticProbe::new()
            .with_env(NO_BACKEND_ENV, "1")
            .with_env(DIRECT_ENV, "1")
            .with_env(ORIGIN_ENV, "http://127.0.0.1:9100");
        let plan = resolve(&probe);
        assert_eq!(plan.topology, Topology::External);
        assert!(!plan.disabled, "a worker was named, so the shell waits");
    }

    #[test]
    fn the_kill_switch_outranks_the_topology_choice() {
        let probe = StaticProbe::new()
            .with_env(NO_BACKEND_ENV, "1")
            .with_env(DIRECT_ENV, "1");
        assert_eq!(resolve(&probe).topology, Topology::Disabled);
    }

    #[test]
    fn every_topology_reports_a_name() {
        for (topology, name) in [
            (Topology::Gateway, "gateway"),
            (Topology::Direct, "direct"),
            (Topology::External, "external"),
            (Topology::Disabled, "disabled"),
        ] {
            assert_eq!(topology.as_str(), name);
        }
    }
}
