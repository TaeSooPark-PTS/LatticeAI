use std::sync::Arc;

use axum::body::Bytes;
use axum::extract::{Request, State};
use axum::http::{header, HeaderMap, HeaderValue, StatusCode};
use axum::response::Response;
use axum::routing::{get, post};
use axum::Router;
use serde_json::{json, Map, Value};

use crate::body::{optional, parse_model, required, Field};
use crate::messages::{detail_error, http_error, resolve_language};
use crate::password::{hash_password, stored_is_hashed, verify_password};
use crate::pyjson::OrderedMap;
use crate::ratelimit::{LOGIN_LIMIT, REGISTER_LIMIT};
use crate::response::json_response;
use crate::setcookie::{delete_session_cookie, session_cookie};
use crate::state::{peer_of, AuthState};
use crate::users::{ensure_user_identity, normalize_email, Users};

pub const AUTH_PATHS: [&str; 6] = [
    "/register",
    "/login",
    "/logout",
    "/account/change-password",
    "/account/profile",
    "/auth/sso/config",
];

const REGISTER_FIELDS: &[Field] = &[
    required("email"),
    required("password"),
    required("name"),
    required("nickname"),
];
const LOGIN_FIELDS: &[Field] = &[required("email"), required("password")];
const CHANGE_PASSWORD_FIELDS: &[Field] = &[required("current_password"), required("new_password")];
const PROFILE_FIELDS: &[Field] = &[optional("name"), optional("nickname")];

pub fn router(state: Arc<AuthState>) -> Router {
    Router::new()
        .route("/register", post(register))
        .route("/login", post(login))
        .route("/logout", post(logout))
        .route("/account/change-password", post(change_password))
        .route("/account/profile", get(get_profile).patch(update_profile))
        .route("/auth/sso/config", get(sso_config))
        .with_state(state)
}

pub fn router_with_csrf(state: Arc<AuthState>) -> Router {
    router(Arc::clone(&state)).layer(axum::middleware::from_fn_with_state(
        state,
        crate::middleware::csrf_guard,
    ))
}

fn ok(entries: Vec<(&str, Value)>) -> Response {
    let mut map = OrderedMap::new();
    for (key, value) in entries {
        map.insert(key, value);
    }
    let body = serde_json::to_string(&map).unwrap_or_else(|_| "{}".into());
    json_response(StatusCode::OK, &body, None)
}

async fn split(request: Request) -> (HeaderMap, Option<String>, Bytes) {
    let (parts, body) = request.into_parts();
    let peer = peer_of(&parts);
    let bytes = axum::body::to_bytes(body, 1024 * 1024)
        .await
        .unwrap_or_default();
    (parts.headers, peer, bytes)
}

async fn register(State(state): State<Arc<AuthState>>, request: Request) -> Response {
    let (headers, peer, bytes) = split(request).await;
    let language = resolve_language(&headers);
    let body = match parse_model(&bytes, REGISTER_FIELDS) {
        Ok(body) => body,
        Err(refusal) => return refusal,
    };
    let ip = state.client_ip(&headers, peer.as_deref());
    if let Err(refusal) =
        state
            .limiter()
            .check_ip(&ip, "register", REGISTER_LIMIT.0, REGISTER_LIMIT.1)
    {
        return refusal;
    }

    let config = state.config();
    let invite_claim = config.invite_gate_enabled && state.invite_authorized(&headers);
    if config.invite_gate_enabled && !invite_claim {
        return http_error(StatusCode::FORBIDDEN, "auth.invitation_required", language);
    }
    if !config.open_registration && !invite_claim {
        return http_error(
            StatusCode::FORBIDDEN,
            "auth.registration_disabled",
            language,
        );
    }
    if let Err(refusal) = enforce_password_policy(body.str("password"), language) {
        return refusal;
    }

    let email = normalize_email(body.str("email"));
    let mut users = state.users().load();
    if users.contains(&email) {
        return http_error(StatusCode::BAD_REQUEST, "auth.email_taken", language);
    }
    let role = if users.is_empty() { "admin" } else { "user" };
    let password_hash = match hash_password(body.str("password")) {
        Ok(hash) => hash,
        Err(err) => {
            return detail_error(
                StatusCode::INTERNAL_SERVER_ERROR,
                &format!("Failed to secure password: {err}"),
            );
        }
    };
    let mut record = Map::new();
    record.insert("password".into(), json!(password_hash));
    record.insert("name".into(), json!(body.str("name")));
    record.insert("nickname".into(), json!(body.str("nickname")));
    record.insert("role".into(), json!(role));
    record.insert("disabled".into(), json!(false));
    ensure_user_identity(&email, &mut record);
    users.insert(email, record);
    state.users().save(&users);

    let message = if role == "admin" {
        "회원가입 성공! 첫 번째 사용자로 관리자 권한이 부여되었습니다."
    } else {
        "회원가입 성공!"
    };
    ok(vec![
        ("status", json!("ok")),
        ("message", json!(message)),
        ("role", json!(role)),
    ])
}

