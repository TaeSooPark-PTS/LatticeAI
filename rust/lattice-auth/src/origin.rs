//! Origin normalisation and the forwarded-authority rule.
//!
//! Ports `normalize_origin` / `_same_site` from `latticeai/core/csrf.py` and
//! `peer_may_forward` / `effective_host` from `latticeai/core/http_origin.py`.
//! `normalize_origin` leans on `urllib.parse.urlsplit`, so the parsing rules
//! that decide what counts as a scheme, an authority and a port are reproduced
//! here rather than approximated — a looser parser would accept an origin the
//! Python guard rejects, which is the direction that costs something.
//!
//! One deliberate divergence, recorded because it is observable: `urlsplit`
//! *raises* on an unbalanced-bracket authority (`http://[::1`) and
//! `normalize_origin` does not catch that, so Python answers 500. Here it
//! resolves to `None`, which the CSRF policy reads as an unusable origin and
//! refuses with 403. Both refuse; only the status differs.

use std::net::IpAddr;

/// A normalised origin: `(scheme, host, port)` with the default port folded to
/// `None`, exactly the tuple the Python policy compares.
pub type Origin = (String, String, Option<u16>);

/// Ports that carry no information because they are implied by the scheme.
fn default_port(scheme: &str) -> Option<u16> {
    match scheme {
        "http" | "ws" => Some(80),
        "https" | "wss" => Some(443),
        _ => None,
    }
}

/// `"HTTP://Localhost:80/x"` → `("http", "localhost", None)`.
///
/// `None` for anything unusable: empty, the literal `"null"` an opaque origin
/// sends, a value with no host, or a malformed port.
pub fn normalize_origin(value: Option<&str>) -> Option<Origin> {
    let raw = value?;
    if raw.is_empty() {
        return None;
    }
    let candidate = raw.trim();
    if candidate.is_empty() || candidate.eq_ignore_ascii_case("null") {
        return None;
    }
    let owned = if candidate.contains("//") {
        candidate.to_string()
    } else {
        // A bare authority ("example.com:4825") — the `Host` header shape.
        format!("//{candidate}")
    };
    let (scheme, netloc) = split_scheme_and_netloc(&owned)?;
    let (host, port_text) = host_and_port(&netloc)?;
    if host.is_empty() {
        return None;
    }
    let port = match port_text {
        None => None,
        Some(text) => {
            // Python: `int(port)` when the text is ASCII digits, else ValueError
            // → `normalize_origin` returns None. Out-of-range likewise raises.
            if text.is_empty() || !text.bytes().all(|byte| byte.is_ascii_digit()) {
                return None;
            }
            match text.parse::<u32>() {
                Ok(number) if number <= 65535 => Some(number as u16),
                _ => return None,
            }
        }
    };
    let port = match port {
        Some(number) if default_port(&scheme) == Some(number) => None,
        other => other,
    };
    Some((scheme, host, port))
}

/// `urlsplit`'s scheme rule plus its `//authority` rule, and nothing else.
fn split_scheme_and_netloc(url: &str) -> Option<(String, String)> {
    // `urlsplit` removes tab/CR/LF anywhere and strips C0 controls and spaces
    // from both ends before it parses.
    let cleaned: String = url
        .chars()
        .filter(|character| !matches!(character, '\t' | '\r' | '\n'))
        .collect();
    let cleaned = cleaned
        .trim_matches(|character: char| character <= '\u{1F}' || character == ' ')
        .to_string();

    let mut scheme = String::new();
    let mut rest = cleaned.as_str();
    if let Some(index) = rest.find(':') {
        let head = &rest[..index];
        let legal = !head.is_empty()
            && head.starts_with(|character: char| character.is_ascii_alphabetic())
            && head.chars().all(|character| {
                character.is_ascii_alphanumeric() || matches!(character, '+' | '-' | '.')
            });
        if legal {
            scheme = head.to_ascii_lowercase();
            rest = &rest[index + 1..];
        }
    }
    if !rest.starts_with("//") {
        // No authority at all: `urlsplit` leaves netloc empty and `hostname`
        // is then None.
        return Some((scheme, String::new()));
    }
    let after = &rest[2..];
    let end = after.find(['/', '?', '#']).unwrap_or(after.len());
    let netloc = &after[..end];
    let open = netloc.contains('[');
    let close = netloc.contains(']');
    if open != close {
        // `urlsplit` raises "Invalid IPv6 URL"; unusable either way.
        return None;
    }
    Some((scheme, netloc.to_string()))
}

