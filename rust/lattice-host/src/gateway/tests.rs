use super::*;
use crate::supervisor::status::WorkerStatus;

struct Stub;

impl StatusProvider for Stub {
    fn status(&self) -> WorkerStatus {
        WorkerStatus::idle(4825, true)
    }

    fn worker_origin(&self) -> String {
        "http://127.0.0.1:4825".into()
    }
}

#[test]
fn loopback_addresses_are_accepted() {
    for addr in ["127.0.0.1:4825", "[::1]:4825", "127.9.9.9:1"] {
        let parsed: SocketAddr = addr.parse().expect("addr");
        assert!(ensure_loopback(&parsed).is_ok(), "{addr} must be allowed");
    }
}

#[test]
fn public_addresses_are_refused() {
    for addr in ["0.0.0.0:4825", "192.168.1.10:4825", "[::]:4825"] {
        let parsed: SocketAddr = addr.parse().expect("addr");
        let err = ensure_loopback(&parsed).expect_err("must refuse");
        assert!(err.to_string().contains("loopback only"));
    }
}

#[tokio::test]
async fn binding_off_loopback_is_refused_before_touching_the_socket() {
    let err = bind_loopback("0.0.0.0:0".parse().expect("addr"))
        .await
        .expect_err("must refuse");
    assert!(matches!(err, GatewayError::NonLoopbackBind(_)));
}

#[tokio::test]
async fn bind_gateway_requires_auth_for_non_loopback_addresses() {
    let err = bind_gateway("0.0.0.0:0".parse().expect("addr"), false)
        .await
        .expect_err("must refuse unauthenticated public bind");
    assert!(matches!(err, GatewayError::UnauthenticatedPublicBind(_)));
    assert!(err.to_string().contains("LATTICEAI_REQUIRE_AUTH=true"));

    let listener = bind_gateway("0.0.0.0:0".parse().expect("addr"), true)
        .await
        .expect("authenticated public bind succeeds");
    assert!(listener.local_addr().is_ok());
}

#[tokio::test]
async fn binding_loopback_works_and_reports_the_chosen_port() {
    let listener = bind_loopback("127.0.0.1:0".parse().expect("addr"))
        .await
        .expect("bind");
    assert!(listener.local_addr().expect("addr").ip().is_loopback());
}

#[test]
fn state_exposes_the_provider() {
    let state = GatewayState::new(Arc::new(Stub)).expect("state");
    assert_eq!(state.worker_origin(), "http://127.0.0.1:4825");
    assert_eq!(state.status().port, 4825);
    assert!(format!("{state:?}").contains("127.0.0.1:4825"));
}

#[test]
fn the_store_defaults_to_the_configured_data_dir_and_can_be_overridden() {
    let state = GatewayState::new(Arc::new(Stub)).expect("state");
    assert_eq!(state.db_path(), lattice_core::graph_db_path());
    assert!(state.db_path().ends_with(lattice_core::DB_FILE_NAME));
    assert!(format!("{state:?}").contains(lattice_core::DB_FILE_NAME));

    let pinned = GatewayState::new(Arc::new(Stub))
        .expect("state")
        .with_db_path("/tmp/elsewhere/knowledge_graph.sqlite");
    assert_eq!(
        pinned.db(),
        Path::new("/tmp/elsewhere/knowledge_graph.sqlite")
    );
}

#[tokio::test]
async fn an_unreachable_worker_leaves_the_posture_unknown_and_caches_it() {
    struct Dead;
    impl StatusProvider for Dead {
        fn status(&self) -> WorkerStatus {
            WorkerStatus::idle(1, true)
        }
        fn worker_origin(&self) -> String {
            // Port 1 on loopback: reserved, nothing listens there.
            "http://127.0.0.1:1".into()
        }
    }
    let state = GatewayState::new(Arc::new(Dead)).expect("state");
    assert_eq!(
        state.cached_posture(),
        None,
        "nothing is known before anything is asked"
    );
    assert_eq!(state.worker_posture().await, Posture::Unknown);
    assert_eq!(
        state.cached_posture(),
        Some(Posture::Unknown),
        "the failure is cached too, so a dead worker is not re-probed per request"
    );
    assert!(!state.probe_worker_health().await);
}

#[tokio::test]
async fn a_pinned_posture_short_circuits_the_probe() {
    let state = GatewayState::new(Arc::new(Stub))
        .expect("state")
        .with_pinned_posture(Posture::Open);
    assert_eq!(state.worker_posture().await, Posture::Open);
    assert_eq!(
        state.cached_posture(),
        None,
        "a pinned posture never consults the worker, so nothing is observed"
    );
}

#[test]
fn the_proxy_client_is_this_state_s_own() {
    let shared = http_client().expect("client");
    let state = GatewayState::with_client(Arc::new(Stub), shared);
    // Distinct handles: the shared one follows redirects, this one must not.
    assert!(!std::ptr::eq(state.client(), state.proxy_client()));
}

#[test]
fn the_router_builds_without_route_conflicts() {
    // Pinned at a temporary root: building the router creates the agent
    // workspace, and a unit test has no business writing to the home
    // directory of whoever runs it.
    let dir = tempfile::tempdir().expect("tempdir");
    let state = GatewayState::new(Arc::new(Stub))
        .expect("state")
        .with_db_path(dir.path().join("knowledge_graph.sqlite"))
        .with_agent_root(dir.path().join("agent_workspace"));
    assert!(state.jobs().is_none(), "no scheduler unless one is wired");
    let _router = build_router(Arc::new(state));

    // …and again with the jobs routes mounted.
    let state = GatewayState::new(Arc::new(Stub))
        .expect("state")
        .with_db_path(dir.path().join("knowledge_graph.sqlite"))
        .with_agent_root(dir.path().join("agent_workspace"))
        .with_jobs(mounts::scheduler(
            "http://127.0.0.1:1",
            http_client().expect("client"),
        ));
    assert!(state.jobs().is_some());
    assert!(format!("{state:?}").contains("jobs: true"));
    let _router = build_router(Arc::new(state));
}