async fn login(State(state): State<Arc<AuthState>>, request: Request) -> Response {
    let (headers, peer, bytes) = split(request).await;
    let language = resolve_language(&headers);
    let body = match parse_model(&bytes, LOGIN_FIELDS) {
        Ok(body) => body,
        Err(refusal) => return refusal,
    };
    let ip = state.client_ip(&headers, peer.as_deref());
    if let Err(refusal) = state
        .limiter()
        .check_ip(&ip, "login", LOGIN_LIMIT.0, LOGIN_LIMIT.1)
    {
        return refusal;
    }

    let email = normalize_email(body.str("email"));
    let mut users = state.users().load();
    let stored = account(&users, &email)
        .map(|record| text_of(record, "password").to_string())
        .unwrap_or_default();
    let known = account(&users, &email).is_some();
    if !known || !verify_and_migrate(&state, &email, body.str("password"), &stored, &mut users) {
        state.record_audit("auth.login.failed", json!({"email": email, "ip": ip}));
        return http_error(StatusCode::UNAUTHORIZED, "auth.bad_credentials", language);
    }
    let record = match account(&users, &email) {
        Some(record) => record.clone(),
        None => return http_error(StatusCode::UNAUTHORIZED, "auth.bad_credentials", language),
    };
    if truthy(record.get("disabled")) {
        state.record_audit(
            "auth.login.disabled_denied",
            json!({"email": email, "ip": ip}),
        );
        return http_error(StatusCode::FORBIDDEN, "auth.account_disabled", language);
    }

    let role = state.get_user_role(&email, &users);
    let subject = users
        .user_id_for_email(Some(&email))
        .unwrap_or_else(|| email.clone());
    let token = state.sessions().create(&subject, Some(&email));
    state.record_audit(
        "auth.login.success",
        json!({"email": email, "role": role, "ip": ip}),
    );
    let body = ok(vec![
        ("status", json!("ok")),
        ("nickname", json!(text_of(&record, "nickname"))),
        ("name", json!(text_of(&record, "name"))),
        ("email", json!(email)),
        ("role", json!(role)),
        ("is_admin", json!(role == "admin")),
    ]);
    with_cookie(
        body,
        &session_cookie(
            &token,
            state.config().session_ttl,
            state.config().secure_cookies,
            state.clock(),
        ),
    )
}

async fn logout(State(state): State<Arc<AuthState>>, request: Request) -> Response {
    let (headers, _, _) = split(request).await;
    if let Some(token) = state.extract_bearer_token(&headers) {
        state.sessions().invalidate(&token);
    }
    with_cookie(
        ok(vec![("status", json!("ok"))]),
        &delete_session_cookie(state.config().secure_cookies, state.clock()),
    )
}

