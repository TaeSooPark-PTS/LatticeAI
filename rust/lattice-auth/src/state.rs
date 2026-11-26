use std::net::{IpAddr, SocketAddr};
use std::sync::Arc;

use axum::extract::ConnectInfo;
use axum::http::request::Parts;
use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use serde_json::{json, Map, Value};

use crate::clock::Clock;
use crate::config::AuthConfig;
use crate::cookies::{cookie_value, SESSION_COOKIE_NAME};
use crate::csrf::CsrfOriginPolicy;
use crate::messages::{detail_error, ADMIN_REQUIRED_LITERAL, LOGIN_REQUIRED_LITERAL};
use crate::policy::{check_role, normalize_role};
use crate::pyjson::OrderedMap;
use crate::ratelimit::RateLimiter;
use crate::sessions::SessionStore;
use crate::users::{normalize_email, UserStore, Users};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Identity {
    pub email: String,
    pub role: String,
}

impl Identity {
    pub fn local_owner() -> Self {
        Self {
            email: String::new(),
            role: "owner".into(),
        }
    }

    pub fn is_local_owner(&self) -> bool {
        self.email.is_empty()
    }

    pub fn can(&self, capability: &str) -> bool {
        crate::policy::role_has_capability(&self.role, capability)
    }
}

#[derive(Debug, Clone)]
pub struct PeerAddr(pub String);

pub fn peer_of(parts: &Parts) -> Option<String> {
    if let Some(PeerAddr(peer)) = parts.extensions.get::<PeerAddr>() {
        return Some(peer.clone());
    }
    parts
        .extensions
        .get::<ConnectInfo<SocketAddr>>()
        .map(|info| info.0.ip().to_string())
}

pub type InviteGate = Arc<dyn Fn(&HeaderMap) -> bool + Send + Sync>;

pub struct AuthState {
    config: AuthConfig,
    users: UserStore,
    sessions: SessionStore,
    limiter: RateLimiter,
    csrf: CsrfOriginPolicy,
    clock: Clock,
    invite_gate: std::sync::OnceLock<InviteGate>,
    audit_sink: std::sync::OnceLock<Arc<dyn crate::enterprise::AuthAuditSink>>,
}

impl std::fmt::Debug for AuthState {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("AuthState")
            .field("data_dir", &self.config.data_dir)
            .field("require_auth", &self.config.require_auth)
            .field("trusted_local_owner", &self.trusted_local_owner())
            .field("invite_gate", &self.invite_gate.get().is_some())
            .finish()
    }
}

impl AuthState {
    pub fn new(config: AuthConfig) -> Arc<Self> {
        Self::with_clock(config, Clock::system())
    }

    pub fn with_clock(config: AuthConfig, clock: Clock) -> Arc<Self> {
        crate::atomic::ensure_private_dir(&config.data_dir);
        let users = UserStore::new(&config.data_dir);
        let sessions =
            SessionStore::new(&config.data_dir, config.session_ttl as f64, clock.clone());
        let csrf = CsrfOriginPolicy::new(
            &config.csrf_trusted_origins,
            &config.host,
            config.port,
            config.bind_is_loopback,
            config.trusted_proxies.clone(),
        );
        Arc::new(Self {
            users,
            sessions,
            limiter: RateLimiter::new(clock.clone()),
            csrf,
            clock,
            invite_gate: std::sync::OnceLock::new(),
            audit_sink: std::sync::OnceLock::new(),
            config,
        })
    }

    pub fn set_audit_sink(&self, sink: Arc<dyn crate::enterprise::AuthAuditSink>) -> bool {
        self.audit_sink.set(sink).is_ok()
    }

    pub fn record_audit(&self, event: &str, fields: Value) {
        if let Some(sink) = self.audit_sink.get() {
            sink.record(event, &fields);
        }
    }

