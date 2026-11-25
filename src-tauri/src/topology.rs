use lattice_host::supervisor::{find_free_port, HostProbe, DEFAULT_PORT, DEFAULT_SCAN_ATTEMPTS};

pub const ORIGIN_ENV: &str = "LATTICEAI_DESKTOP_BACKEND_ORIGIN";
pub const NO_BACKEND_ENV: &str = "LATTICEAI_DESKTOP_NO_BACKEND";
pub const DIRECT_ENV: &str = "LATTICEAI_DESKTOP_DIRECT";
pub const PORT_ENV: &str = "LATTICEAI_PORT";
pub const PINNED_PRODUCTION_PORT: u16 = DEFAULT_PORT;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[allow(dead_code)]
pub enum PortPolicy {
    PinnedProduction,
    AllowDynamicFallback,
}

impl PortPolicy {
    pub const fn for_build() -> Self {
        #[cfg(not(debug_assertions))]
        {
            Self::PinnedProduction
        }
        #[cfg(debug_assertions)]
        {
            Self::AllowDynamicFallback
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Topology {
    Gateway,
    External,
}

impl Topology {
    pub fn as_str(self) -> &'static str {
        match self {
            Topology::Gateway => "gateway",
            Topology::External => "external",
        }
    }

    pub fn supervises(self) -> bool {
        matches!(self, Topology::Gateway)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Plan {
    pub topology: Topology,
    pub origin: String,
    pub worker_origin: String,
    pub worker_port: u16,
    pub gateway_port: Option<u16>,
}

impl Plan {
    pub fn supervised(&self) -> bool {
        self.topology.supervises()
    }

    pub fn app_url(&self) -> String {
        format!("{}/app", self.origin.trim_end_matches('/'))
    }
}

pub fn flag_is_on(raw: Option<&str>) -> bool {
    matches!(
        raw.map(|value| value.trim().to_ascii_lowercase())
            .as_deref(),
        Some("1" | "true" | "yes" | "on")
    )
}

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

pub fn validate_external_origin(raw: &str) -> Option<String> {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return None;
    }
    let (scheme, rest) = trimmed.split_once("://")?;
    let scheme_lower = scheme.to_ascii_lowercase();
    if scheme_lower != "http" && scheme_lower != "https" {
        return None;
    }
    let authority = rest.split('/').next().unwrap_or_default();
    if authority.is_empty()
        || authority.contains('@')
        || authority.contains('?')
        || authority.contains('#')
    {
        return None;
    }
    let host = if let Some(after_open) = authority.strip_prefix('[') {
        let (ipv6, tail) = after_open.split_once(']')?;
        if !tail.is_empty() {
            let port_str = tail.strip_prefix(':')?;
            let port = port_str.parse::<u16>().ok()?;
            if port == 0 {
                return None;
            }
        }
        ipv6.to_ascii_lowercase()
    } else {
        match authority.split_once(':') {
            Some((h, port_str)) => {
                let port = port_str.parse::<u16>().ok()?;
                if port == 0 {
                    return None;
                }
                h.to_ascii_lowercase()
            }
            None => authority.to_ascii_lowercase(),
        }
    };
    if matches!(host.as_str(), "127.0.0.1" | "localhost" | "::1") {
        Some(trimmed.to_string())
    } else {
        None
    }
}

fn next_safe_port(gateway_port: u16) -> u16 {
    let candidate = gateway_port
        .checked_add(1)
        .filter(|&p| p >= 1024)
        .unwrap_or(DEFAULT_PORT);
    if candidate == gateway_port {
        DEFAULT_PORT.saturating_add(1)
    } else {
        candidate
    }
}

pub fn resolve_port_with_policy(probe: &dyn HostProbe, policy: PortPolicy) -> Result<u16, String> {
    let raw_port = probe
        .env_var(PORT_ENV)
        .map(|raw| raw.trim().to_string())
        .filter(|raw| !raw.is_empty());
    let pinned = raw_port
        .as_deref()
        .and_then(|raw| raw.parse::<u16>().ok())
        .filter(|port| *port != 0);

    match policy {
        PortPolicy::PinnedProduction => match pinned {
            Some(port) if port == PINNED_PRODUCTION_PORT => Ok(PINNED_PRODUCTION_PORT),
            Some(port) => Err(format!(
                "{PORT_ENV}={port} conflicts with production desktop security policy: \
                 tauri.conf.json CSP and capabilities/default.json pin http://127.0.0.1:{PINNED_PRODUCTION_PORT}. \
                 Unset {PORT_ENV} or set it to {PINNED_PRODUCTION_PORT}."
            )),
            None => {
                if let Some(invalid) = raw_port {
                    return Err(format!(
                        "{PORT_ENV}={invalid:?} is invalid; production desktop security policy \
                         pins http://127.0.0.1:{PINNED_PRODUCTION_PORT}."
                    ));
                }
                Ok(PINNED_PRODUCTION_PORT)
            }
        },
        PortPolicy::AllowDynamicFallback => match pinned {
            Some(port) => Ok(port),
            None => match find_free_port(DEFAULT_PORT, DEFAULT_SCAN_ATTEMPTS, &[]) {
                Ok(port) => Ok(port),
                Err(err) => {
                    eprintln!(
                        "lattice-ai-desktop: port scan from {DEFAULT_PORT} failed ({err}); falling back to {DEFAULT_PORT}"
                    );
                    Ok(DEFAULT_PORT)
                }
            },
        },
    }
}

#[cfg(test)]
pub fn resolve_port(probe: &dyn HostProbe) -> u16 {
    resolve_port_with_policy(probe, PortPolicy::AllowDynamicFallback).unwrap_or(DEFAULT_PORT)
}

fn loopback(port: u16) -> String {
    format!("http://127.0.0.1:{port}")
}

pub fn is_pinned_production_origin(origin: &str) -> bool {
    let trimmed = origin.trim().trim_end_matches('/');
    trimmed.eq_ignore_ascii_case(&format!("http://127.0.0.1:{PINNED_PRODUCTION_PORT}"))
}

pub fn resolve_with_policy(probe: &dyn HostProbe, policy: PortPolicy) -> Result<Plan, String> {
    let raw_external = probe
        .env_var(ORIGIN_ENV)
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty());
    if let Some(raw) = raw_external {
        if let Some(origin) = validate_external_origin(&raw) {
            if policy == PortPolicy::PinnedProduction && !is_pinned_production_origin(&origin) {
                return Err(format!(
                    "{ORIGIN_ENV}={origin:?} conflicts with production desktop security policy: \
                     tauri.conf.json CSP and capabilities/default.json pin http://127.0.0.1:{PINNED_PRODUCTION_PORT}. \
                     Point {ORIGIN_ENV} at http://127.0.0.1:{PINNED_PRODUCTION_PORT}."
                ));
            }
            let parsed_port = origin_port(&origin);
            let host_port = match parsed_port {
                Some(port) => port,
                None => resolve_port_with_policy(probe, policy)?,
            };
            return Ok(Plan {
                topology: Topology::External,
                worker_origin: origin.clone(),
                origin,
                worker_port: host_port,
                gateway_port: None,
            });
        }
        eprintln!(
            "lattice-ai-desktop: ignoring invalid {ORIGIN_ENV}={raw:?} \
             (only http(s):// loopback origins 127.0.0.1, localhost, or [::1] are allowed)"
        );
    }

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

    let gateway_port = resolve_port_with_policy(probe, policy)?;
    let start_port = next_safe_port(gateway_port);
    let worker_port = match find_free_port(start_port, DEFAULT_SCAN_ATTEMPTS, &[gateway_port]) {
        Ok(port) => port,
        Err(err) => {
            eprintln!(
                "lattice-ai-desktop: worker port scan from {start_port} failed ({err}); falling back to {start_port}"
            );
            start_port
        }
    };
    Ok(Plan {
        topology: Topology::Gateway,
        origin: loopback(gateway_port),
        worker_origin: loopback(worker_port),
        worker_port,
        gateway_port: Some(gateway_port),
    })
}

#[cfg(test)]
pub fn resolve(probe: &dyn HostProbe) -> Plan {
    resolve_with_policy(probe, PortPolicy::AllowDynamicFallback)
        .expect("dynamic fallback policy never fails port resolution")
}

#[cfg(test)]
pub(crate) static PORT_4825_TEST_LOCK: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

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

    #[test]
    fn external_origin_validation_blocks_non_loopback_and_non_http_schemes() {
        assert_eq!(
            validate_external_origin("http://127.0.0.1:4825"),
            Some("http://127.0.0.1:4825".to_string())
        );
        assert_eq!(
            validate_external_origin("https://[::1]:4825"),
            Some("https://[::1]:4825".to_string())
        );
        assert_eq!(
            validate_external_origin("http://localhost"),
            Some("http://localhost".to_string())
        );
        for rejected in [
            "javascript:alert(1)",
            "file:///etc/passwd",
            "http://evil.example:4825",
            "https://192.168.1.10:4825",
            "http://user:pass@127.0.0.1:4825",
            "http://127.0.0.1:0",
        ] {
            assert_eq!(validate_external_origin(rejected), None, "{rejected}");
        }
        let plan = resolve(
            &StaticProbe::new()
                .with_env(ORIGIN_ENV, "https://evil.example")
                .with_env(PORT_ENV, "41839"),
        );
        assert_eq!(plan.topology, Topology::Gateway);
        assert_eq!(plan.gateway_port, Some(41839));
    }

    #[test]
    fn next_safe_port_never_wraps_to_zero() {
        assert_eq!(next_safe_port(65535), DEFAULT_PORT);
        assert_ne!(next_safe_port(65535), 0);
        assert_eq!(next_safe_port(4825), 4826);
    }

    #[test]
    fn production_policy_pins_default_port_even_when_occupied() {
        let _guard = PORT_4825_TEST_LOCK.blocking_lock();
        let _occupied = std::net::TcpListener::bind(("127.0.0.1", PINNED_PRODUCTION_PORT)).ok();

        let port = resolve_port_with_policy(&StaticProbe::new(), PortPolicy::PinnedProduction)
            .expect("unset LATTICEAI_PORT resolves to pinned 4825");
        assert_eq!(
            port, PINNED_PRODUCTION_PORT,
            "production policy must never silently scan upward to 4826+"
        );

        let plan = resolve_with_policy(&StaticProbe::new(), PortPolicy::PinnedProduction)
            .expect("gateway plan pins 4825");
        assert_eq!(plan.gateway_port, Some(PINNED_PRODUCTION_PORT));
        assert_eq!(
            plan.origin,
            format!("http://127.0.0.1:{PINNED_PRODUCTION_PORT}")
        );
        assert_ne!(
            plan.worker_port, PINNED_PRODUCTION_PORT,
            "internal worker still moves behind the pinned gateway port"
        );

        if _occupied.is_some() {
            let dev_port =
                resolve_port_with_policy(&StaticProbe::new(), PortPolicy::AllowDynamicFallback)
                    .expect("dev fallback port");
            assert!(
                dev_port > PINNED_PRODUCTION_PORT,
                "dev fallback scans upward when 4825 is held, got {dev_port}"
            );
        }
    }

    #[test]
    fn production_policy_rejects_mismatched_ports_and_accepts_pinned_4825() {
        let bad_port = StaticProbe::new().with_env(PORT_ENV, "4826");
        let err = resolve_port_with_policy(&bad_port, PortPolicy::PinnedProduction)
            .expect_err("non-4825 LATTICEAI_PORT must fail fast in production");
        assert!(err.contains("4825"), "{err}");
        assert!(err.contains("CSP"), "{err}");

        let invalid_port = StaticProbe::new().with_env(PORT_ENV, "not-a-port");
        let err = resolve_port_with_policy(&invalid_port, PortPolicy::PinnedProduction)
            .expect_err("invalid LATTICEAI_PORT must fail fast in production");
        assert!(err.contains("4825"), "{err}");

        let ok_port = StaticProbe::new().with_env(PORT_ENV, "4825");
        assert_eq!(
            resolve_port_with_policy(&ok_port, PortPolicy::PinnedProduction),
            Ok(4825)
        );

        for rejected_origin in [
            "http://127.0.0.1:9100",
            "https://127.0.0.1:4825",
            "http://[::1]:4825",
            "http://localhost:4825",
        ] {
            let bad_external = StaticProbe::new().with_env(ORIGIN_ENV, rejected_origin);
            let err = resolve_with_policy(&bad_external, PortPolicy::PinnedProduction)
                .expect_err(&format!("{rejected_origin} must fail fast in production"));
            assert!(err.contains("http://127.0.0.1:4825"), "{err}");
        }

        for accepted_origin in ["http://127.0.0.1:4825", "http://127.0.0.1:4825/"] {
            let ok_external = StaticProbe::new().with_env(ORIGIN_ENV, accepted_origin);
            let plan = resolve_with_policy(&ok_external, PortPolicy::PinnedProduction)
                .expect("http://127.0.0.1:4825 matches production CSP and capabilities");
            assert_eq!(plan.topology, Topology::External);
            assert_eq!(plan.worker_port, 4825);
        }
    }

    #[test]
    fn tauri_conf_csp_and_capabilities_match_pinned_production_port() {
        let conf: serde_json::Value = serde_json::from_str(include_str!("../tauri.conf.json"))
            .expect("valid tauri.conf.json");
        let csp = conf["app"]["security"]["csp"]
            .as_str()
            .expect("app.security.csp string");
        let pinned_origin = format!("http://127.0.0.1:{PINNED_PRODUCTION_PORT}");
        assert!(
            csp.contains(&pinned_origin),
            "tauri.conf.json CSP must allow {pinned_origin}, got: {csp}"
        );

        let caps: serde_json::Value =
            serde_json::from_str(include_str!("../capabilities/default.json"))
                .expect("valid capabilities/default.json");
        let urls: Vec<&str> = caps["remote"]["urls"]
            .as_array()
            .expect("remote.urls array")
            .iter()
            .filter_map(|v| v.as_str())
            .collect();
        assert!(
            urls.contains(&pinned_origin.as_str()),
            "capabilities/default.json must allow {pinned_origin}, got: {urls:?}"
        );
    }
}
