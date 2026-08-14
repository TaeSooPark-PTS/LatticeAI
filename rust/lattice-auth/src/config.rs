//! The environment this crate reads, parsed once.
//!
//! Ports the auth-relevant slice of `latticeai/core/config.py` plus the two
//! derivations that live outside it: `secure_cookies` (`security_runtime.py`,
//! `is_public or network_exposed`) and the CSRF allowlist the web runtime
//! assembles (`cors_allowed_origins + csrf_trusted_origins`).
//!
//! Two safety clamps from the Python original are reproduced exactly, because
//! they are the reason a LAN binding cannot be talked into being open:
//! `require_auth` is forced **on** and `open_registration` forced **off**
//! whenever the bind is externally reachable, no matter what the environment
//! says.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use serde_json::Value;

use crate::origin::IpNetwork;

/// Where the identity provider sends the browser back to.
pub const SSO_CALLBACK_PATH: &str = "/auth/sso/callback";

/// The SSO surface `GET /auth/sso/config` publishes.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct SsoSettings {
    /// Whether single sign-on is both configured and switched on.
    pub enabled: bool,
    /// Display name for the provider.
    pub provider_name: String,
    /// OIDC discovery document URL.
    pub discovery_url: String,
    /// OAuth client id.
    pub client_id: String,
    /// OAuth client secret — never published, only its presence.
    pub client_secret: String,
    /// Where the provider returns the browser.
    pub redirect_uri: String,
    /// Requested scopes.
    pub scopes: String,
}

/// Everything `lattice-auth` needs to know about this install.
#[derive(Debug, Clone)]
pub struct AuthConfig {
    /// `<data_dir>` — where `users.json` and `sessions.json` live.
    pub data_dir: PathBuf,
    /// The bind address, used for the CSRF default origins.
    pub host: String,
    /// The front door's port, likewise.
    pub port: u16,
    /// `is_public or network_exposed` — anything but this machine can connect.
    pub externally_reachable: bool,
    /// Whether the bind is loopback (the CSRF no-origin exemption).
    pub bind_is_loopback: bool,
    /// Whether a session is required at all.
    pub require_auth: bool,
    /// Whether `POST /register` accepts an uninvited caller.
    pub open_registration: bool,
    /// Whether the invite gate is switched on.
    pub invite_gate_enabled: bool,
    /// `Secure` on the session cookie.
    pub secure_cookies: bool,
    /// Session lifetime in seconds, and the cookie's `Max-Age`.
    pub session_ttl: i64,
    /// Emails promoted to `admin` regardless of their stored role.
    pub admin_emails: Vec<String>,
    /// Whether the token-bucket limiter is armed.
    pub rate_limit_enabled: bool,
    /// Browser origins allowed to send cookie-authenticated writes.
    pub csrf_trusted_origins: Vec<String>,
    /// Peers whose `X-Forwarded-*` headers may be believed.
    pub trusted_proxies: Vec<IpNetwork>,
    /// The SSO surface, env defaults merged with `sso_config.json`.
    pub sso: SsoSettings,
}

impl Default for AuthConfig {
    fn default() -> Self {
        Self::from_map(&HashMap::new(), None)
    }
}

impl AuthConfig {
    /// Parse the live process environment.
    pub fn from_env() -> Self {
        let env: HashMap<String, String> = std::env::vars().collect();
        let home = std::env::var_os("HOME").map(PathBuf::from);
        Self::from_map(&env, home.as_deref())
    }