    pub fn verify_tenant_access(
        &self,
        identity: &Identity,
        requested_tenant: Option<&str>,
    ) -> Result<Option<String>, Response> {
        let users = self.users.load();
        let record = if identity.email.is_empty() {
            None
        } else {
            users.get(&identity.email)
        };
        match crate::enterprise::verify_tenant_boundary(&identity.email, record, requested_tenant) {
            Ok(tenant) => Ok(tenant),
            Err(err) => {
                self.record_audit(
                    "auth.tenant.denied",
                    json!({
                        "email": identity.email,
                        "requested_tenant": requested_tenant,
                        "reason": err.to_string(),
                    }),
                );
                Err(crate::enterprise::tenant_denied_response(&err))
            }
        }
    }

    pub fn tenant_for_identity(&self, identity: &Identity) -> Option<String> {
        if identity.email.is_empty() {
            return None;
        }
        let users = self.users.load();
        users
            .get(&identity.email)
            .and_then(crate::enterprise::tenant_of_user)
    }

    pub fn set_invite_gate(&self, gate: InviteGate) -> bool {
        self.invite_gate.set(gate).is_ok()
    }

    pub fn config(&self) -> &AuthConfig {
        &self.config
    }

    pub fn clock(&self) -> &Clock {
        &self.clock
    }

    pub fn sessions(&self) -> &SessionStore {
        &self.sessions
    }

    pub fn users(&self) -> &UserStore {
        &self.users
    }

    pub fn limiter(&self) -> &RateLimiter {
        &self.limiter
    }

    pub fn csrf_policy(&self) -> &CsrfOriginPolicy {
        &self.csrf
    }

    pub fn invite_authorized(&self, headers: &HeaderMap) -> bool {
        match self.invite_gate.get() {
            Some(gate) => gate(headers),
            None => false,
        }
    }

    pub fn trusted_local_owner(&self) -> bool {
        !self.config.require_auth && !self.config.externally_reachable
    }

    pub fn effective_require_auth(&self) -> bool {
        self.config.require_auth || self.config.externally_reachable
    }

    pub fn graph_viewer(&self, email: &str) -> Option<String> {
        let email = email.trim();
        (self.effective_require_auth() && !email.is_empty()).then(|| email.to_string())
    }

    pub fn extract_bearer_token(&self, headers: &HeaderMap) -> Option<String> {
        let authorization = headers
            .get("authorization")
            .and_then(|value| value.to_str().ok())
            .unwrap_or("");
        if let Some(rest) = authorization.strip_prefix("Bearer ") {
            return Some(rest.trim().to_string());
        }
        let cookie = headers.get("cookie").and_then(|value| value.to_str().ok());
        cookie_value(cookie, SESSION_COOKIE_NAME)
    }

    pub fn get_user_role(&self, email: &str, users: &Users) -> String {
        if self.trusted_local_owner() && email.is_empty() {
            return "owner".into();
        }
        let normalized = normalize_email(email);
        let record = users
            .get(&normalized)
            .or_else(|| users.get(email))
            .or_else(|| users.by_id(email).map(|(_, record)| record));
        if let Some(role) = record
            .and_then(|record| record.get("role"))
            .and_then(Value::as_str)
            .filter(|role| !role.is_empty())
        {
            return normalize_role(role).to_string();
        }
        if self
            .config
            .admin_emails
            .iter()
            .any(|allowed| normalize_email(allowed) == normalized)
        {
            return "admin".into();
        }
        if users.first_email() == Some(normalized.as_str()) {
            "admin".into()
        } else {
            "user".into()
        }
    }

    pub fn active_session_email(&self, identity: Option<&str>, users: &Users) -> Option<String> {
        let raw = identity.filter(|value| !value.is_empty())?;
        let normalized = normalize_email(raw);
        let matched = if users.get(&normalized).is_some() {
            Some(normalized.clone())
        } else if users.get(raw).is_some() {
            Some(raw.to_string())
        } else {
            users.by_id(raw).map(|(email, _)| email.to_string())
        }
        .filter(|key| !key.is_empty())?;
        let record = users.get(&matched)?;
        let disabled = record
            .get("disabled")
            .map(|value| value.as_bool().unwrap_or(!value.is_null()))
            .unwrap_or(false);
        if disabled {
            return None;
        }
        Some(normalize_email(&matched))
    }

