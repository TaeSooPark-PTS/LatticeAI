//! Compatibility redirects from retired legacy pages into the SPA — native.
//!
//! Port of `latticeai/api/ui_redirects.py` and of every route that calls it. The
//! Python helper is four lines and its callers are scattered across
//! `latticeai/api/*.py`; the map below gathers them, because a redirect that
//! moved house without its callers would leave a bookmark answering 404 and
//! nobody would notice until a user did.
//!
//! The contract, from `rust/fixtures/http/static_ui.json`:
//!
//! * status **308**, not 302 — the method and body survive, and browsers cache
//!   it, which is the point of keeping the old paths alive at all;
//! * `Location: /app#/{fragment}{query}` — the fragment is a *hash* route, so
//!   the query has to sit **after** the `#` for the SPA router to see it;
//! * the query string is copied verbatim, still percent-encoded, and is dropped
//!   only where the Python handler dropped it (it passes no request).
//!
//! ## Authentication
//!
//! Two of these routes are public in Python (`/chat`, `/admin` — the SPA
//! enforces admin client-side and the API enforces it again); the rest call
//! `require_user` before redirecting. That split is data on [`UiRedirect`], and
//! [`authenticated_router`] is the half that must be mounted **behind**
//! `lattice-auth`'s user gate. [`router`] returns all of them and is for hosts
//! that apply the gate as an outer layer.
//!
//! `/` and `/account` are redirects too, but they are invite-gated and one of
//! them issues a cookie, so they live with the gate in [`crate::static_ui`].

#![allow(
    dead_code,
    unused_imports,
    unused_variables,
    unused_assignments,
    unused_mut,
    private_interfaces,
    clippy::result_large_err,
    clippy::needless_lifetimes,
    clippy::too_many_arguments,
    clippy::type_complexity,
    clippy::collapsible_if,
    clippy::needless_as_bytes,
    clippy::redundant_closure,
    clippy::needless_return,
    clippy::manual_clamp,
    clippy::ptr_arg,
    clippy::unnecessary_sort_by,
    clippy::result_unit_err,
    clippy::useless_vec,
    clippy::uninlined_format_args,
    clippy::manual_contains,
    clippy::needless_borrows_for_generic_args,
    clippy::implicit_clone,
    clippy::unnecessary_map_or,
    clippy::match_like_matches_macro,
    clippy::manual_range_contains,
    clippy::derivable_impls,
    clippy::needless_pass_by_ref_mut,
    clippy::redundant_guards,
    clippy::map_identity,
    clippy::iter_overeager_cloned,
    clippy::explicit_auto_deref,
    clippy::bool_comparison,
    clippy::nonminimal_bool,
    clippy::if_same_then_else,
    clippy::question_mark,
    clippy::single_char_pattern,
    clippy::manual_pattern_char_comparison,
    clippy::manual_is_ascii_check,
    clippy::repeat_once,
    clippy::unused_self,
    clippy::module_inception
)]
use axum::body::Body;
use axum::extract::RawQuery;
use axum::http::{header, Response, StatusCode};
use axum::routing::get;
use axum::Router;

use crate::static_ui::method_not_allowed;

/// The status every legacy page redirect answers with.
pub const REDIRECT_STATUS: StatusCode = StatusCode::PERMANENT_REDIRECT;

/// One legacy page path and the SPA hash route it now lives at.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct UiRedirect {
    /// The legacy path, exactly as the browser still asks for it.
    pub path: &'static str,
    /// The SPA fragment, without the leading `#/`.
    pub fragment: &'static str,
    /// Whether the Python handler calls `require_user` before redirecting.
    pub requires_user: bool,
    /// The Python module the route was lifted from — so the two can be diffed.
    pub python_module: &'static str,
}

/// Every legacy page redirect in the product.
///
/// Recorded in the fixture's `redirects.routes`, which a test asserts this
/// table against; adding a route in Python and not here fails that test rather
/// than quietly leaving a dead bookmark.
pub const REDIRECTS: &[UiRedirect] = &[
    UiRedirect {
        path: "/chat",
        fragment: "chat",
        requires_user: false,
        python_module: "static_routes.py",
    },
    UiRedirect {
        path: "/admin",
        fragment: "admin/users",
        requires_user: false,
        python_module: "static_routes.py",
    },
    UiRedirect {
        path: "/workspace",
        fragment: "workspace-admin",
        requires_user: true,
        python_module: "workspace.py",
    },
    UiRedirect {
        path: "/onboarding",
        fragment: "workspace-admin",
        requires_user: true,
        python_module: "workspace.py",
    },
    UiRedirect {
        path: "/graph",
        fragment: "knowledge-graph",
        requires_user: true,
        python_module: "knowledge_graph.py",
    },
    UiRedirect {
        path: "/knowledge-graph",
        fragment: "knowledge-graph",
        requires_user: true,
        python_module: "knowledge_graph.py",
    },
    UiRedirect {
        path: "/agents",
        fragment: "agents",
        requires_user: true,
        python_module: "agents.py",
    },
    UiRedirect {
        path: "/workflows",
        fragment: "workflows",
        requires_user: true,
        python_module: "workflow_designer.py",
    },
    UiRedirect {
        path: "/activity",
        fragment: "activity",
        requires_user: true,
        python_module: "realtime.py",
    },
    UiRedirect {
        path: "/plugins/sdk",
        fragment: "marketplace",
        requires_user: true,
        python_module: "plugins.py",
    },
];

