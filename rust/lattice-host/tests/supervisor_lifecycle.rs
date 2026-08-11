//! Supervisor lifecycle integration tests.
//!
//! The "worker" is a `/bin/sh` script (so the process really exists, really
//! traps signals and really exits) while its HTTP face is the pure-Rust
//! [`common::FakeWorker`] on the port the supervisor chose. That split lets
//! every branch — health gate, crash restart, graceful stop — be driven
//! deterministically without a python interpreter.

mod common;

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::{Duration, Instant};

use common::{wait_until, FakeWorker};
use lattice_host::supervisor::process::is_alive;
use lattice_host::supervisor::{
    BackoffPolicy, StaticProbe, Supervisor, SupervisorConfig, SupervisorError,
};

struct Scripted {
    _dir: tempfile::TempDir,
    root: PathBuf,
    script: PathBuf,
}

impl Scripted {
    /// Write `body` as a shell script the supervisor will be told to run.
    fn new(body: &str) -> Self {
        let dir = tempfile::tempdir().expect("tempdir");
        let root = dir.path().to_path_buf();
        let script = root.join("worker.sh");
        std::fs::write(&script, body).expect("write script");
        Self {
            _dir: dir,
            root,
            script,
        }
    }

    fn path(&self, name: &str) -> PathBuf {
        self.root.join(name)
    }

    fn probe(&self) -> Arc<StaticProbe> {
        Arc::new(StaticProbe::new().with_env(
            "LATTICEAI_DESKTOP_BACKEND_CMD",
            &format!("/bin/sh {}", self.script.display()),
        ))
    }

    fn read(&self, name: &str) -> String {
        std::fs::read_to_string(self.path(name)).unwrap_or_default()
    }

    fn lines(&self, name: &str) -> usize {
        self.read(name).lines().count()
    }
}

fn config(port: u16) -> SupervisorConfig {
    let mut config = SupervisorConfig::new(port);
    config.health_interval = Duration::from_millis(20);
    config.health_deadline = Duration::from_secs(5);
    config.health_poll_interval = Duration::from_millis(50);
    config.stop_grace = Duration::from_millis(400);
    config.backoff = BackoffPolicy {
        base: Duration::from_millis(20),
        cap: Duration::from_millis(60),
        max_attempts: 50,
        reset_after: Duration::from_secs(3600),
    };
    config
}

async fn wait_for_file(path: &Path) {
    let path = path.to_path_buf();
    assert!(
        wait_until(Duration::from_secs(5), move || path.exists()).await,
        "the worker never reported readiness"
    );
}

#[tokio::test]
async fn the_health_gate_opens_only_once_the_worker_answers() {
    let worker = FakeWorker::start_with_health(false).await;
    let scripted = Scripted::new(
        "echo \"port=$LATTICEAI_PORT\" > \"$LATTICEAI_TEST_DIR/env.txt\"\n\
         : > \"$LATTICEAI_TEST_DIR/ready\"\n\
         while true; do sleep 0.05; done\n",
    );
    let mut config = config(worker.port());
    config.log_dir = Some(scripted.root.clone());
    config.extra_env = vec![(
        "LATTICEAI_TEST_DIR".to_string(),
        scripted.root.to_string_lossy().into_owned(),
    )];
    let supervisor = Supervisor::with_probe(config, scripted.probe()).expect("supervisor");

    // The gate must stay shut while /health answers 503.
    let start = supervisor.clone();
    let starting = tokio::spawn(async move { start.start().await });
    wait_for_file(&scripted.path("ready")).await;
    assert!(
        !starting.is_finished(),
        "gate opened without a healthy worker"
    );

    worker.set_healthy(true);
    let status = starting
        .await
        .expect("join")
        .expect("the gate opens once /health is 2xx");
    assert!(status.running);
    assert!(status.healthy);
    assert!(status.pid.is_some());
    assert_eq!(status.port, worker.port());
    assert_eq!(status.restarts, 0);
    assert!(status.supervised);

    // The chosen port is what the worker was actually told to bind.
    assert_eq!(
        scripted.read("env.txt").trim(),
        format!("port={}", worker.port())
    );
    // ...and stdout landed in the sidecar log.
    assert!(scripted.path("desktop-sidecar.log").exists());

    supervisor.stop().await;
    worker.shutdown();
}

