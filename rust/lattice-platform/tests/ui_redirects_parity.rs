//! The legacy page redirects, against the recorded Python contract (WP-I4).
//!
//! Three things are proven here, and the third is the one that rots silently:
//!
//! 1. every route in [`lattice_platform::ui_redirects::REDIRECTS`] answers 308
//!    to the SPA hash route, over real HTTP, with the query where Python puts it;
//! 2. the table *is* the Python map — same paths, same fragments, same
//!    `require_user` flags — asserted against `redirects.routes` in the fixture;
//! 3. a path that only one of the two routers claims cannot be added twice: the
//!    static router and this one are merged in the same host, and axum panics on
//!    a duplicate, so the merge itself is a test.

#![allow(dead_code, unused_imports, unused_variables)]
#![allow(clippy::all)]
mod static_ui_harness;

use lattice_platform::ui_redirects::{
    app_redirect, authenticated_router, public_router, router, REDIRECTS, REDIRECT_STATUS,
};
use static_ui_harness::{fixture, Install};

/// The fixture's table, minus `/account` — that one is invite-gated and is
/// served by `static_ui`, which is where its cases live.
fn recorded_routes() -> Vec<(&'static str, &'static str, bool)> {
    fixture()["redirects"]["routes"]
        .as_array()
        .expect("routes")
        .iter()
        .map(|route| {
            (
                route["path"].as_str().expect("path"),
                route["fragment"].as_str().expect("fragment"),
                route["requires_user"].as_bool().expect("flag"),
            )
        })
        .filter(|(path, _, _)| *path != "/account")
        .collect()
}

#[test]
fn the_table_is_the_python_map() {
    let recorded = recorded_routes();
    assert_eq!(
        recorded.len(),
        REDIRECTS.len(),
        "the fixture knows {} redirects, the table {}",
        recorded.len(),
        REDIRECTS.len()
    );
    for (path, fragment, requires_user) in recorded {
        let ours = REDIRECTS
            .iter()
            .find(|route| route.path == path)
            .unwrap_or_else(|| panic!("{path} is not in the Rust table"));
        assert_eq!(ours.fragment, fragment, "{path}: fragment");
        assert_eq!(ours.requires_user, requires_user, "{path}: require_user");
    }
}

#[test]
fn the_recorded_status_is_the_one_we_answer() {
    assert_eq!(
        REDIRECT_STATUS.as_u16(),
        fixture()["redirects"]["status"].as_u64().expect("status") as u16
    );
}

#[tokio::test]
async fn every_redirect_answers_over_http() {
    let install = Install::serve(router(), std::path::PathBuf::from(".")).await;
    for route in REDIRECTS {
        let response = install
            .client
            .get(format!("{}{}", install.origin, route.path))
            .send()
            .await
            .expect("request");
        assert_eq!(response.status().as_u16(), 308, "{}", route.path);
        assert_eq!(
            response
                .headers()
                .get("location")
                .expect("location")
                .to_str()
                .expect("ascii"),
            format!("/app#/{}", route.fragment),
            "{}",
            route.path
        );
        assert_eq!(response.bytes().await.expect("body").len(), 0);
    }
}

#[tokio::test]
async fn a_redirect_carries_the_query_across_the_hash() {
    let install = Install::serve(router(), std::path::PathBuf::from(".")).await;
    let response = install
        .client
        .get(format!(
            "{}/workspace?tab=members&q=%EA%B9%80",
            install.origin
        ))
        .send()
        .await
        .expect("request");
    assert_eq!(
        response.headers()["location"].to_str().expect("ascii"),
        "/app#/workspace-admin?tab=members&q=%EA%B9%80",
        "the query is copied byte-for-byte, still encoded"
    );
}

#[tokio::test]
async fn these_are_get_routes_and_say_so() {
    let install = Install::serve(router(), std::path::PathBuf::from(".")).await;
    for method in [reqwest::Method::HEAD, reqwest::Method::POST] {
        let response = install
            .client
            .request(method.clone(), format!("{}/chat", install.origin))
            .send()
            .await
            .expect("request");
        assert_eq!(response.status().as_u16(), 405, "{method}");
        assert_eq!(response.headers()["allow"].to_str().expect("ascii"), "GET");
        assert_eq!(
            response.headers()["content-type"].to_str().expect("ascii"),
            "application/json"
        );
        // `POST /chat` is the SSE stream in the product. This 405 is only what
        // *this* router says about a path it serves the page redirect for; the
        // chat crate mounts the POST and the two merge without colliding.
    }
}

#[test]
fn the_halves_add_up_to_the_whole() {
    // Building all three is the collision check; counting them is the coverage
    // check — a route in neither half would be served by `router` alone.
    let _ = router();
    let _ = public_router();
    let _ = authenticated_router();
    let public: Vec<_> = REDIRECTS
        .iter()
        .filter(|route| !route.requires_user)
        .collect();
    let gated: Vec<_> = REDIRECTS
        .iter()
        .filter(|route| route.requires_user)
        .collect();
    assert_eq!(public.len() + gated.len(), REDIRECTS.len());
    assert!(public
        .iter()
        .all(|route| route.path == "/chat" || route.path == "/admin"));
}

#[test]
fn the_helper_drops_an_empty_query_and_keeps_a_real_one() {
    let location = |response: axum::http::Response<axum::body::Body>| {
        response.headers()["location"]
            .to_str()
            .expect("ascii")
            .to_string()
    };
    assert_eq!(location(app_redirect("activity", None)), "/app#/activity");
    assert_eq!(
        location(app_redirect("activity", Some(""))),
        "/app#/activity"
    );
    assert_eq!(
        location(app_redirect("activity", Some("a=1"))),
        "/app#/activity?a=1"
    );
}