/// `app_redirect(fragment, request)` — a 308 into the SPA's hash route.
///
/// `query` is the raw query string (no `?`), or `None` where the Python caller
/// passed no request. An empty query is the same as none: Starlette's
/// `request.url.query` is falsy for `/chat?`, so no `?` is appended there either.
pub fn app_redirect(fragment: &str, query: Option<&str>) -> Response<Body> {
    let fragment = fragment.trim_matches('/');
    let query = query.unwrap_or_default();
    let location = if query.is_empty() {
        format!("/app#/{fragment}")
    } else {
        format!("/app#/{fragment}?{query}")
    };
    let mut response = Response::new(Body::empty());
    *response.status_mut() = REDIRECT_STATUS;
    // A `Location` is only ever built here, from a compile-time fragment and a
    // query string that arrived percent-encoded, so the header value cannot
    // fail to parse — but a panic in a redirect is not worth the brevity.
    match header::HeaderValue::from_str(&location) {
        Ok(value) => {
            response.headers_mut().insert(header::LOCATION, value);
            response.headers_mut().insert(
                header::CONTENT_LENGTH,
                header::HeaderValue::from_static("0"),
            );
            response
        }
        Err(_) => {
            crate::static_ui::json_detail(StatusCode::BAD_REQUEST, "Invalid redirect target.")
        }
    }
}

/// Every legacy page redirect, for a host that gates them with an outer layer.
pub fn router() -> Router {
    router_from(REDIRECTS)
}

/// The redirects Python serves without asking who is calling: `/chat`, `/admin`.
pub fn public_router() -> Router {
    router_from_filtered(|route| !route.requires_user)
}

/// The redirects whose Python handlers call `require_user` first.
///
/// Mount this behind `lattice-auth`'s user gate; on its own it is open, which
/// is a difference from Python and the reason it is a separate factory rather
/// than a flag.
pub fn authenticated_router() -> Router {
    router_from_filtered(|route| route.requires_user)
}

/// A router over a chosen subset — the escape hatch for a host that mounts one
/// of these paths from another crate and needs this one to keep its hands off.
pub fn router_from(routes: &'static [UiRedirect]) -> Router {
    routes.iter().fold(Router::new(), |router, route| {
        router.route(route.path, redirect_method(route.fragment))
    })
}

fn router_from_filtered(keep: fn(&UiRedirect) -> bool) -> Router {
    REDIRECTS
        .iter()
        .filter(|route| keep(route))
        .fold(Router::new(), |router, route| {
            router.route(route.path, redirect_method(route.fragment))
        })
}

/// GET answers the redirect; everything else — HEAD included, as in Python,
/// where these are `@router.get` — answers 405 with `Allow: GET`.
fn redirect_method(fragment: &'static str) -> axum::routing::MethodRouter {
    get(move |RawQuery(query): RawQuery| async move { app_redirect(fragment, query.as_deref()) })
        .head(|| async { method_not_allowed("GET") })
        .fallback(|| async { method_not_allowed("GET") })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn location(response: &Response<Body>) -> &str {
        response
            .headers()
            .get(header::LOCATION)
            .expect("location")
            .to_str()
            .expect("ascii")
    }

    #[test]
    fn a_redirect_is_a_308_into_the_hash_route() {
        let response = app_redirect("chat", None);
        assert_eq!(response.status(), StatusCode::PERMANENT_REDIRECT);
        assert_eq!(location(&response), "/app#/chat");
    }

    #[test]
    fn the_query_follows_the_fragment_verbatim() {
        let response = app_redirect("chat", Some("thread=7&x=%20a"));
        assert_eq!(location(&response), "/app#/chat?thread=7&x=%20a");
    }

    #[test]
    fn an_empty_query_is_no_query() {
        // Starlette's `request.url.query` is `""` for `/chat?`, and `""` is falsy.
        assert_eq!(location(&app_redirect("chat", Some(""))), "/app#/chat");
    }

    #[test]
    fn slashes_around_the_fragment_are_stripped_as_python_strips_them() {
        assert_eq!(
            location(&app_redirect("/admin/users/", None)),
            "/app#/admin/users"
        );
    }

    #[test]
    fn the_routers_partition_the_table() {
        let public = REDIRECTS
            .iter()
            .filter(|route| !route.requires_user)
            .count();
        let gated = REDIRECTS.iter().filter(|route| route.requires_user).count();
        assert_eq!(public + gated, REDIRECTS.len());
        assert_eq!(public, 2, "only /chat and /admin skip require_user");
        // Building each router proves the paths do not collide.
        let _ = router();
        let _ = public_router();
        let _ = authenticated_router();
    }
}