/// `SplitResult._hostinfo`: strip userinfo, unwrap brackets, split the port.
fn host_and_port(netloc: &str) -> Option<(String, Option<String>)> {
    let hostinfo = match netloc.rfind('@') {
        Some(index) => &netloc[index + 1..],
        None => netloc,
    };
    let (host, port) = match hostinfo.find('[') {
        Some(open) => {
            let bracketed = &hostinfo[open + 1..];
            let close = bracketed.find(']')?;
            let host = &bracketed[..close];
            let tail = &bracketed[close + 1..];
            let port = tail.find(':').map(|index| tail[index + 1..].to_string());
            (host.to_string(), port)
        }
        None => match hostinfo.find(':') {
            Some(index) => (
                hostinfo[..index].to_string(),
                Some(hostinfo[index + 1..].to_string()),
            ),
            None => (hostinfo.to_string(), None),
        },
    };
    // Python treats an empty port string as "no port".
    let port = port.filter(|text| !text.is_empty());
    // A scoped IPv6 zone keeps its case; the address does not.
    let host = match host.split_once('%') {
        Some((address, zone)) => format!("{}%{}", address.to_ascii_lowercase(), zone),
        None => host.to_ascii_lowercase(),
    };
    Some((host, port))
}

/// Host+port equality, comparing scheme only when both sides state one.
///
/// The `Host` header has no scheme, and a TLS-terminating proxy speaks `http`
/// to us while the browser reports `https`; comparing the authority is what
/// answers "this page came from this server" in both deployments.
pub fn same_site(left: &Origin, right: &Origin) -> bool {
    if left.1 != right.1 || left.2 != right.2 {
        return false;
    }
    if !left.0.is_empty() && !right.0.is_empty() {
        return left.0 == right.0;
    }
    true
}

/// One entry of the trusted-proxy allowlist: an address plus a prefix length.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IpNetwork {
    address: IpAddr,
    prefix: u8,
}

impl IpNetwork {
    /// `ipaddress.ip_network(value, strict=False)` for the shapes we accept:
    /// a bare address, or `address/prefix`. Anything else is skipped, as the
    /// Python loader skips entries it cannot parse.
    pub fn parse(value: &str) -> Option<Self> {
        let text = value.trim();
        if text.is_empty() {
            return None;
        }
        let (address_text, prefix_text) = match text.split_once('/') {
            Some((address, prefix)) => (address, Some(prefix)),
            None => (text, None),
        };
        let address: IpAddr = address_text.parse().ok()?;
        let width = if address.is_ipv4() { 32 } else { 128 };
        let prefix = match prefix_text {
            None => width,
            Some(text) => {
                let parsed: u8 = text.trim().parse().ok()?;
                if parsed > width {
                    return None;
                }
                parsed
            }
        };
        Some(Self { address, prefix })
    }

    /// Whether `candidate` falls inside this network.
    pub fn contains(&self, candidate: &IpAddr) -> bool {
        match (self.address, candidate) {
            (IpAddr::V4(network), IpAddr::V4(probe)) => {
                prefix_match(&network.octets(), &probe.octets(), self.prefix)
            }
            (IpAddr::V6(network), IpAddr::V6(probe)) => {
                prefix_match(&network.octets(), &probe.octets(), self.prefix)
            }
            _ => false,
        }
    }
}

fn prefix_match(network: &[u8], probe: &[u8], prefix: u8) -> bool {
    let full = (prefix / 8) as usize;
    if network[..full] != probe[..full] {
        return false;
    }
    let remainder = prefix % 8;
    if remainder == 0 {
        return true;
    }
    let mask = 0xffu8 << (8 - remainder);
    network[full] & mask == probe[full] & mask
}

/// Whether `X-Forwarded-*` from this direct peer may be believed.
///
/// Loopback, or a member of the configured allowlist. An unparseable or absent
/// peer is not trusted.
pub fn peer_may_forward(peer: Option<&str>, trusted_proxies: &[IpNetwork]) -> bool {
    let Some(peer) = peer else {
        return false;
    };
    let text = peer.trim();
    if text.is_empty() {
        return false;
    }
    let Ok(address) = text.parse::<IpAddr>() else {
        return false;
    };
    if address.is_loopback() {
        return true;
    }
    trusted_proxies
        .iter()
        .any(|network| network.contains(&address))
}

/// The first entry of a possibly comma-joined forwarded header.
fn first(value: Option<&str>) -> &str {
    value.unwrap_or("").split(',').next().unwrap_or("").trim()
}

