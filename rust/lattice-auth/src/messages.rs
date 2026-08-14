//! The ko/en strings this crate answers with, and how a request picks one.
//!
//! Port of the `auth.*` slice of `latticeai/core/messages.py` plus the three
//! refusals that live as literals in the Python source rather than in the
//! catalog (`access_runtime`'s two and `core/security`'s rate-limit text).
//!
//! TODO(WP-I3): `lattice_core::messages` becomes the one catalog for the whole
//! Rust side. When it lands, delete `CATALOG` and route `translate` through it;
//! the ids below are already the Python ids, so nothing outside this file has
//! to change. Until then the wording is duplicated deliberately — an id that
//! resolves to nothing is worse than an id that resolves twice.

use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
use serde_json::json;

use crate::response::json_response;

/// The language a message falls back to, matching `DEFAULT_LANGUAGE`.
pub const DEFAULT_LANGUAGE: &str = "ko";
/// The languages the catalog is complete in.
pub const SUPPORTED_LANGUAGES: [&str; 2] = ["ko", "en"];
/// The explicit override the frontend sends on every request.
pub const LANGUAGE_HEADER: &str = "x-lattice-language";

/// `(id, ko, en)` for every message this crate can answer with.
const CATALOG: &[(&str, &str, &str)] = &[
    (
        "auth.password_too_weak",
        "비밀번호는 8자 이상이며 영문자와 숫자를 모두 포함해야 합니다.",
        "Your password needs at least 8 characters, including letters and numbers.",
    ),
    (
        "auth.invitation_required",
        "유효한 서명 초대 권한이 필요합니다.",
        "A valid signed invitation is required.",
    ),
    (
        "auth.registration_disabled",
        "회원가입이 비활성화되어 있습니다. 관리자에게 문의하세요.",
        "Sign-up is turned off. Ask an administrator to enable it.",
    ),
    (
        "auth.email_taken",
        "이미 존재하는 이메일입니다.",
        "That email address is already registered.",
    ),
    (
        "auth.bad_credentials",
        "이메일 또는 비밀번호가 틀렸습니다.",
        "That email address or password is not correct.",
    ),
    (
        "auth.account_disabled",
        "비활성화된 계정입니다.",
        "This account has been disabled.",
    ),
    (
        "auth.login_required",
        "인증이 필요합니다.",
        "You need to sign in first.",
    ),
    (
        "auth.user_not_found",
        "사용자를 찾을 수 없습니다.",
        "No such user.",
    ),
    (
        "auth.current_password_wrong",
        "현재 비밀번호가 틀렸습니다.",
        "Your current password is not correct.",
    ),
    (
        "auth.name_required",
        "이름을 입력해주세요.",
        "Please enter a name.",
    ),
    (
        "auth.nickname_required",
        "닉네임을 입력해주세요.",
        "Please enter a nickname.",
    ),
];

/// `require_user`'s refusal. A literal in `access_runtime.py`, Korean in both
/// languages there, so it is Korean in both languages here.
pub const LOGIN_REQUIRED_LITERAL: &str = "인증이 필요합니다.";
/// `require_admin`'s refusal — likewise a literal, likewise unlocalized.
pub const ADMIN_REQUIRED_LITERAL: &str = "관리자 권한이 필요합니다.";
/// `check_ip_rate_limit`'s 429 detail (`core/security.py`).
pub const IP_RATE_LIMITED_LITERAL: &str = "요청이 너무 많습니다. 잠시 후 다시 시도하세요.";
/// `requested_workspace`'s 403 detail (`api/workspace_scope.py`).
pub const WORKSPACE_MISMATCH_LITERAL: &str = "Workspace selectors must match.";

/// Localized text for `key`; an unknown id returns the id, as Python does.
pub fn translate(key: &str, language: &str) -> String {
    match CATALOG.iter().find(|(id, _, _)| *id == key) {
        Some((_, ko, en)) => {
            if language == "en" {
                (*en).to_string()
            } else {
                (*ko).to_string()
            }
        }
        None => key.to_string(),
    }
}

/// The language for this request: the product's choice, then the browser's.
///
/// Never fails and never returns an unsupported tag — the Python contract.
pub fn resolve_language(headers: &HeaderMap) -> &'static str {
    if let Some(explicit) = headers
        .get(LANGUAGE_HEADER)
        .and_then(|value| value.to_str().ok())
        .and_then(normalize_tag)
    {
        return explicit;
    }
    let accept = headers
        .get("accept-language")
        .and_then(|value| value.to_str().ok())
        .unwrap_or("");
    for part in accept.split(',') {
        let head = part.split(';').next().unwrap_or("");
        if let Some(tag) = normalize_tag(head) {
            return tag;
        }
    }
    DEFAULT_LANGUAGE
}

/// `"en-GB"` → `"en"` when supported, else `None`.
fn normalize_tag(value: &str) -> Option<&'static str> {
    let tag = value.trim().to_ascii_lowercase().replace('_', "-");
    if tag.is_empty() {
        return None;
    }
    let base = tag.split('-').next().unwrap_or("").to_string();
    SUPPORTED_LANGUAGES
        .iter()
        .copied()
        .find(|supported| *supported == base)
}

/// A FastAPI-shaped `HTTPException` response: `{"detail": "<localized>"}`.
pub fn http_error(status: StatusCode, key: &str, language: &str) -> Response {
    detail_error(status, &translate(key, language))
}

/// A FastAPI-shaped `HTTPException` response carrying `detail` verbatim.
pub fn detail_error(status: StatusCode, detail: &str) -> Response {
    json_response(status, &json!({ "detail": detail }).to_string(), None)
}

#[cfg(test)]
mod tests {
    use super::*;

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
    fn catalog_has_both_languages_for_every_id() {
        for (id, ko, en) in CATALOG {
            assert!(!ko.is_empty(), "{id} missing ko");
            assert!(!en.is_empty(), "{id} missing en");
        }
    }

    #[test]
    fn unknown_id_returns_the_id() {
        assert_eq!(translate("nope.at.all", "ko"), "nope.at.all");
    }

    #[test]
    fn explicit_header_beats_accept_language() {
        let map = headers(&[
            ("x-lattice-language", "en"),
            ("accept-language", "ko-KR,ko;q=0.9"),
        ]);
        assert_eq!(resolve_language(&map), "en");
    }

    #[test]
    fn accept_language_is_read_in_order() {
        let map = headers(&[("accept-language", "fr-FR,en-GB;q=0.9,ko;q=0.8")]);
        assert_eq!(resolve_language(&map), "en");
    }

    #[test]
    fn nothing_usable_falls_back_to_korean() {
        assert_eq!(resolve_language(&HeaderMap::new()), "ko");
        assert_eq!(
            resolve_language(&headers(&[("accept-language", "fr")])),
            "ko"
        );
        assert_eq!(
            resolve_language(&headers(&[("x-lattice-language", "  ")])),
            "ko"
        );
    }

    #[test]
    fn translate_picks_the_language() {
        assert_eq!(translate("auth.login_required", "ko"), "인증이 필요합니다.");
        assert_eq!(
            translate("auth.login_required", "en"),
            "You need to sign in first."
        );
    }
}