#[tokio::test]
async fn a_worker_that_never_answers_reports_a_health_timeout() {
    let worker = FakeWorker::start_with_health(false).await;
    let scripted =
        Scripted::new(": > \"$LATTICEAI_TEST_DIR/ready\"\nwhile true; do sleep 0.05; done\n");
    let mut config = config(worker.port());
    config.health_deadline = Duration::from_millis(200);
    config.extra_env = vec![(
        "LATTICEAI_TEST_DIR".to_string(),
        scripted.root.to_string_lossy().into_owned(),
    )];
    let supervisor = Supervisor::with_probe(config, scripted.probe()).expect("supervisor");

    let err = supervisor.start().await.expect_err("gate must time out");
    assert!(matches!(err, SupervisorError::Health(_)), "got {err:?}");
    assert!(err.to_string().contains("GET /health"));

    let status = supervisor.status();
    assert!(
        status.running,
        "the process is up even though it is not ready"
    );
    assert!(!status.healthy);

    supervisor.stop().await;
    worker.shutdown();
}

#[tokio::test]
async fn a_crashing_worker_is_restarted_and_a_manual_stop_ends_it() {
    let worker = FakeWorker::start().await;
    let scripted = Scripted::new(
        "echo run >> \"$LATTICEAI_TEST_DIR/runs.txt\"\n\
         sleep 0.05\n\
         exit 3\n",
    );
    let mut config = config(worker.port());
    config.health_deadline = Duration::from_secs(2);
    config.extra_env = vec![(
        "LATTICEAI_TEST_DIR".to_string(),
        scripted.root.to_string_lossy().into_owned(),
    )];
    let supervisor = Supervisor::with_probe(config, scripted.probe()).expect("supervisor");
    supervisor.start().await.expect("first run comes up");

    assert!(
        wait_until(Duration::from_secs(15), || scripted.lines("runs.txt") >= 3).await,
        "the crash monitor never re-spawned the worker (saw {} runs)",
        scripted.lines("runs.txt")
    );
    assert!(
        supervisor.status().restarts >= 2,
        "every re-spawn is counted, saw {}",
        supervisor.status().restarts
    );
    let status = supervisor.status();
    assert!(
        status
            .last_error
            .as_deref()
            .unwrap_or_default()
            .contains("exited with"),
        "the crash is reported: {:?}",
        status.last_error
    );

    // A manual stop suppresses every further restart.
    let stopped = supervisor.stop().await;
    assert!(!stopped.running);
    let after_stop = scripted.lines("runs.txt");
    tokio::time::sleep(Duration::from_millis(300)).await;
    assert_eq!(
        scripted.lines("runs.txt"),
        after_stop,
        "nothing may be re-spawned after stop()"
    );
    assert!(!supervisor.status().running);

    worker.shutdown();
}

#[tokio::test]
async fn stopping_a_healthy_worker_never_restarts_it() {
    let worker = FakeWorker::start().await;
    let scripted = Scripted::new(
        "echo run >> \"$LATTICEAI_TEST_DIR/runs.txt\"\n\
         : > \"$LATTICEAI_TEST_DIR/ready\"\n\
         while true; do sleep 0.05; done\n",
    );
    let mut config = config(worker.port());
    config.extra_env = vec![(
        "LATTICEAI_TEST_DIR".to_string(),
        scripted.root.to_string_lossy().into_owned(),
    )];
    let supervisor = Supervisor::with_probe(config, scripted.probe()).expect("supervisor");

    let status = supervisor.start().await.expect("start");
    let pid = status.pid.expect("pid");
    wait_for_file(&scripted.path("ready")).await;

    let stopped = supervisor.stop().await;
    assert!(!stopped.running);
    assert!(!stopped.healthy);
    assert_eq!(stopped.restarts, 0, "a manual stop is not a crash");
    assert!(
        wait_until(Duration::from_secs(2), move || !is_alive(pid)).await,
        "the worker process outlived stop()"
    );
    tokio::time::sleep(Duration::from_millis(250)).await;
    assert_eq!(scripted.lines("runs.txt"), 1, "exactly one run, ever");

    worker.shutdown();
}