async fn change_password(State(state): State<Arc<AuthState>>, request: Request) -> Response {
    let (headers, _, bytes) = split(request).await;
    let language = resolve_language(&headers);
    let body = match parse_model(&bytes, CHANGE_PASSWORD_FIELDS) {
        Ok(body) => body,
        Err(refusal) => return refusal,
    };
    let identity = match state.require_user(&headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    let email = normalize_email(&identity.email);
    if email.is_empty() {
        return http_error(StatusCode::UNAUTHORIZED, "auth.login_required", language);
    }
    if let Err(refusal) = enforce_password_policy(body.str("new_password"), language) {
        return refusal;
    }
    let mut users = state.users().load();
    let Some(stored) =
        account(&users, &email).map(|record| text_of(record, "password").to_string())
    else {
        return http_error(StatusCode::NOT_FOUND, "auth.user_not_found", language);
    };
    if !verify_and_migrate(
        &state,
        &email,
        body.str("current_password"),
        &stored,
        &mut users,
    ) {
        return http_error(
            StatusCode::UNAUTHORIZED,
            "auth.current_password_wrong",
            language,
        );
    }
    let new_hash = match hash_password(body.str("new_password")) {
        Ok(hash) => hash,
        Err(err) => {
            return detail_error(
                StatusCode::INTERNAL_SERVER_ERROR,
                &format!("Failed to secure password: {err}"),
            );
        }
    };
    let mut record = users.get(&email).cloned().unwrap_or_default();
    record.insert("password".into(), json!(new_hash));
    users.insert(email, record);
    state.users().save(&users);
    ok(vec![
        ("status", json!("ok")),
        ("message", json!("비밀번호가 변경되었습니다.")),
    ])
}

async fn update_profile(State(state): State<Arc<AuthState>>, request: Request) -> Response {
    let (headers, _, bytes) = split(request).await;
    let language = resolve_language(&headers);
    let body = match parse_model(&bytes, PROFILE_FIELDS) {
        Ok(body) => body,
        Err(refusal) => return refusal,
    };
    let identity = match state.require_user(&headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    let email = normalize_email(&identity.email);
    if email.is_empty() {
        return http_error(StatusCode::UNAUTHORIZED, "auth.login_required", language);
    }
    if let Some(name) = body.opt("name") {
        if name.trim().is_empty() {
            return http_error(StatusCode::BAD_REQUEST, "auth.name_required", language);
        }
    }
    if let Some(nickname) = body.opt("nickname") {
        if nickname.trim().is_empty() {
            return http_error(StatusCode::BAD_REQUEST, "auth.nickname_required", language);
        }
    }
    let mut users = state.users().load();
    let Some(mut record) = account(&users, &email).cloned() else {
        return http_error(StatusCode::NOT_FOUND, "auth.user_not_found", language);
    };
    if let Some(name) = body.opt("name") {
        record.insert("name".into(), json!(name.trim()));
    }
    if let Some(nickname) = body.opt("nickname") {
        record.insert("nickname".into(), json!(nickname.trim()));
    }
    users.insert(email, record.clone());
    state.users().save(&users);
    ok(vec![
        ("status", json!("ok")),
        ("name", json!(text_of(&record, "name"))),
        ("nickname", json!(text_of(&record, "nickname"))),
    ])
}

async fn get_profile(State(state): State<Arc<AuthState>>, request: Request) -> Response {
    let (headers, _, _) = split(request).await;
    let language = resolve_language(&headers);
    let identity = match state.require_user(&headers) {
        Ok(identity) => identity,
        Err(refusal) => return refusal,
    };
    let email = normalize_email(&identity.email);
    if email.is_empty() {
        if state.config().require_auth {
            return http_error(StatusCode::UNAUTHORIZED, "auth.login_required", language);
        }
        return ok(vec![
            ("email", json!("")),
            ("name", json!("Local User")),
            ("nickname", json!("You")),
            ("role", json!("admin")),
            ("is_admin", json!(true)),
        ]);
    }
    let users = state.users().load();
    let Some(record) = account(&users, &email) else {
        return http_error(StatusCode::NOT_FOUND, "auth.user_not_found", language);
    };
    let role = state.get_user_role(&email, &users);
    ok(vec![
        ("email", json!(email)),
        ("name", json!(text_of(record, "name"))),
        ("nickname", json!(text_of(record, "nickname"))),
        ("role", json!(role)),
        ("is_admin", json!(role == "admin")),
    ])
}

async fn sso_config(State(state): State<Arc<AuthState>>) -> Response {
    let sso = state.config().clone().with_stored_sso().sso;
    ok(vec![
        ("enabled", json!(sso.enabled)),
        ("provider_name", json!(sso.provider_name)),
        ("discovery_url", json!(sso.discovery_url)),
        ("client_id", json!(sso.client_id)),
        ("redirect_uri", json!(sso.redirect_uri)),
        ("scopes", json!(sso.scopes)),
        ("secret_configured", json!(!sso.client_secret.is_empty())),
    ])
}

fn enforce_password_policy(password: &str, language: &str) -> Result<(), Response> {
    let long_enough = password.chars().count() >= 8;
    let has_letter = password.chars().any(char::is_alphabetic);
    let has_digit = password.chars().any(|c| c.is_numeric());
    if long_enough && has_letter && has_digit {
        Ok(())
    } else {
        Err(http_error(
            StatusCode::BAD_REQUEST,
            "auth.password_too_weak",
            language,
        ))
    }
}

fn verify_and_migrate(
    state: &AuthState,
    email: &str,
    plain: &str,
    stored: &str,
    users: &mut Users,
) -> bool {
    if stored_is_hashed(stored) {
        return verify_password(plain, stored);
    }
    if plain == stored {
        if let Some(mut record) = users.get(email).cloned() {
            if let Ok(hashed) = hash_password(plain) {
                record.insert("password".into(), json!(hashed));
                users.insert(email, record);
                state.users().save(users);
                state.record_audit(
                    "auth.password.migrated_from_plaintext",
                    json!({"email": email}),
                );
            }
        }
        return true;
    }
    false
}

fn text_of<'a>(record: &'a Map<String, Value>, key: &str) -> &'a str {
    record.get(key).and_then(Value::as_str).unwrap_or("")
}