    pub fn get_current_user(&self, headers: &HeaderMap) -> Option<String> {
        let token = self.extract_bearer_token(headers)?;
        let identity = self.sessions.get_email(&token)?;
        self.active_session_email(Some(&identity), &self.users.load())
    }

    pub fn require_user(&self, headers: &HeaderMap) -> Result<Identity, Response> {
        let users = self.users.load();
        if let Some(email) = self
            .extract_bearer_token(headers)
            .and_then(|token| self.sessions.get_email(&token))
            .and_then(|identity| self.active_session_email(Some(&identity), &users))
        {
            let role = self.get_user_role(&email, &users);
            return Ok(Identity { email, role });
        }
        if self.trusted_local_owner() {
            return Ok(Identity::local_owner());
        }
        Err(detail_error(
            StatusCode::UNAUTHORIZED,
            LOGIN_REQUIRED_LITERAL,
        ))
    }

    pub fn require_admin(&self, headers: &HeaderMap) -> Result<Identity, Response> {
        let users = self.users.load();
        if self.trusted_local_owner() {
            return Ok(Identity::local_owner());
        }
        if let Some(email) = self
            .extract_bearer_token(headers)
            .and_then(|token| self.sessions.get_email(&token))
            .and_then(|identity| self.active_session_email(Some(&identity), &users))
        {
            let role = self.get_user_role(&email, &users);
            if check_role(&role, "admin:users").is_ok() {
                return Ok(Identity { email, role });
            }
        }
        Err(detail_error(StatusCode::FORBIDDEN, ADMIN_REQUIRED_LITERAL))
    }

    pub fn public_user(
        &self,
        email: &str,
        record: &Map<String, Value>,
        users: &Users,
    ) -> OrderedMap {
        let role = self.get_user_role(email, users);
        let user_id = record
            .get("id")
            .and_then(Value::as_str)
            .filter(|id| !id.is_empty())
            .map(str::to_string)
            .or_else(|| users.user_id_for_email(Some(email)));
        let mut view = OrderedMap::new();
        view.insert("id", json!(user_id));
        view.insert("email", json!(email));
        view.insert("identity", json!(user_id));
        view.insert(
            "name",
            json!(record.get("name").and_then(Value::as_str).unwrap_or("")),
        );
        view.insert(
            "nickname",
            json!(record.get("nickname").and_then(Value::as_str).unwrap_or("")),
        );
        view.insert("role", json!(role));
        view.insert(
            "disabled",
            json!(record
                .get("disabled")
                .map(|value| value.as_bool().unwrap_or(!value.is_null()))
                .unwrap_or(false)),
        );
        view
    }

    pub fn enforce_rate_limit(&self, email: &str, bucket_key: &str) -> Result<(), Response> {
        self.limiter
            .enforce(email, bucket_key, self.config.rate_limit_enabled)
    }

    pub fn client_ip(&self, headers: &HeaderMap, peer: Option<&str>) -> String {
        let peer = peer.unwrap_or("");
        if self.peer_is_trusted_proxy(peer) {
            for name in ["cf-connecting-ip", "x-forwarded-for"] {
                let Some(raw) = headers.get(name).and_then(|value| value.to_str().ok()) else {
                    continue;
                };
                let candidate = raw.split(',').next().unwrap_or("").trim();
                if candidate.parse::<IpAddr>().is_ok() {
                    return candidate.to_string();
                }
            }
        }
        if peer.is_empty() {
            "unknown".to_string()
        } else {
            peer.to_string()
        }
    }

