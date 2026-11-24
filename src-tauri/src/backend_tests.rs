use super::*;
use crate::topology::{DIRECT_ENV, NO_BACKEND_ENV, ORIGIN_ENV, PORT_ENV};
use lattice_host::supervisor::StaticProbe;

fn backend(probe: StaticProbe) -> DesktopBackend {
    DesktopBackend::with_probe(Arc::new(probe)).expect("backend")
}

#[test]
fn the_default_shell_is_the_gateway_front_door() {
    let shell = backend(StaticProbe::new().with_env(PORT_ENV, "41840"));
    assert_eq!(shell.topology(), Topology::Gateway);
    assert_eq!(shell.origin(), "http://127.0.0.1:41840");
    assert_eq!(shell.app_url(), "http://127.0.0.1:41840/app");
    assert!(shell.supervised());
    assert_ne!(
        shell.plan.worker_origin, shell.plan.origin,
        "the worker lives behind the front door"
    );
}

#[test]
fn a_retired_direct_flag_still_fronts_the_gateway() {
    let shell = backend(
        StaticProbe::new()
            .with_env(DIRECT_ENV, "1")
            .with_env(PORT_ENV, "41841"),
    );
    assert_eq!(shell.topology(), Topology::Gateway);
    assert_ne!(shell.plan.worker_origin, shell.plan.origin);
    assert!(
        shell.jobs.lock().expect("jobs lock").is_some(),
        "gateway topology still mounts /host/jobs"
    );
}

#[test]
fn the_origin_override_pins_the_origin_and_spawns_nothing() {
    let shell = backend(StaticProbe::new().with_env(ORIGIN_ENV, "http://127.0.0.1:9100/"));
    assert_eq!(shell.origin(), "http://127.0.0.1:9100/");
    assert_eq!(shell.app_url(), "http://127.0.0.1:9100/app");
    assert!(!shell.supervised());
    assert_eq!(shell.topology(), Topology::External);
    assert!(
        shell.jobs.lock().expect("jobs lock").is_none(),
        "an attached host is not ours to schedule"
    );
}

#[test]
fn a_retired_kill_switch_still_fronts_the_gateway() {
    let shell = backend(
        StaticProbe::new()
            .with_env(NO_BACKEND_ENV, "1")
            .with_env(PORT_ENV, "41845"),
    );
    assert!(shell.supervised());
    assert_eq!(shell.topology(), Topology::Gateway);
    assert_eq!(shell.origin(), "http://127.0.0.1:41845");
}

#[test]
fn an_explicit_origin_outranks_the_retired_kill_switch() {
    let shell = backend(
        StaticProbe::new()
            .with_env(NO_BACKEND_ENV, "1")
            .with_env(ORIGIN_ENV, "http://127.0.0.1:9100"),
    );
    assert!(!shell.supervised(), "still nothing is spawned");
    assert_eq!(shell.topology(), Topology::External);
}

#[test]
fn an_unresolved_command_says_why_instead_of_going_blank() {
    let shell = backend(StaticProbe::new());
    let mut status = WorkerStatus::idle(4825, true);
    assert_eq!(shell.command_text(&status), "not resolved yet");
    status.last_error = Some("worker unavailable".into());
    assert_eq!(
        shell.command_text(&status),
        "unavailable: worker unavailable"
    );
    status.command = Some("/usr/bin/python3 -m latticeai.worker_app".into());
    assert_eq!(
        shell.command_text(&status),
        "/usr/bin/python3 -m latticeai.worker_app"
    );
}

#[test]
fn an_external_host_is_named_by_its_own_origin() {
    let shell = backend(StaticProbe::new().with_env(ORIGIN_ENV, "http://127.0.0.1:9100"));
    assert_eq!(
        shell.command_text(&WorkerStatus::idle(9100, false)),
        "external lattice-host at http://127.0.0.1:9100 (not started here)"
    );
}