    /// Parse an explicit environment — the seam tests and fixtures use.
    pub fn from_map(env: &HashMap<String, String>, home: Option<&Path>) -> Self {
        let value = |key: &str, default: &str| -> String {
            let raw = env.get(key).map(String::as_str).unwrap_or("");
            if raw.is_empty() {
                default.to_string()
            } else {
                raw.to_string()
            }
        };
        let flag = |key: &str, default: bool| -> bool {
            match env.get(key).map(|raw| raw.trim().to_lowercase()) {
                None => default,
                Some(raw) => match raw.as_str() {
                    "1" | "true" | "yes" | "on" => true,
                    "0" | "false" | "no" | "off" => false,
                    _ => default,
                },
            }
        };
        let list = |key: &str| -> Vec<String> {
            value(key, "")
                .split(',')
                .map(str::trim)
                .filter(|item| !item.is_empty())
                .map(str::to_string)
                .collect()
        };

        let app_mode = value("LATTICEAI_MODE", "local").to_lowercase();
        let is_public = app_mode == "public";
        let host = value("LATTICEAI_HOST", "127.0.0.1");
        let port = value("LATTICEAI_PORT", "4825")
            .trim()
            .parse::<u32>()
            .ok()
            .filter(|port| (1..=65535).contains(port))
            .unwrap_or(4825) as u16;
        let bind_is_loopback = host_is_loopback(&host);
        let externally_reachable = is_public || !bind_is_loopback;

        let data_dir =
            lattice_core::resolve_data_dir(env.get("LATTICEAI_DATA_DIR").map(String::as_str), home);

        // The web runtime hands the guard the CORS allowlist *and* the
        // operator's extra origins; both halves are trusted for writes.
        let mut csrf_trusted_origins = vec![
            format!("http://localhost:{port}"),
            format!("http://127.0.0.1:{port}"),
        ];
        csrf_trusted_origins.extend(list("LATTICEAI_CORS_ALLOWED_ORIGINS"));
        if flag("LATTICEAI_CORS_ALLOW_NETWORK", false) {
            csrf_trusted_origins.push(format!("http://{host}:{port}"));
            csrf_trusted_origins.push(format!("https://{host}:{port}"));
        }
        csrf_trusted_origins.extend(list("LATTICEAI_CSRF_TRUSTED_ORIGINS"));

        let discovery_url = value("OIDC_DISCOVERY_URL", "");
        let client_id = value("OIDC_CLIENT_ID", "");
        let client_secret = value("OIDC_CLIENT_SECRET", "");
        let default_redirect = format!("http://localhost:{port}{SSO_CALLBACK_PATH}");
        let sso = SsoSettings {
            enabled: !discovery_url.is_empty()
                && !client_id.is_empty()
                && !client_secret.is_empty(),
            provider_name: value("OIDC_PROVIDER_NAME", "SSO"),
            discovery_url,
            client_id,
            client_secret,
            redirect_uri: value("OIDC_REDIRECT_URI", &default_redirect),
            scopes: "openid email profile".to_string(),
        };

        Self {
            data_dir,
            host,
            port,
            externally_reachable,
            bind_is_loopback,
            // An explicit `false` must never turn a reachable bind into an
            // unauthenticated service.
            require_auth: externally_reachable || flag("LATTICEAI_REQUIRE_AUTH", false),
            // Likewise: a reachable bind is closed-registration regardless.
            open_registration: !externally_reachable && flag("LATTICEAI_OPEN_REGISTRATION", true),
            invite_gate_enabled: flag("LATTICEAI_INVITE_GATE_ENABLED", false),
            secure_cookies: externally_reachable,
            session_ttl: crate::sessions::SESSION_TTL as i64,
            admin_emails: list("LATTICEAI_ADMIN_EMAILS")
                .into_iter()
                .map(|item| item.to_lowercase())
                .collect(),
            rate_limit_enabled: env
                .get("LATTICEAI_RATE_LIMIT")
                .map(String::as_str)
                .unwrap_or("1")
                != "0",
            csrf_trusted_origins,
            trusted_proxies: list("LATTICEAI_TRUSTED_PROXIES")
                .iter()
                .filter_map(|item| IpNetwork::parse(item))
                .collect(),
            sso,
        }
    }

