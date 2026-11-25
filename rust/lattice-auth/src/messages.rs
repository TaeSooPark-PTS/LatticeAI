use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;
pub use lattice_core::messages::{DEFAULT_LANGUAGE, LANGUAGE_HEADER};
use serde_json::json;

use crate::response::json_response;

pub const SUPPORTED_LANGUAGES: [&str; 2] = ["ko", "en"];

pub const LOGIN_REQUIRED_LITERAL: &str = "인증이 필요합니다.";
pub const ADMIN_REQUIRED_LITERAL: &str = "관리자 권한이 필요합니다.";
pub const IP_RATE_LIMITED_LITERAL: &str = "요청이 너무 많습니다. 잠시 후 다시 시도하세요.";
pub const WORKSPACE_MISMATCH_LITERAL: &str = "Workspace selectors must match.";
pub const CSRF_DENIED_DETAIL: &str =
    "Request origin could not be verified and was rejected. It may have been sent from another site.";
pub const CSRF_ERROR_CODE: &str = "csrf_origin_rejected";

pub fn translate(key: &str, language: &str) -> String {
    lattice_core::messages::text(key, language, &[])
}

pub fn resolve_language(headers: &HeaderMap) -> &'static str {
    let explicit = headers.get(LANGUAGE_HEADER).and_then(|v| v.to_str().ok());
    let accept = headers.get("accept-language").and_then(|v| v.to_str().ok());
    lattice_core::messages::resolve_language(explicit, accept)
}

pub fn http_error(status: StatusCode, key: &str, language: &str) -> Response {
    detail_error(status, &translate(key, language))
}

pub fn detail_error(status: StatusCode, detail: &str) -> Response {
    json_response(status, &json!({ "detail": detail }).to_string(), None)
}

#[cfg(test)]
mod tests {
    use super::*;

    const AUTH_IDS: &[&str] = &[
        "auth.account_disabled",
        "auth.bad_credentials",
        "auth.current_password_wrong",
        "auth.email_taken",
        "auth.invitation_required",
        "auth.login_required",
        "auth.name_required",
        "auth.nickname_required",
        "auth.password_too_weak",
        "auth.registration_disabled",
        "auth.user_not_found",
    ];

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
    fn canonical_core_catalog_resolves_every_auth_id_in_both_languages() {
        for id in AUTH_IDS {
            let ko = translate(id, "ko");
            let en = translate(id, "en");
            assert_ne!(ko, *id, "{id} missing ko in lattice_core::messages");
            assert_ne!(en, *id, "{id} missing en in lattice_core::messages");
            assert!(!ko.is_empty());
            assert!(!en.is_empty());
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