#[test]
fn the_status_keeps_every_field_the_frontend_has_ever_read() {
    let shell = backend(StaticProbe::new().with_env(PORT_ENV, "41842"));
    let rendered = shell.render(&WorkerStatus::idle(41843, true));
    let value = serde_json::to_value(&rendered).expect("serialise");
    for key in ["origin", "command", "cwd", "running", "pid", "last_error"] {
        assert!(value.get(key).is_some(), "missing legacy field {key}");
    }
    assert!(value["command"].is_string(), "command is never null");
    assert_eq!(value["topology"], "gateway");
    assert_eq!(value["gateway_origin"], "http://127.0.0.1:41842");
    assert_eq!(value["worker_origin"], shell.plan.worker_origin);
    assert_eq!(value["jobs_running"], false, "mounted is not started");
}

#[test]
fn an_external_shell_reports_no_local_gateway_origin() {
    let shell = backend(StaticProbe::new().with_env(ORIGIN_ENV, "http://127.0.0.1:9100"));
    let value =
        serde_json::to_value(shell.render(&WorkerStatus::idle(9100, false))).expect("serialise");
    assert_eq!(value["topology"], "external");
    assert!(value["gateway_origin"].is_null());
    assert_eq!(value["worker_origin"], value["origin"]);
}

#[tokio::test]
async fn stopping_a_gateway_that_never_started_is_a_no_op() {
    let shell = backend(StaticProbe::new().with_env(ORIGIN_ENV, "http://127.0.0.1:9100"));
    shell.stop_gateway().await;
    shell.stop_gateway().await;
}

#[tokio::test]
async fn stop_gateway_clears_slot_so_subsequent_restart_is_allowed() {
    let shell = backend(StaticProbe::new().with_env(PORT_ENV, "41849"));
    assert!(shell.gateway.lock().unwrap().is_none());
    let (tx, mut rx) = tokio::sync::oneshot::channel::<()>();
    let task = tauri::async_runtime::spawn(async move {
        let _ = (&mut rx).await;
    });
    *shell.gateway.lock().unwrap() = Some(RunningGateway {
        shutdown: Some(tx),
        task,
    });
    shell.stop_gateway().await;
    assert!(
        shell.gateway.lock().unwrap().is_none(),
        "stop_gateway must clear self.gateway so restart can rebind"
    );
}

#[test]
fn a_healthy_shell_names_no_gateway_error() {
    for probe in [
        StaticProbe::new().with_env(PORT_ENV, "41844"),
        StaticProbe::new().with_env(ORIGIN_ENV, "http://127.0.0.1:9100"),
    ] {
        let shell = backend(probe);
        assert_eq!(shell.gateway_error(), None);
        let value =
            serde_json::to_value(shell.render(&WorkerStatus::idle(4825, true))).expect("json");
        assert!(value["gateway_error"].is_null());
    }
}

#[tokio::test]
async fn a_gateway_that_cannot_bind_is_reported_and_not_navigated_to() {
    let occupied = std::net::TcpListener::bind("127.0.0.1:0").expect("bind");
    let port = occupied.local_addr().expect("addr").port();
    let shell = backend(StaticProbe::new().with_env(PORT_ENV, &port.to_string()));
    assert_eq!(shell.topology(), Topology::Gateway);

    shell.start_gateway().await;

    let reason = shell.gateway_error().expect("the failure is recorded");
    assert!(reason.contains("could not bind"), "{reason}");
    assert!(
        reason.contains("LATTICEAI_DESKTOP_BACKEND_ORIGIN"),
        "the way out is named: {reason}"
    );
    assert!(
        !reason.contains("LATTICEAI_DESKTOP_DIRECT"),
        "the retired hatch must not be offered: {reason}"
    );
    assert!(
        shell.app_url().contains(&port.to_string()),
        "app_url still names the port nothing bound — which is why boot must 
         read gateway_error instead of navigating"
    );
    let value =
        serde_json::to_value(shell.render(&WorkerStatus::idle(port, true))).expect("serialise");
    assert_eq!(value["gateway_error"], reason);
    drop(occupied);
}