    /// Merge `<data_dir>/sso_config.json` over the env defaults, exactly as
    /// `sso_config_runtime.load_sso_config` does (including the final
    /// `enabled` re-derivation, which no stored `true` can survive without a
    /// discovery URL, a client id and a secret).
    pub fn with_stored_sso(mut self) -> Self {
        let path = self.data_dir.join("sso_config.json");
        if let Ok(text) = std::fs::read_to_string(&path) {
            if let Ok(Value::Object(stored)) = serde_json::from_str::<Value>(&text) {
                let text_of = |key: &str, current: &str| -> String {
                    match stored.get(key) {
                        Some(Value::Null) | None => current.to_string(),
                        Some(Value::String(value)) => value.clone(),
                        Some(other) => other.to_string(),
                    }
                };
                self.sso.provider_name = text_of("provider_name", &self.sso.provider_name);
                self.sso.discovery_url = text_of("discovery_url", &self.sso.discovery_url);
                self.sso.client_id = text_of("client_id", &self.sso.client_id);
                self.sso.client_secret = text_of("client_secret", &self.sso.client_secret);
                self.sso.redirect_uri = text_of("redirect_uri", &self.sso.redirect_uri);
                self.sso.scopes = text_of("scopes", &self.sso.scopes);
                if let Some(enabled) = stored.get("enabled") {
                    self.sso.enabled = truthy(enabled);
                }
            }
        }
        if self.sso.provider_name.is_empty() {
            self.sso.provider_name = "SSO".into();
        }
        if self.sso.scopes.is_empty() {
            self.sso.scopes = "openid email profile".into();
        }
        self.sso.enabled = self.sso.enabled
            && !self.sso.discovery_url.is_empty()
            && !self.sso.client_id.is_empty()
            && !self.sso.client_secret.is_empty();
        self
    }
}

fn truthy(value: &Value) -> bool {
    match value {
        Value::Bool(flag) => *flag,
        Value::String(text) => !text.is_empty(),
        Value::Number(number) => number.as_f64() != Some(0.0),
        Value::Null => false,
        Value::Array(items) => !items.is_empty(),
        Value::Object(map) => !map.is_empty(),
    }
}