#[tokio::test]
async fn graceful_stop_sends_sigterm_before_sigkill() {
    let worker = FakeWorker::start().await;
    let scripted = Scripted::new(
        "trap 'echo term >> \"$LATTICEAI_TEST_DIR/signals.txt\"; exit 0' TERM\n\
         : > \"$LATTICEAI_TEST_DIR/ready\"\n\
         while true; do sleep 0.05; done\n",
    );
    let mut config = config(worker.port());
    config.extra_env = vec![(
        "LATTICEAI_TEST_DIR".to_string(),
        scripted.root.to_string_lossy().into_owned(),
    )];
    let supervisor = Supervisor::with_probe(config, scripted.probe()).expect("supervisor");
    supervisor.start().await.expect("start");
    wait_for_file(&scripted.path("ready")).await;

    let started = Instant::now();
    supervisor.stop().await;
    assert!(
        scripted.read("signals.txt").contains("term"),
        "the worker must be asked to terminate, not shot: {:?}",
        scripted.read("signals.txt")
    );
    assert!(
        started.elapsed() < Duration::from_millis(400),
        "a cooperative worker must not wait out the whole grace period"
    );

    worker.shutdown();
}

#[tokio::test]
async fn a_worker_that_ignores_sigterm_is_killed_after_the_grace_period() {
    let worker = FakeWorker::start().await;
    let scripted = Scripted::new(
        "trap '' TERM\n\
         : > \"$LATTICEAI_TEST_DIR/ready\"\n\
         while true; do sleep 0.05; done\n",
    );
    let mut config = config(worker.port());
    config.stop_grace = Duration::from_millis(200);
    config.extra_env = vec![(
        "LATTICEAI_TEST_DIR".to_string(),
        scripted.root.to_string_lossy().into_owned(),
    )];
    let supervisor = Supervisor::with_probe(config, scripted.probe()).expect("supervisor");
    let pid = supervisor.start().await.expect("start").pid.expect("pid");
    wait_for_file(&scripted.path("ready")).await;

    let started = Instant::now();
    let stopped = supervisor.stop().await;
    assert!(
        started.elapsed() >= Duration::from_millis(200),
        "SIGKILL must only follow the full grace period"
    );
    assert!(!stopped.running);
    assert!(
        wait_until(Duration::from_secs(2), move || !is_alive(pid)).await,
        "an uncooperative worker must still be gone"
    );

    worker.shutdown();
}

#[tokio::test]
async fn restart_brings_the_worker_back_up() {
    let worker = FakeWorker::start().await;
    let scripted = Scripted::new(
        "echo run >> \"$LATTICEAI_TEST_DIR/runs.txt\"\n\
         while true; do sleep 0.05; done\n",
    );
    let mut config = config(worker.port());
    config.extra_env = vec![(
        "LATTICEAI_TEST_DIR".to_string(),
        scripted.root.to_string_lossy().into_owned(),
    )];
    let supervisor = Supervisor::with_probe(config, scripted.probe()).expect("supervisor");
    let first = supervisor.start().await.expect("start");
    // The health gate says "the worker answers", not "the script reached its
    // first line" — wait for the process itself before replacing it.
    assert!(
        wait_until(Duration::from_secs(5), || scripted.lines("runs.txt") >= 1).await,
        "the first worker never ran"
    );

    let second = supervisor.restart().await.expect("restart");
    assert!(second.running);
    assert!(second.healthy);
    assert_ne!(first.pid, second.pid, "restart means a new process");
    assert!(
        wait_until(Duration::from_secs(5), || scripted.lines("runs.txt") >= 2).await,
        "the replacement worker never ran (runs: {:?})",
        scripted.read("runs.txt")
    );

    supervisor.stop().await;
    worker.shutdown();
}
