use axum::http::{HeaderMap, StatusCode};
use axum::response::Response;

use crate::messages::{detail_error, WORKSPACE_MISMATCH_LITERAL};

pub const WORKSPACE_HEADER: &str = "x-workspace-id";
pub const WORKSPACE_PARAM: &str = "workspace_id";

pub trait WorkspaceResolver: Send + Sync {
    fn resolve_read_scope(
        &self,
        requested: Option<&str>,
        user: Option<&str>,
    ) -> Result<Option<String>, String>;

    fn resolve_write_scope(
        &self,
        requested: Option<&str>,
        user: Option<&str>,
    ) -> Result<Option<String>, String>;
}

fn clean(value: Option<&str>) -> Option<String> {
    let text = value?.trim();
    if text.is_empty() {
        None
    } else {
        Some(text.to_string())
    }
}

pub fn query_workspace(query: Option<&str>) -> Option<String> {
    let query = query?;
    for pair in query.split('&') {
        let (key, value) = pair.split_once('=').unwrap_or((pair, ""));
        if key == WORKSPACE_PARAM {
            return clean(Some(&percent_decode(value)));
        }
    }
    None
}

fn percent_decode(value: &str) -> String {
    let bytes = value.as_bytes();
    let mut out: Vec<u8> = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        match bytes[index] {
            b'+' => {
                out.push(b' ');
                index += 1;
            }
            b'%' if index + 2 < bytes.len() => {
                let hex = std::str::from_utf8(&bytes[index + 1..index + 3]).unwrap_or("");
                match u8::from_str_radix(hex, 16) {
                    Ok(byte) => {
                        out.push(byte);
                        index += 3;
                    }
                    Err(_) => {
                        out.push(bytes[index]);
                        index += 1;
                    }
                }
            }
            byte => {
                out.push(byte);
                index += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

pub fn workspace_scope_from_request(headers: &HeaderMap, query: Option<&str>) -> Option<String> {
    let header = clean(
        headers
            .get(WORKSPACE_HEADER)
            .and_then(|value| value.to_str().ok()),
    );
    header.or_else(|| query_workspace(query))
}

pub fn requested_workspace(
    headers: &HeaderMap,
    query: Option<&str>,
    body_workspace: Option<&str>,
) -> Result<Option<String>, Response> {
    let mut selectors: Vec<String> = Vec::new();
    for value in [
        clean(body_workspace),
        clean(
            headers
                .get(WORKSPACE_HEADER)
                .and_then(|value| value.to_str().ok()),
        ),
        query_workspace(query),
    ]
    .into_iter()
    .flatten()
    {
        selectors.push(value);
    }
    let distinct = selectors.iter().collect::<std::collections::BTreeSet<_>>();
    if distinct.len() > 1 {
        return Err(detail_error(
            StatusCode::FORBIDDEN,
            WORKSPACE_MISMATCH_LITERAL,
        ));
    }
    Ok(selectors.into_iter().next())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ScopeMode {
    Read,
    Write,
}

pub fn resolve_workspace_scope(
    headers: &HeaderMap,
    query: Option<&str>,
    body_workspace: Option<&str>,
    user: &str,
    resolver: Option<&dyn WorkspaceResolver>,
    mode: ScopeMode,
    allow_unscoped_anonymous: bool,
) -> Result<Option<String>, Response> {
    let requested = requested_workspace(headers, query, body_workspace)?;
    let Some(resolver) = resolver else {
        return Ok(requested);
    };
    if allow_unscoped_anonymous && user.is_empty() && requested.is_none() {
        return Ok(None);
    }
    let user = if user.is_empty() { None } else { Some(user) };
    let outcome = match mode {
        ScopeMode::Read => resolver.resolve_read_scope(requested.as_deref(), user),
        ScopeMode::Write => resolver.resolve_write_scope(requested.as_deref(), user),
    };
    outcome.map_err(|message| detail_error(StatusCode::FORBIDDEN, &message))
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Deny;
    impl WorkspaceResolver for Deny {
        fn resolve_read_scope(
            &self,
            _requested: Option<&str>,
            _user: Option<&str>,
        ) -> Result<Option<String>, String> {
            Err("no read for you".into())
        }
        fn resolve_write_scope(
            &self,
            _requested: Option<&str>,
            _user: Option<&str>,
        ) -> Result<Option<String>, String> {
            Err("no write for you".into())
        }
    }

    struct Echo;
    impl WorkspaceResolver for Echo {
        fn resolve_read_scope(
            &self,
            requested: Option<&str>,
            _user: Option<&str>,
        ) -> Result<Option<String>, String> {
            Ok(requested.map(str::to_string).or(Some("active".into())))
        }
        fn resolve_write_scope(
            &self,
            requested: Option<&str>,
            user: Option<&str>,
        ) -> Result<Option<String>, String> {
            Ok(Some(format!(
                "{}:{}",
                requested.unwrap_or("active"),
                user.unwrap_or("anon")
            )))
        }
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
    fn the_header_wins_over_the_query_for_the_simple_reader() {
        let map = headers(&[("x-workspace-id", " team ")]);
        assert_eq!(
            workspace_scope_from_request(&map, Some("workspace_id=other")),
            Some("team".into())
        );
        assert_eq!(
            workspace_scope_from_request(&HeaderMap::new(), Some("a=1&workspace_id=other&b=2")),
            Some("other".into())
        );
        assert_eq!(workspace_scope_from_request(&HeaderMap::new(), None), None);
        assert_eq!(
            workspace_scope_from_request(&headers(&[("x-workspace-id", "  ")]), None),
            None
        );
    }

    #[test]
    fn agreeing_selectors_resolve_and_disagreeing_ones_are_refused() {
        let map = headers(&[("x-workspace-id", "team")]);
        assert_eq!(
            requested_workspace(&map, Some("workspace_id=team"), Some("team")).unwrap(),
            Some("team".into())
        );
        let refusal = requested_workspace(&map, Some("workspace_id=other"), None).unwrap_err();
        assert_eq!(refusal.status(), StatusCode::FORBIDDEN);
        assert_eq!(
            requested_workspace(&HeaderMap::new(), None, None).unwrap(),
            None
        );
    }

    #[test]
    fn query_values_are_percent_decoded() {
        assert_eq!(
            query_workspace(Some("workspace_id=team%20one")),
            Some("team one".into())
        );
        assert_eq!(
            query_workspace(Some("workspace_id=a+b")),
            Some("a b".into())
        );
        assert_eq!(
            query_workspace(Some("workspace_id=%zz")),
            Some("%zz".into())
        );
        assert_eq!(query_workspace(Some("workspace_id=%2")), Some("%2".into()));
        assert_eq!(query_workspace(Some("other=1")), None);
        assert_eq!(query_workspace(Some("workspace_id")), None);
        assert_eq!(query_workspace(None), None);
    }

    #[test]
    fn no_resolver_is_the_standalone_pass_through() {
        let map = headers(&[("x-workspace-id", "team")]);
        assert_eq!(
            resolve_workspace_scope(&map, None, None, "a@b.com", None, ScopeMode::Write, false)
                .unwrap(),
            Some("team".into())
        );
    }

    #[test]
    fn a_resolver_gates_reads_and_writes_separately() {
        assert_eq!(
            resolve_workspace_scope(
                &HeaderMap::new(),
                None,
                None,
                "a@b.com",
                Some(&Echo),
                ScopeMode::Read,
                false
            )
            .unwrap(),
            Some("active".into())
        );
        assert_eq!(
            resolve_workspace_scope(
                &HeaderMap::new(),
                None,
                Some("team"),
                "a@b.com",
                Some(&Echo),
                ScopeMode::Write,
                false
            )
            .unwrap(),
            Some("team:a@b.com".into())
        );
        let refusal = resolve_workspace_scope(
            &HeaderMap::new(),
            None,
            None,
            "",
            Some(&Deny),
            ScopeMode::Read,
            false,
        )
        .unwrap_err();
        assert_eq!(refusal.status(), StatusCode::FORBIDDEN);
    }

    #[test]
    fn an_anonymous_caller_may_stay_unscoped() {
        assert_eq!(
            resolve_workspace_scope(
                &HeaderMap::new(),
                None,
                None,
                "",
                Some(&Echo),
                ScopeMode::Write,
                true
            )
            .unwrap(),
            None
        );
        assert_eq!(
            resolve_workspace_scope(
                &HeaderMap::new(),
                None,
                Some("team"),
                "",
                Some(&Echo),
                ScopeMode::Write,
                true
            )
            .unwrap(),
            Some("team:anon".into())
        );
    }
}
