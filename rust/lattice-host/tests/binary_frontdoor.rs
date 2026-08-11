//! End-to-end test of the `lattice-host` binary itself: the front-door
//! topology (gateway on the public port, worker behind it) and a clean
//! shutdown on SIGTERM.

mod common;

use std::process::Stdio;
use std::time::Duration;

use common::{client, json, FakeWorker};
use tokio::process::Command;

/// Grab a port, then release it — the binary binds it a moment later.
fn free_port() -> u16 {
    let listener = std::net::TcpListener::bind("127.0.0.1:0").expect("bind");
    listener.local_addr().expect("addr").port()
}

#[tokio::test]
async fn the_binary_fronts_an_existing_worker_and_shuts_down_cleanly() {
    let worker = FakeWorker::start().await;
    let gateway_port = free_port();

    let mut child = Command::new(env!("CARGO_BIN_EXE_lattice-host"))
        .args([
            "--no-spawn",
            "--port",
            &gateway_port.to_string(),
            "--worker-port",
            &worker.port().to_string(),
        ])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .kill_on_drop(true)
        .spawn()
        .expect("spawn lattice-host");

    let base = format!("http://127.0.0.1:{gateway_port}");
    let http = client();

    // Wait for the front door to come up.
    let mut ready = false;
    for _ in 0..200 {
        if http
            .get(format!("{base}/host/health"))
            .send()
            .await
            .map(|response| response.status().is_success())
            .unwrap_or(false)
        {
            ready = true;
            break;
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    assert!(ready, "the gateway never answered on {base}/host/health");

    let body = json(
        http.get(format!("{base}/host/health"))
            .send()
            .await
            .expect("health"),
    )
    .await;
    assert_eq!(body["status"], "ok");
    assert_eq!(body["worker_healthy"], true);
    assert_eq!(
        body["worker"]["supervised"], false,
        "--no-spawn was honoured"
    );
    assert_eq!(body["worker"]["port"], worker.port());

    let proxied = http
        .get(format!("{base}/api/memory"))
        .send()
        .await
        .expect("proxied request");
    assert_eq!(proxied.status(), 200);
    assert_eq!(
        proxied.text().await.expect("body"),
        "worker saw /api/memory"
    );

    // SIGTERM must end the process, not wedge it.
    let pid = child.id().expect("pid");
    assert_eq!(
        lattice_host::supervisor::process::terminate(pid),
        lattice_host::supervisor::SignalOutcome::Delivered
    );
    let status = tokio::time::timeout(Duration::from_secs(10), child.wait())
        .await
        .expect("the binary exits on SIGTERM")
        .expect("wait");
    assert!(status.success(), "clean exit, got {status}");

    worker.shutdown();
}
