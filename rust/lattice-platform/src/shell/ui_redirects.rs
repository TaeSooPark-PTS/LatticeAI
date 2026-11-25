use axum::body::Body;
use axum::extract::RawQuery;
use axum::http::{header, Response, StatusCode};
use axum::routing::get;
use axum::Router;

use crate::shell::static_ui::method_not_allowed;

pub const REDIRECT_STATUS: StatusCode = StatusCode::PERMANENT_REDIRECT;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct UiRedirect {
    pub path: &'static str,
    pub fragment: &'static str,
    pub requires_user: bool,
    pub python_module: &'static str,
}

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
    match header::HeaderValue::from_str(&location) {
        Ok(value) => {
            response.headers_mut().insert(header::LOCATION, value);
            response.headers_mut().insert(
                header::CONTENT_LENGTH,
                header::HeaderValue::from_static("0"),
            );
            response
        }
        Err(_) => crate::shell::static_ui::json_detail(
            StatusCode::BAD_REQUEST,
            "Invalid redirect target.",
        ),
    }
}

pub fn router() -> Router {
    router_from(REDIRECTS)
}

pub fn public_router() -> Router {
    router_from_filtered(|route| !route.requires_user)
}

pub fn authenticated_router() -> Router {
    router_from_filtered(|route| route.requires_user)
}

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
        let _ = router();
        let _ = public_router();
        let _ = authenticated_router();
    }
}