/// `latticeai.core.security.host_is_loopback`.
pub fn host_is_loopback(host: &str) -> bool {
    if matches!(host, "localhost" | "127.0.0.1" | "::1") {
        return true;
    }
    host.parse::<std::net::IpAddr>()
        .map(|address| address.is_loopback())
        .unwrap_or(false)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn env(pairs: &[(&str, &str)]) -> HashMap<String, String> {
        pairs
            .iter()
            .map(|(key, value)| ((*key).to_string(), (*value).to_string()))
            .collect()
    }

    #[test]
    fn the_local_default_is_loopback_no_auth_open_registration() {
        let config = AuthConfig::from_map(&env(&[]), Some(Path::new("/home/x")));
        assert_eq!(config.data_dir, PathBuf::from("/home/x/.ltcai"));
        assert!(!config.require_auth);
        assert!(config.open_registration);
        assert!(!config.secure_cookies);
        assert!(config.bind_is_loopback);
        assert!(!config.externally_reachable);
        assert_eq!(config.session_ttl, 86_400);
        assert!(config.rate_limit_enabled);
        assert!(config
            .csrf_trusted_origins
            .contains(&"http://127.0.0.1:4825".to_string()));
    }

    #[test]
    fn a_reachable_bind_clamps_auth_and_registration() {
        let config = AuthConfig::from_map(
            &env(&[
                ("LATTICEAI_HOST", "0.0.0.0"),
                ("LATTICEAI_REQUIRE_AUTH", "false"),
                ("LATTICEAI_OPEN_REGISTRATION", "true"),
            ]),
            None,
        );
        assert!(config.require_auth);
        assert!(!config.open_registration);
        assert!(config.secure_cookies);
        assert!(config.externally_reachable);
    }

    #[test]
    fn public_mode_is_reachable_even_on_loopback() {
        let config = AuthConfig::from_map(&env(&[("LATTICEAI_MODE", "public")]), None);
        assert!(config.externally_reachable);
        assert!(config.require_auth);
        assert!(config.bind_is_loopback);
    }

    #[test]
    fn env_lists_and_flags_parse_like_python() {
        let config = AuthConfig::from_map(
            &env(&[
                ("LATTICEAI_PORT", "70000"),
                ("LATTICEAI_RATE_LIMIT", "0"),
                ("LATTICEAI_ADMIN_EMAILS", " A@B.com , ,c@d.com"),
                ("LATTICEAI_TRUSTED_PROXIES", "10.0.0.0/8,nonsense"),
                ("LATTICEAI_CSRF_TRUSTED_ORIGINS", "https://front.door"),
                ("LATTICEAI_CORS_ALLOW_NETWORK", "yes"),
                ("LATTICEAI_REQUIRE_AUTH", "maybe"),
            ]),
            None,
        );
        assert_eq!(config.port, 4825, "an out-of-range port falls back");
        assert!(!config.rate_limit_enabled);
        assert_eq!(config.admin_emails, vec!["a@b.com", "c@d.com"]);
        assert_eq!(config.trusted_proxies.len(), 1);
        assert!(config
            .csrf_trusted_origins
            .contains(&"https://front.door".to_string()));
        assert!(config
            .csrf_trusted_origins
            .contains(&"https://127.0.0.1:4825".to_string()));
        assert!(!config.require_auth, "an unparsable flag keeps the default");
    }

    #[test]
    fn sso_defaults_are_disabled_but_describable() {
        let config = AuthConfig::from_map(&env(&[]), None);
        assert!(!config.sso.enabled);
        assert_eq!(config.sso.provider_name, "SSO");
        assert_eq!(
            config.sso.redirect_uri,
            "http://localhost:4825/auth/sso/callback"
        );
        assert_eq!(config.sso.scopes, "openid email profile");
    }

    #[test]
    fn env_sso_is_enabled_only_when_complete() {
        let config = AuthConfig::from_map(
            &env(&[
                ("OIDC_DISCOVERY_URL", "https://idp/.well-known"),
                ("OIDC_CLIENT_ID", "abc"),
            ]),
            None,
        );
        assert!(!config.sso.enabled);
        let config = AuthConfig::from_map(
            &env(&[
                ("OIDC_DISCOVERY_URL", "https://idp/.well-known"),
                ("OIDC_CLIENT_ID", "abc"),
                ("OIDC_CLIENT_SECRET", "shh"),
                ("OIDC_PROVIDER_NAME", "Okta"),
            ]),
            None,
        );
        assert!(config.sso.enabled);
        assert_eq!(config.sso.provider_name, "Okta");
    }

    #[test]
    fn the_stored_sso_file_wins_but_cannot_enable_an_incomplete_config() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(
            dir.path().join("sso_config.json"),
            r#"{"enabled":true,"provider_name":"","client_id":"stored","scopes":null}"#,
        )
        .unwrap();
        let mut config = AuthConfig::from_map(&env(&[]), None);
        config.data_dir = dir.path().to_path_buf();
        let config = config.with_stored_sso();
        assert_eq!(config.sso.client_id, "stored");
        assert_eq!(config.sso.provider_name, "SSO");
        assert_eq!(config.sso.scopes, "openid email profile");
        assert!(!config.sso.enabled, "no discovery URL and no secret");
    }

    #[test]
    fn a_missing_or_broken_sso_file_leaves_the_env_defaults() {
        let dir = tempfile::tempdir().unwrap();
        let mut config = AuthConfig::from_map(&env(&[]), None);
        config.data_dir = dir.path().to_path_buf();
        assert_eq!(config.clone().with_stored_sso().sso.provider_name, "SSO");
        std::fs::write(dir.path().join("sso_config.json"), "[]").unwrap();
        assert_eq!(config.with_stored_sso().sso.provider_name, "SSO");
    }

    #[test]
    fn loopback_detection_matches_python() {
        for host in ["localhost", "127.0.0.1", "127.0.0.5", "::1"] {
            assert!(host_is_loopback(host), "{host}");
        }
        for host in ["0.0.0.0", "192.168.1.4", "example.com", ""] {
            assert!(!host_is_loopback(host), "{host}");
        }
    }

    #[test]
    fn the_default_impl_reads_an_empty_environment() {
        let config = AuthConfig::default();
        assert_eq!(config.port, 4825);
    }

    #[test]
    fn truthiness_matches_python() {
        assert!(truthy(&Value::Bool(true)));
        assert!(!truthy(&Value::Bool(false)));
        assert!(!truthy(&Value::Null));
        assert!(truthy(&serde_json::json!("x")));
        assert!(!truthy(&serde_json::json!("")));
        assert!(!truthy(&serde_json::json!(0)));
        assert!(truthy(&serde_json::json!([1])));
        assert!(!truthy(&serde_json::json!({})));
    }
}