#[test]
fn an_existing_backend_cmd_is_left_alone() {
    let probe = StaticProbe::new().with_env(BACKEND_CMD_ENV, "custom-worker --flag");
    assert_eq!(resolve_worker_python(&probe), None);
}

#[test]
fn a_bundled_tree_prefers_bundled_or_uvicorn_capable_python() {
    let probe = StaticProbe::new()
        .with_resource_dir("/app/Resources")
        .with_file("/app/Resources/latticeai/worker_app.py")
        .with_file("/app/Resources/python/bin/python3")
        .with_importable("/app/Resources/python/bin/python3");
    assert_eq!(
        resolve_worker_python(&probe).as_deref(),
        Some("/app/Resources/python/bin/python3")
    );
}

#[test]
fn an_unpackaged_shell_resolves_the_worker_interpreter_for_supervisor_factory() {
    let probe = StaticProbe::new().with_importable("/opt/homebrew/bin/python3");
    let python = resolve_worker_python(&probe).expect("python");
    assert_eq!(python, "/opt/homebrew/bin/python3");
}

#[test]
fn packaged_sidecar_binary_takes_precedence_over_workspace_build() {
    let bin_name = if cfg!(windows) {
        "lattice-host.exe"
    } else {
        "lattice-host"
    };
    let sidecar = format!("/Applications/Lattice AI.app/Contents/MacOS/{bin_name}");
    let ws_bin = format!("/workspace/rust/target/release/{bin_name}");
    let probe = StaticProbe::new().with_file(&sidecar).with_file(&ws_bin);
    let found = resolve_packaged_host_binary(
        &probe,
        Some(std::path::Path::new(
            "/Applications/Lattice AI.app/Contents/MacOS",
        )),
        Some(std::path::Path::new("/workspace")),
    )
    .expect("resolved");
    assert_eq!(found, std::path::PathBuf::from(sidecar));
}

#[test]
fn degraded_worker_surfaces_explicit_non_silent_warning() {
    let shell = backend(StaticProbe::new().with_env(PORT_ENV, "41846"));
    let mut status = WorkerStatus::idle(41847, true);
    status.last_error = Some("worker unavailable".into());
    let rendered = shell.render(&status);
    let warning = rendered.worker_warning.expect("warning present");
    assert!(warning.contains("Local Brain"), "{warning}");
    assert!(warning.contains("LTCAI_PYTHON"), "{warning}");
}

#[test]
fn production_policy_rejects_unpinned_port_override_before_boot() {
    let err = DesktopBackend::with_probe_and_policy(
        Arc::new(StaticProbe::new().with_env(PORT_ENV, "4826")),
        PortPolicy::PinnedProduction,
    )
    .err()
    .expect("production policy rejects non-4825 port");
    assert!(err.contains("4825"), "{err}");
    assert!(err.contains("CSP"), "{err}");
}

#[tokio::test]
async fn production_policy_fails_fast_with_clear_error_when_pinned_port_is_occupied() {
    let occupied =
        std::net::TcpListener::bind(("127.0.0.1", topology::PINNED_PRODUCTION_PORT)).ok();
    let shell = DesktopBackend::with_probe_and_policy(
        Arc::new(StaticProbe::new()),
        PortPolicy::PinnedProduction,
    )
    .expect("production shell resolves to pinned 4825");
    assert_eq!(shell.origin(), "http://127.0.0.1:4825");

    if occupied.is_some() {
        shell.start_gateway().await;
        let reason = shell
            .gateway_error()
            .expect("occupied pinned port records a clear production error");
        assert!(reason.contains("could not bind"), "{reason}");
        assert!(reason.contains("4825"), "{reason}");
        assert!(reason.contains("CSP"), "{reason}");
        assert!(
            !reason.contains("set LATTICEAI_PORT to a free port"),
            "production error must not advise an unreachable port: {reason}"
        );
    }
    drop(occupied);
}