    fn peer_is_trusted_proxy(&self, peer: &str) -> bool {
        if peer.is_empty() || self.config.trusted_proxies.is_empty() {
            return false;
        }
        match peer.parse::<IpAddr>() {
            Ok(address) => self
                .config
                .trusted_proxies
                .iter()
                .any(|network| network.contains(&address)),
            Err(_) => false,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::AuthConfig;
    use std::collections::HashMap;

    fn build(pairs: &[(&str, &str)], dir: &std::path::Path) -> Arc<AuthState> {
        let env: HashMap<String, String> = pairs
            .iter()
            .map(|(key, value)| ((*key).to_string(), (*value).to_string()))
            .collect();
        let mut config = AuthConfig::from_map(&env, None);
        config.data_dir = dir.to_path_buf();
        AuthState::with_clock(config, Clock::frozen(1_000.0))
    }

    fn seed(state: &AuthState, records: &[(&str, &str, bool)]) {
        let mut users = Users::new();
        for (email, role, disabled) in records {
            let mut record = Map::new();
            record.insert("role".into(), json!(role));
            record.insert("disabled".into(), json!(disabled));
            record.insert("name".into(), json!("N"));
            record.insert("nickname".into(), json!("n"));
            users.insert(*email, record);
        }
        state.users().save(&users);
    }

    fn headers(pairs: &[(&str, &str)]) -> HeaderMap {
        let mut map = HeaderMap::new();
        for (name, value) in pairs {
            map.insert(
                axum::http::HeaderName::from_bytes(name.as_bytes()).unwrap(),
                value.parse().unwrap(),
            );
        }
        map
    }

    #[test]
    fn the_local_owner_is_an_empty_identity_with_the_owner_role() {
        let dir = tempfile::tempdir().unwrap();
        let state = build(&[], dir.path());
        assert!(state.trusted_local_owner());
        let identity = state.require_user(&HeaderMap::new()).unwrap();
        assert_eq!(identity, Identity::local_owner());
        assert!(identity.is_local_owner());
        assert!(identity.can("admin:users"));
        assert!(state.require_admin(&HeaderMap::new()).is_ok());
        assert!(format!("{state:?}").contains("trusted_local_owner: true"));
    }

    #[test]
    fn the_graph_viewer_exists_only_for_a_signed_in_user_in_multi_user_mode() {
        let dir = tempfile::tempdir().unwrap();
        let single = build(&[], dir.path());
        assert_eq!(single.graph_viewer("a@b.com"), None);
        let multi = build(&[("LATTICEAI_REQUIRE_AUTH", "1")], dir.path());
        assert_eq!(multi.graph_viewer(" a@b.com "), Some("a@b.com".to_string()));
        assert_eq!(multi.graph_viewer("  "), None);
    }

    #[test]
    fn require_auth_refuses_an_anonymous_caller() {
        let dir = tempfile::tempdir().unwrap();
        let state = build(&[("LATTICEAI_REQUIRE_AUTH", "1")], dir.path());
        assert!(!state.trusted_local_owner());
        assert!(state.effective_require_auth());
        let refusal = state.require_user(&HeaderMap::new()).unwrap_err();
        assert_eq!(refusal.status(), StatusCode::UNAUTHORIZED);
        let refusal = state.require_admin(&HeaderMap::new()).unwrap_err();
        assert_eq!(refusal.status(), StatusCode::FORBIDDEN);
    }

    #[test]
    fn a_session_resolves_through_cookie_or_bearer() {
        let dir = tempfile::tempdir().unwrap();
        let state = build(&[("LATTICEAI_REQUIRE_AUTH", "1")], dir.path());
        seed(&state, &[("a@b.com", "admin", false)]);
        let token = state.sessions().create("user:one", Some("a@b.com"));

        let by_cookie = headers(&[("cookie", &format!("session_token={token}"))]);
        assert_eq!(state.require_user(&by_cookie).unwrap().email, "a@b.com");
        let by_bearer = headers(&[("authorization", &format!("Bearer {token}"))]);
        let identity = state.require_admin(&by_bearer).unwrap();
        assert_eq!(identity.role, "admin");
        assert_eq!(
            state.get_current_user(&by_bearer).as_deref(),
            Some("a@b.com")
        );
    }

    #[test]
    fn a_disabled_account_loses_its_live_session() {
        let dir = tempfile::tempdir().unwrap();
        let state = build(&[("LATTICEAI_REQUIRE_AUTH", "1")], dir.path());
        seed(&state, &[("a@b.com", "admin", true)]);
        let token = state.sessions().create("user:one", Some("a@b.com"));
        let carrying = headers(&[("cookie", &format!("session_token={token}"))]);
        assert!(state.require_user(&carrying).is_err());
        assert_eq!(state.get_current_user(&carrying), None);
    }

    #[test]
    fn a_non_admin_session_is_refused_by_require_admin() {
        let dir = tempfile::tempdir().unwrap();
        let state = build(&[("LATTICEAI_REQUIRE_AUTH", "1")], dir.path());
        seed(
            &state,
            &[("a@b.com", "admin", false), ("c@d.com", "user", false)],
        );
        let token = state.sessions().create("user:two", Some("c@d.com"));
        let carrying = headers(&[("cookie", &format!("session_token={token}"))]);
        assert_eq!(state.require_user(&carrying).unwrap().role, "user");
        assert_eq!(
            state.require_admin(&carrying).unwrap_err().status(),
            StatusCode::FORBIDDEN
        );
    }

    #[test]
    fn roles_fall_back_to_the_allowlist_then_to_the_first_account() {
        let dir = tempfile::tempdir().unwrap();
        let state = build(&[("LATTICEAI_ADMIN_EMAILS", "boss@x.y")], dir.path());
        let mut users = Users::new();
        users.insert("first@x.y", Map::new());
        users.insert("boss@x.y", Map::new());
        users.insert("other@x.y", Map::new());
        state.users().save(&users);
        let users = state.users().load();
        assert_eq!(state.get_user_role("first@x.y", &users), "admin");
        assert_eq!(state.get_user_role("boss@x.y", &users), "admin");
        assert_eq!(state.get_user_role("other@x.y", &users), "user");
        let mut users2 = users.clone();
        let mut record = Map::new();
        record.insert("role".into(), json!("VIEWER"));
        users2.insert("boss@x.y", record);
        assert_eq!(state.get_user_role("boss@x.y", &users2), "viewer");
        assert_eq!(state.get_user_role("", &users), "owner");
    }

    #[test]
    fn a_session_keyed_on_the_stable_id_still_resolves() {
        let dir = tempfile::tempdir().unwrap();
        let state = build(&[("LATTICEAI_REQUIRE_AUTH", "1")], dir.path());
        seed(&state, &[("a@b.com", "admin", false)]);
        let users = state.users().load();
        let identity = users.get("a@b.com").unwrap()["id"]
            .as_str()
            .unwrap()
            .to_string();
        assert_eq!(
            state
                .active_session_email(Some(&identity), &users)
                .as_deref(),
            Some("a@b.com")
        );
        assert_eq!(state.active_session_email(Some(""), &users), None);
        assert_eq!(state.active_session_email(None, &users), None);
        assert_eq!(state.active_session_email(Some("ghost@x.y"), &users), None);
    }

    #[test]
    fn the_bearer_header_beats_the_cookie() {
        let dir = tempfile::tempdir().unwrap();
        let state = build(&[], dir.path());
        let map = headers(&[
            ("authorization", "Bearer  from-header  "),
            ("cookie", "session_token=from-cookie"),
        ]);
        assert_eq!(
            state.extract_bearer_token(&map).as_deref(),
            Some("from-header")
        );
        let map = headers(&[
            ("authorization", "Basic nope"),
            ("cookie", "session_token=c"),
        ]);
        assert_eq!(state.extract_bearer_token(&map).as_deref(), Some("c"));
        assert_eq!(state.extract_bearer_token(&HeaderMap::new()), None);
    }

    #[test]
    fn client_ip_ignores_forwarded_headers_without_an_allowlist() {
        let dir = tempfile::tempdir().unwrap();
        let state = build(&[], dir.path());
        let map = headers(&[("x-forwarded-for", "9.9.9.9")]);
        assert_eq!(state.client_ip(&map, Some("127.0.0.1")), "127.0.0.1");
        assert_eq!(state.client_ip(&map, None), "unknown");

        let state = build(&[("LATTICEAI_TRUSTED_PROXIES", "10.0.0.0/8")], dir.path());
        assert_eq!(state.client_ip(&map, Some("10.1.2.3")), "9.9.9.9");
        let cf = headers(&[
            ("cf-connecting-ip", "8.8.8.8"),
            ("x-forwarded-for", "9.9.9.9"),
        ]);
        assert_eq!(state.client_ip(&cf, Some("10.1.2.3")), "8.8.8.8");
        let junk = headers(&[("x-forwarded-for", "not-an-ip")]);
        assert_eq!(state.client_ip(&junk, Some("10.1.2.3")), "10.1.2.3");
        assert_eq!(state.client_ip(&map, Some("192.168.0.1")), "192.168.0.1");
    }

    #[test]
    fn the_public_user_view_carries_the_stable_id() {
        let dir = tempfile::tempdir().unwrap();
        let state = build(&[], dir.path());
        seed(&state, &[("a@b.com", "user", false)]);
        let users = state.users().load();
        let record = users.get("a@b.com").unwrap();
        let view = state.public_user("a@b.com", record, &users);
        assert_eq!(view.get("email"), Some(&json!("a@b.com")));
        assert_eq!(view.get("identity"), view.get("id"));
        assert_eq!(view.get("disabled"), Some(&json!(false)));
        assert_eq!(view.get("role"), Some(&json!("user")));
        let keys: Vec<&str> = view.iter().map(|(key, _)| key).collect();
        assert_eq!(
            keys,
            vec!["id", "email", "identity", "name", "nickname", "role", "disabled"]
        );
    }

    #[test]
    fn the_invite_gate_defaults_to_refusing() {
        let dir = tempfile::tempdir().unwrap();
        let state = build(&[], dir.path());
        assert!(!state.invite_authorized(&HeaderMap::new()));
        assert!(state.set_invite_gate(Arc::new(|headers: &HeaderMap| {
            headers.contains_key("x-invite")
        })));
        assert!(state.invite_authorized(&headers(&[("x-invite", "1")])));
        assert!(!state.invite_authorized(&HeaderMap::new()));
        assert!(
            !state.set_invite_gate(Arc::new(|_: &HeaderMap| true)),
            "a second gate must not replace the first"
        );
    }

    #[test]
    fn the_rate_limit_switch_is_honoured() {
        let dir = tempfile::tempdir().unwrap();
        let state = build(&[("LATTICEAI_RATE_LIMIT", "0")], dir.path());
        for _ in 0..100 {
            assert!(state.enforce_rate_limit("a@b.com", "agent").is_ok());
        }
        let state = build(&[], dir.path());
        for _ in 0..10 {
            assert!(state.enforce_rate_limit("a@b.com", "agent").is_ok());
        }
        assert!(state.enforce_rate_limit("a@b.com", "agent").is_err());
    }

    #[test]
    fn the_peer_comes_from_the_extension_or_the_socket() {
        let request = axum::http::Request::builder().body(()).unwrap();
        let (mut parts, ()) = request.into_parts();
        assert_eq!(peer_of(&parts), None);
        parts
            .extensions
            .insert(ConnectInfo(SocketAddr::from(([127, 0, 0, 1], 4825))));
        assert_eq!(peer_of(&parts).as_deref(), Some("127.0.0.1"));
        parts.extensions.insert(PeerAddr("9.9.9.9".into()));
        assert_eq!(peer_of(&parts).as_deref(), Some("9.9.9.9"));
    }
}