fn account<'a>(users: &'a Users, email: &str) -> Option<&'a Map<String, Value>> {
    users.get(email).filter(|record| !record.is_empty())
}

fn truthy(value: Option<&Value>) -> bool {
    match value {
        None | Some(Value::Null) => false,
        Some(Value::Bool(flag)) => *flag,
        Some(Value::String(text)) => !text.is_empty(),
        Some(Value::Number(number)) => number.as_f64() != Some(0.0),
        Some(Value::Array(items)) => !items.is_empty(),
        Some(Value::Object(map)) => !map.is_empty(),
    }
}

fn with_cookie(mut response: Response, cookie: &str) -> Response {
    if let Ok(value) = HeaderValue::from_str(cookie) {
        response.headers_mut().append(header::SET_COOKIE, value);
    }
    response
}

pub fn login_required() -> Response {
    detail_error(
        StatusCode::UNAUTHORIZED,
        crate::messages::LOGIN_REQUIRED_LITERAL,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_password_policy_is_length_letters_and_digits() {
        assert!(enforce_password_policy("abcd1234", "ko").is_ok());
        for weak in ["short1", "abcdefgh", "12345678", ""] {
            assert!(enforce_password_policy(weak, "ko").is_err(), "{weak}");
        }
        assert!(enforce_password_policy("가나다라마바1２", "ko").is_ok());
    }

    #[test]
    fn every_declared_path_is_unique() {
        let mut sorted = AUTH_PATHS.to_vec();
        sorted.sort_unstable();
        sorted.dedup();
        assert_eq!(sorted.len(), AUTH_PATHS.len());
    }

    #[test]
    fn python_truthiness_decides_disabled() {
        assert!(!truthy(None));
        assert!(!truthy(Some(&json!(false))));
        assert!(!truthy(Some(&json!(null))));
        assert!(!truthy(Some(&json!(""))));
        assert!(!truthy(Some(&json!(0))));
        assert!(truthy(Some(&json!(true))));
        assert!(truthy(Some(&json!("yes"))));
        assert!(truthy(Some(&json!(1))));
        assert!(truthy(Some(&json!(["x"]))));
        assert!(!truthy(Some(&json!([]))));
        assert!(truthy(Some(&json!({"a": 1}))));
    }

    #[test]
    fn the_login_required_body_is_the_guard_body() {
        assert_eq!(login_required().status(), StatusCode::UNAUTHORIZED);
    }
}