/// The authority the client aimed at: the forwarded one when believable.
pub fn effective_host(
    host: Option<&str>,
    forwarded_host: Option<&str>,
    peer: Option<&str>,
    trusted_proxies: &[IpNetwork],
) -> Option<String> {
    if peer_may_forward(peer, trusted_proxies) {
        let claimed = first(forwarded_host);
        if !claimed.is_empty() {
            return Some(claimed.to_string());
        }
    }
    let own = host.unwrap_or("").trim();
    if own.is_empty() {
        None
    } else {
        Some(own.to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn origin(scheme: &str, host: &str, port: Option<u16>) -> Origin {
        (scheme.into(), host.into(), port)
    }

    #[test]
    fn lowercases_and_drops_the_default_port() {
        assert_eq!(
            normalize_origin(Some("HTTP://Localhost:80/x")),
            Some(origin("http", "localhost", None))
        );
        assert_eq!(
            normalize_origin(Some("https://example.com:443")),
            Some(origin("https", "example.com", None))
        );
        assert_eq!(
            normalize_origin(Some("http://127.0.0.1:4825")),
            Some(origin("http", "127.0.0.1", Some(4825)))
        );
    }

    #[test]
    fn a_bare_authority_is_the_host_header_shape() {
        assert_eq!(
            normalize_origin(Some("example.com:4825")),
            Some(origin("", "example.com", Some(4825)))
        );
        assert_eq!(
            normalize_origin(Some("[::1]:4825")),
            Some(origin("", "::1", Some(4825)))
        );
    }

    #[test]
    fn unusable_values_are_none() {
        for value in [
            None,
            Some(""),
            Some("   "),
            Some("null"),
            Some("NULL"),
            Some("http://"),
            Some("example.com:notaport"),
            Some("http://example.com:99999"),
            Some("http://[::1"),
        ] {
            assert_eq!(normalize_origin(value), None, "{value:?}");
        }
    }

    #[test]
    fn userinfo_and_ipv6_zones_parse_like_python() {
        assert_eq!(
            normalize_origin(Some("http://user:pw@Example.COM:8080")),
            Some(origin("http", "example.com", Some(8080)))
        );
        assert_eq!(
            normalize_origin(Some("http://[fe80::1%tESt]:1234")),
            Some(origin("http", "fe80::1%tESt", Some(1234)))
        );
    }

    #[test]
    fn control_characters_are_stripped_before_parsing() {
        assert_eq!(
            normalize_origin(Some("http://exa\tmple.com\n")),
            Some(origin("http", "example.com", None))
        );
    }

    #[test]
    fn a_custom_scheme_keeps_its_port() {
        assert_eq!(
            normalize_origin(Some("chrome-extension://abcdef")),
            Some(origin("chrome-extension", "abcdef", None))
        );
    }

    #[test]
    fn same_site_ignores_a_missing_scheme() {
        let with_scheme = origin("https", "example.com", Some(4825));
        let bare = origin("", "example.com", Some(4825));
        assert!(same_site(&with_scheme, &bare));
        assert!(!same_site(
            &with_scheme,
            &origin("http", "example.com", Some(4825))
        ));
        assert!(!same_site(
            &with_scheme,
            &origin("https", "example.com", None)
        ));
        assert!(!same_site(&bare, &origin("", "other.com", Some(4825))));
    }

    #[test]
    fn loopback_peers_may_forward_and_strangers_may_not() {
        assert!(peer_may_forward(Some("127.0.0.1"), &[]));
        assert!(peer_may_forward(Some("::1"), &[]));
        assert!(!peer_may_forward(Some("10.0.0.9"), &[]));
        assert!(!peer_may_forward(Some("testclient"), &[]));
        assert!(!peer_may_forward(None, &[]));
        assert!(!peer_may_forward(Some("  "), &[]));
        let allow = vec![IpNetwork::parse("10.0.0.0/8").unwrap()];
        assert!(peer_may_forward(Some("10.0.0.9"), &allow));
        assert!(!peer_may_forward(Some("11.0.0.9"), &allow));
    }

    #[test]
    fn ip_network_parsing_matches_the_python_loader() {
        assert!(IpNetwork::parse("").is_none());
        assert!(IpNetwork::parse("not-an-ip").is_none());
        assert!(IpNetwork::parse("10.0.0.1/33").is_none());
        assert!(IpNetwork::parse("10.0.0.1/x").is_none());
        let host_route = IpNetwork::parse("10.0.0.1").unwrap();
        assert!(host_route.contains(&"10.0.0.1".parse().unwrap()));
        assert!(!host_route.contains(&"10.0.0.2".parse().unwrap()));
        let v6 = IpNetwork::parse("fe80::/10").unwrap();
        assert!(v6.contains(&"fe80::1".parse().unwrap()));
        assert!(!v6.contains(&"2001::1".parse().unwrap()));
        // Mixed families never match.
        assert!(!v6.contains(&"10.0.0.1".parse().unwrap()));
    }

    #[test]
    fn forwarded_host_wins_only_for_a_trusted_peer() {
        assert_eq!(
            effective_host(
                Some("worker:4826"),
                Some("front.door:4825"),
                Some("127.0.0.1"),
                &[]
            ),
            Some("front.door:4825".to_string())
        );
        assert_eq!(
            effective_host(
                Some("worker:4826"),
                Some("front.door:4825"),
                Some("8.8.8.8"),
                &[]
            ),
            Some("worker:4826".to_string())
        );
        assert_eq!(
            effective_host(Some("worker:4826"), Some("  "), Some("127.0.0.1"), &[]),
            Some("worker:4826".to_string())
        );
        assert_eq!(effective_host(None, None, None, &[]), None);
        assert_eq!(
            effective_host(Some("a:1"), Some("b:2, c:3"), Some("127.0.0.1"), &[]),
            Some("b:2".to_string())
        );
    }
}
